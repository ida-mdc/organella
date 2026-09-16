import json
import os
import re
import threading
import time
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from organella import report_io
from organella.cli import (
    FLAVOR,
    _apply_analysis_env,
    cli,
    estimate_peak_gb,
    find_object_dirs,
)
from organella.config import RunConfig
from synthetic import make_object, make_dataset


@pytest.fixture
def dataset(tmp_path):
    return make_dataset(tmp_path / "experiment")


def test_finds_every_object_folder_but_not_the_folders_holding_them(dataset):
    found = {p.name for p in find_object_dirs(dataset)}

    assert found == {"object_a", "object_b", "object_c", "object_d"}


def test_a_single_object_folder_is_its_own_dataset(tmp_path):
    make_object(tmp_path / "object_a", prefix="sample_a")

    assert find_object_dirs(tmp_path / "object_a") == [tmp_path / "object_a"]


def test_peak_memory_estimate_scales_with_the_object(tmp_path):
    make_object(tmp_path / "small", prefix="s")           # 3 entities, 20×40×40
    big = tmp_path / "big"
    make_object(big, prefix="b")

    # Same shape here, so the estimate is about entity count and voxels, not file size.
    assert estimate_peak_gb(tmp_path / "small") == pytest.approx(estimate_peak_gb(big))
    assert 0 < estimate_peak_gb(tmp_path / "small") < 0.01


def test_a_folder_with_no_source_estimates_nothing(tmp_path):
    (tmp_path / "empty").mkdir()

    assert estimate_peak_gb(tmp_path / "empty") == 0.0


def test_analysis_flags_travel_as_environment_variables(monkeypatch):
    # Writes land in a throwaway copy: a leaked ORGANELLA_* here would silently
    # reconfigure every later test, since that is exactly how plugins read their options.
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORGANELLA_")}
    monkeypatch.setattr(os, "environ", env)

    _apply_analysis_env("pm", "cell", "0.5,0.1,0.1", True, False, 0.25, None, None,
                        baseline_exclude="nucleus")

    assert env["ORGANELLA_OBJECT_MASK"] == "pm"
    assert env["ORGANELLA_OBJECT_NOUN"] == "cell"
    assert env["ORGANELLA_VOXEL_SIZE_UM"] == "0.5,0.1,0.1"
    assert env["ORGANELLA_NO_CLIP"] == "1"
    assert env["ORGANELLA_CONTACT_MAX_UM"] == "0.25"
    assert env["ORGANELLA_BASELINE_EXCLUDE"] == "nucleus"
    # Flags left alone must not be forced to a default here - config.py owns those.
    assert "ORGANELLA_AUTO_LABEL_MASKS" not in env
    assert "ORGANELLA_MAX_SKELETON_VOXELS" not in env


def test_a_colour_settings_file_is_read_and_expanded(tmp_path, monkeypatch):
    """One file per study, hand-edited: short hex and any case have to work."""
    settings = tmp_path / "colours.json"
    settings.write_text(json.dumps({"mito": "#D62728", "er": "#2c3"}))
    monkeypatch.setenv("ORGANELLA_ENTITY_COLOURS", str(settings))

    assert RunConfig.from_env().entity_colours == {"mito": "#d62728", "er": "#22cc33"}


@pytest.mark.parametrize("contents,complaint", [
    ('{"mito": "red"}', "hex colour"),
    ('{"mito": 16711680}', "hex colour"),
    ('["mito"]', "structure: colour pairs"),
    ('{"mito": ', "not valid JSON"),
])
def test_a_broken_colour_file_says_what_is_wrong(tmp_path, monkeypatch, contents, complaint):
    settings = tmp_path / "colours.json"
    settings.write_text(contents)
    monkeypatch.setenv("ORGANELLA_ENTITY_COLOURS", str(settings))

    with pytest.raises(ValueError, match=complaint):
        RunConfig.from_env()


def test_a_missing_colour_file_is_an_error_not_a_default(tmp_path, monkeypatch):
    """Silently ignoring it would produce a report coloured nothing like the study asked."""
    monkeypatch.setenv("ORGANELLA_ENTITY_COLOURS", str(tmp_path / "nope.json"))

    with pytest.raises(ValueError, match="no such file"):
        RunConfig.from_env()


def test_a_report_records_the_version_that_measured_it(report_path):
    """The footer's version is what says which code produced a file somebody was sent.

    There were two versions to bump - pyproject's and organella.__version__ - and only one
    of them was, so 0.2.0 reports went out stamped 0.1.0. pyproject reads the module's now,
    which is what this pins: a bump in one place cannot leave the other behind.
    """
    import tomllib

    import organella

    _, footer = report_io.read(report_path)
    assert footer["organella_version"] == organella.__version__

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert "version" not in pyproject["project"], (
        "pyproject carries a second version; it should read organella.__version__")
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/organella/__init__.py"


def test_colouring_a_report_keeps_everything_else_about_it(tmp_path, report_path):
    """Colours are presentation, and a run takes minutes: changing them re-reads no pixels.

    The report is rewritten, so the footer the viewer's provenance strip reads has to survive,
    and so do the rows.
    """
    before = pq.read_table(report_path)
    palette = tmp_path / "palette.json"
    palette.write_text(json.dumps({"nucleus": "#9467bd"}))
    coloured = tmp_path / "coloured.parquet"
    coloured.write_bytes(Path(report_path).read_bytes())

    result = CliRunner().invoke(cli, ["colours", str(coloured), str(palette)])

    assert result.exit_code == 0, result.output
    after = pq.read_table(coloured)
    assert after.num_rows == before.num_rows
    assert (after.schema.metadata[b"organella_flavour"]
            == before.schema.metadata[b"organella_flavour"])
    assert (after.schema.metadata[b"organella_paths"]
            == before.schema.metadata[b"organella_paths"])
    entities = pl.from_arrow(after).filter(pl.col("obs_level") == 1)
    by_name = dict(zip(entities["entity_name"], entities["entity_colour"]))
    # The new palette replaces the old one outright: it is the whole answer, not an addition.
    assert by_name["nucleus"] == "#9467bd"
    assert by_name["mito"] is None


def test_describing_a_report_keeps_everything_else_about_it(tmp_path, report_path):
    """Credits are the thing nobody has ready when a 30-minute run starts."""
    before = pq.read_table(report_path)
    described = tmp_path / "described.parquet"
    described.write_bytes(Path(report_path).read_bytes())

    result = CliRunner().invoke(cli, ["describe", str(described), "Credit: someone."])

    assert result.exit_code == 0, result.output
    after = pq.read_table(described)
    assert after.num_rows == before.num_rows
    assert after.schema.metadata[b"organella_description"] == b"Credit: someone."
    assert (after.schema.metadata[b"organella_processing_stats"]
            == before.schema.metadata[b"organella_processing_stats"])


def test_a_description_can_come_from_standard_input(tmp_path, report_path):
    """A citation is longer than a shell line, and `-` is how the shell hands one over."""
    described = tmp_path / "described.parquet"
    described.write_bytes(Path(report_path).read_bytes())
    credits = "Alpha cells, Schmidt et al. 2026.\nCC BY 4.0.\n"

    result = CliRunner().invoke(cli, ["describe", str(described), "-"], input=credits)

    assert result.exit_code == 0, result.output
    written = pq.read_table(described).schema.metadata[b"organella_description"].decode()
    assert written == credits.strip()


def test_colouring_a_report_from_a_broken_palette_fails_before_writing(tmp_path, report_path):
    palette = tmp_path / "palette.json"
    palette.write_text('{"mito": "crimson"}')
    coloured = tmp_path / "untouched.parquet"
    coloured.write_bytes(Path(report_path).read_bytes())
    before = coloured.read_bytes()

    result = CliRunner().invoke(cli, ["colours", str(coloured), str(palette)])

    assert result.exit_code != 0 and "hex colour" in result.output
    assert coloured.read_bytes() == before


def test_dry_run_lists_the_objects_and_their_entities(dataset):
    result = CliRunner().invoke(cli, ["dry-run", str(dataset)])

    assert result.exit_code == 0
    assert "control/object_a" in result.output
    assert "labels  mito" in result.output
    assert "masks   nucleus, pm" in result.output
    assert "label:mito               4/4" in result.output


def test_dry_run_without_a_mask_says_which_masks_to_choose_from(dataset):
    """This is the survey you run first: nothing is guessed, so it has to offer the names."""
    result = CliRunner().invoke(cli, ["dry-run", str(dataset)])

    assert result.exit_code == 0
    assert "pass it as --object-mask: nucleus, pm" in result.output
    # And no mask is marked as the boundary, because none was chosen.
    assert "pm*" not in result.output


def test_dry_run_marks_and_checks_the_mask_it_is_given(dataset):
    result = CliRunner().invoke(cli, ["dry-run", str(dataset), "--object-mask", "pm"])

    assert result.exit_code == 0
    assert "masks   nucleus, pm*" in result.output      # * marks the object mask
    assert "--object-mask" not in result.output.split("=====")[-1]


def test_dry_run_reports_a_folder_missing_the_mask_it_was_given(dataset):
    (dataset / "control" / "object_a" / "sample_a_pm_mask.tif").unlink()

    result = CliRunner().invoke(cli, ["dry-run", str(dataset), "--object-mask", "pm"])

    # Naming a mask a folder does not have is the error: measuring against a different
    # boundary would put every distance in that object on a different origin.
    assert result.exit_code == 1
    assert "No mask named 'pm'" in result.output


def test_dry_run_says_so_when_nothing_looks_like_an_object(tmp_path):
    result = CliRunner().invoke(cli, ["dry-run", str(tmp_path)])

    assert result.exit_code == 1
    assert "No object folders found" in result.output


def test_process_writes_a_report_without_being_told_how_to_slice(dataset, tmp_path):
    out = tmp_path / "report.parquet"
    result = CliRunner().invoke(
        cli, ["process", str(dataset), "-o", str(out), "--object-mask", "pm",
              "-p", "control", "-p", "treated"]
    )

    assert result.exit_code == 0, result.output
    table = pl.read_parquet(out)
    # The entity rows only exist if slice_size was set for us.
    assert table.filter(pl.col("obs_level") == 1).height == 12
    assert table.filter(pl.col("obs_level") == 0)["instance_count"].sum() == 14


def test_the_report_says_what_kind_of_analysis_it_is(dataset, tmp_path):
    out = tmp_path / "report.parquet"
    result = CliRunner().invoke(cli, ["process", str(dataset), "-o", str(out), "--object-mask", "pm"])

    assert result.exit_code == 0, result.output
    # The viewer shows the flavour as a chip beside the title, so a report is recognisable
    # as this analysis before a widget is read.
    metadata = pq.read_metadata(out).metadata
    assert metadata[b"organella_flavour"].decode() == FLAVOR == "organella"


def test_process_can_skip_the_expensive_processors(dataset, tmp_path):
    out = tmp_path / "lean.parquet"
    result = CliRunner().invoke(
        cli, ["process", str(dataset), "-o", str(out), "--object-mask", "pm",
              "--no-instances", "--no-contacts"]
    )

    assert result.exit_code == 0, result.output
    columns = pl.read_parquet(out).columns
    assert "instance_entity" not in columns
    assert "contact_count" not in columns
    assert "entity_name" in columns          # per-entity morphology still runs


def test_process_refuses_a_directory_with_no_objects(tmp_path):
    result = CliRunner().invoke(
        cli, ["process", str(tmp_path), "-o", str(tmp_path / "x.parquet"),
              "--object-mask", "pm"]
    )

    assert result.exit_code != 0
    assert "No object folders found" in result.output
    assert "dry-run" in result.output        # points at the command that explains why


# ── view, and the page it opens ──────────────────────────────────────────────
#
# The page is a standalone HTML file and needs no server: dropping a report onto it is the
# whole workflow. What `view` adds is the geometry, which sits beside the report as its own
# parquet per object and which a page opened from a file:// URL cannot reach.

def test_page_prints_a_file_that_is_there():
    from click.testing import CliRunner
    from organella import cli as cli_mod

    result = CliRunner().invoke(cli_mod.cli, ["page"])

    assert result.exit_code == 0, result.output
    printed = Path(result.output.strip())
    assert printed.is_file()
    assert printed.suffix == ".html"


def test_the_page_is_self_contained():
    """It is copied around and opened from disk, so it may reference nothing beside it."""
    from organella.report_page import report_page

    source = report_page().read_text()

    # Every script, stylesheet and image is inline, a data: URL or an absolute one; a
    # relative one would 404 the moment the file is copied somewhere else.
    allowed = ("http", "data:", "#", "mailto:")
    for match in re.finditer(r'(?:src|href)="([^"]+)"', source):
        url = match.group(1)
        assert url.startswith(allowed), url


def test_view_serves_the_report_and_points_the_page_at_it(tmp_path, report_path):
    """One origin for both, because the page reads the geometry over HTTP."""
    import shutil
    import urllib.request

    from organella import report_page as page_mod

    served = tmp_path / "report.parquet"
    shutil.copy(report_path, served)
    port = page_mod.free_port()

    thread = threading.Thread(
        target=page_mod.serve, args=(served,),
        kwargs={"port": port, "open_browser": False}, daemon=True)
    thread.start()
    url = page_mod.open_url(served, port)
    for _ in range(100):                       # the server needs a moment to bind
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/report.parquet").read(4)
            break
        except OSError:
            time.sleep(0.05)

    # The page, the report, and the geometry, all reachable from where the page is opened.
    assert "data=report.parquet" in url
    page = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/{page_mod.PAGE_FILENAME}").read().decode()
    assert "Organella Report" in page
    assert urllib.request.urlopen(
        f"http://127.0.0.1:{port}/report.parquet").read(4) == b"PAR1"


def test_nothing_served_may_be_cached(tmp_path, report_path):
    """The same URL serves every run's copy of a file, so a kept one is a wrong one.

    A geometry file is /__geometry/<object_id>/geometry.parquet whichever run wrote it, and
    `view` always serves on the same port. A browser that answers one object out of its
    cache hands DuckDB a glob of two schemas, and the reader is told "schema mismatch in
    glob" about files that are all correct on disk.
    """
    import shutil
    import urllib.request

    from organella import report_page as page_mod

    served = tmp_path / "report.parquet"
    shutil.copy(report_path, served)
    port = page_mod.free_port()
    thread = threading.Thread(
        target=page_mod.serve, args=(served,),
        kwargs={"port": port, "open_browser": False}, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/report.parquet").read(4)
            break
        except OSError:
            time.sleep(0.05)

    for path in (page_mod.PAGE_FILENAME, "report.parquet"):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}") as answer:
            assert answer.headers["Cache-Control"] == "no-store", path


def test_a_report_with_geometry_is_opened_with_its_geometry_attached(report_path):
    from organella import report_page as page_mod

    # This report was written without --with-mesh, so there is nothing to attach and the
    # page is not told to look for any: an empty 3D section says so itself.
    assert page_mod.geometry_root(report_path) is None
    assert "geometry=" not in page_mod.open_url(report_path, 8052)


def test_the_geometry_root_is_read_off_the_report(tmp_path):
    """--mesh-dir can put it anywhere, so where it went is read rather than guessed."""
    import polars as pl

    from organella import report_page as page_mod

    geometry = tmp_path / "somewhere_else" / "object_a"
    geometry.mkdir(parents=True)
    (geometry / "geometry.parquet").write_bytes(b"")
    report = tmp_path / "report.parquet"
    pl.DataFrame({"object_id": ["object_a"],
                  "mesh_geometry_file": [str(geometry / "geometry.parquet")]}
                 ).write_parquet(report)

    assert page_mod.geometry_root(report) == geometry.parent
    assert "geometry=__geometry" in page_mod.open_url(report, 8052)


def test_a_geometry_request_cannot_walk_out_of_the_geometry_root(tmp_path):
    from organella.report_page import GEOMETRY_MOUNT, _Handler

    _Handler.geometry = tmp_path / "geometry"
    _Handler.page = tmp_path / "page.html"
    resolved = _Handler.translate_path(
        _Handler, f"/{GEOMETRY_MOUNT}/../../etc/passwd")

    assert Path(resolved) == tmp_path / "geometry" / "etc" / "passwd"
