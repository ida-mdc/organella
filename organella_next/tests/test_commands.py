import json

import polars as pl
import pytest
from click.testing import CliRunner

from organella.cli import main
from organella.report.file import read_report


def run(*args):
    return CliRunner().invoke(main, [str(a) for a in args], catch_exceptions=False)


@pytest.fixture(scope="module")
def processed(object_folder, tmp_path_factory):
    out = tmp_path_factory.mktemp("cmd") / "report.parquet"
    result = run("process", object_folder.parent, "-o", out, "--object-mask", "cell", "--reach-um", "0.4", "--object-noun", "cell",
                 "--skeletons", "mito", "--with-mesh", "--geometry-as", "mito=ellipsoid")
    assert result.exit_code == 0, result.output
    return out


def test_process_writes_the_report_and_with_mesh_the_geometry_beside_it(processed):
    rows, footer = read_report(processed)
    assert footer["organella_object_noun"] == "cell" and rows.filter(pl.col("row_type") == "object").height == 1
    object_row = rows.filter(pl.col("row_type") == "object").row(0, named=True)
    geometry = processed.with_name("report_meshes") / "cell_a" / "geometry.parquet"
    assert geometry.is_file() and object_row["mesh_geometry_file"].endswith("report_meshes/cell_a/geometry.parquet")
    assert pl.read_parquet(geometry).filter(pl.col("row_type") == "instance")["surface_kind"].unique().to_list() == ["ellipsoid"]


def test_mesh_writes_the_geometry_alone(object_folder, tmp_path):
    result = run("mesh", object_folder.parent, "-o", tmp_path / "g", "--object-mask", "cell", "--no-contacts")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "g" / "cell_a" / "geometry.parquet").is_file() and not list(tmp_path.glob("*.parquet"))


def test_process_exits_non_zero_and_says_which_object_failed(tmp_path, object_folder):
    broken = tmp_path / "batch" / "x_broken"
    broken.mkdir(parents=True)
    import numpy as np, tifffile
    tifffile.imwrite(broken / "x_cell_mask.tif", np.ones((4, 4, 4), np.uint8))
    result = CliRunner().invoke(main, ["process", str(broken.parent), "-o", str(tmp_path / "r.parquet")])
    assert result.exit_code == 1 and "failed x_broken" in result.output


def test_page_writes_the_standalone_page_and_prints_where(tmp_path):
    result = run("page", tmp_path / "p.html")
    assert result.exit_code == 0 and (tmp_path / "p.html").read_text().startswith("<!DOCTYPE html>")
    assert result.output.strip() == str((tmp_path / "p.html").resolve())


def test_colours_and_describe_amend_a_report_without_measuring(processed, tmp_path):
    copy = tmp_path / "copy.parquet"
    copy.write_bytes(processed.read_bytes())
    palette = tmp_path / "palette.json"
    palette.write_text(json.dumps({"mito": "#d62728"}))
    assert "1 structure(s) coloured" in run("colours", copy, palette).output
    assert "description written" in run("describe", copy, "Credits.").output
    rows, footer = read_report(copy)
    assert footer["organella_description"] == "Credits." and rows.filter(pl.col("entity_name") == "mito")["entity_colour"][0] == "#d62728"


def test_a_geometry_choice_that_is_not_one_is_refused_with_the_kinds_there_are(object_folder, tmp_path):
    result = CliRunner().invoke(main, ["process", str(object_folder.parent), "-o", str(tmp_path / "r.parquet"), "--geometry-as", "mito=blob"])
    assert result.exit_code != 0 and "no kind called 'blob'" in result.output


def test_dry_run_lists_what_would_be_analysed_from_headers_and_exits_zero(object_folder):
    result = run("dry-run", object_folder.parent)
    assert result.exit_code == 0
    for text in ("cell_a", "sample.tif", "labels  mito", "masks   cell, wall", "mask:cell", "1/1", "Pick the mask that bounds each object"):
        assert text in result.output, text
    assert "cell*" in run("dry-run", object_folder.parent, "--object-mask", "cell").output


def test_dry_run_exits_one_for_an_object_that_cannot_be_analysed_and_says_why(tmp_path, object_folder):
    import numpy as np, tifffile
    odd = tmp_path / "batch" / "odd"
    odd.mkdir(parents=True)
    tifffile.imwrite(odd / "o_a_mask.tif", np.ones((4, 4, 4), np.uint8))
    tifffile.imwrite(odd / "o_b_label.tif", np.ones((5, 4, 4), np.uint8))
    result = CliRunner().invoke(main, ["dry-run", str(odd.parent), "--object-mask", "zzz", "--voxel-size-um", "0.1,0.1,0.1"])
    assert result.exit_code == 1 and "structures differ in shape" in result.output and "no object mask named 'zzz'" in result.output
    assert "2 problem(s) found" in result.output


def test_dry_run_of_an_empty_folder_exits_one(tmp_path):
    (tmp_path / "nothing").mkdir()
    result = CliRunner().invoke(main, ["dry-run", str(tmp_path / "nothing")])
    assert result.exit_code == 1 and "No object folders found" in result.output


def test_threads_caps_the_workers_dask_uses_for_the_run(object_folder, tmp_path, monkeypatch):
    import dask
    seen = []
    real = dask.config.set
    monkeypatch.setattr("organella.cli.process.dask.config.set", lambda *a, **k: (seen.append(k), real(*a, **k))[1])
    assert run("process", object_folder.parent, "-o", tmp_path / "t.parquet", "--object-mask", "cell", "--threads", "2", "--reach-um", "0.4").exit_code == 0
    assert {"num_workers": 2} in seen
