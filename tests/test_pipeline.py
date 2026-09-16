"""End-to-end: run the real pipeline over a synthetic grouped batch.

This is the phase-1 validation — it asserts the row layout the whole design rests on:
one row per object at obs_level 0, one row per entity at obs_level 1 named by structure, and
one row per instance, instance-target distance and contact below them.
"""

from pathlib import Path

import polars as pl
import pytest

from organella import pipeline
from organella.pipeline import parts as parts_mod

from conftest import MITO_COLOUR, settings

def test_one_row_per_object_at_obs_level_0(table):
    objects = table.filter(pl.col("obs_level") == 0)

    assert sorted(objects["object_id"].to_list()) == ["object_a", "object_b", "object_c", "object_d"]
    assert objects["n_entities"].to_list() == [3, 3, 3, 3]
    # instance_count rolls up across the object's label entities (mito only here); the
    # mask entities contribute null, not 1.
    assert objects.sort("object_id")["instance_count"].to_list() == [4, 4, 3, 3]


def test_object_row_carries_no_per_entity_measurements(table):
    objects = table.filter(pl.col("obs_level") == 0)

    # Per-entity values must not leak onto the object row even when only one entity
    # produced them — otherwise a one-label-entity object looks different in kind from
    # a two-label-entity one.
    for col in ("entity_name", "entity_kind", "volume_um3", "sphericity"):
        assert objects[col].null_count() == objects.height


def test_one_row_per_entity_at_obs_level_1(table):
    entities = table.filter(pl.col("obs_level") == 1)

    assert entities.height == 4 * 3
    per_object = entities.group_by("object_id").agg(pl.col("entity_name").sort())
    for names in per_object["entity_name"].to_list():
        assert names == ["mito", "nucleus", "pm"]


def test_entity_rows_say_which_structure_they_are_about(table):
    row = table.filter((pl.col("obs_level") == 1) & (pl.col("object_id") == "object_a"))

    assert sorted(row["entity_name"].to_list()) == ["mito", "nucleus", "pm"]
    assert dict(row.select("entity_name", "entity_kind").iter_rows()) == {
        "pm": "mask", "mito": "label", "nucleus": "mask"}


def test_the_report_has_no_dimension_to_slice_by(table):
    """A dim_* column puts a "slice by that dimension" control in the viewer's sidebar.

    An object stacks its structures along C, so such a control would offer a five-structure
    cell as five channels to filter by. Structures are chosen by name, in the widgets that
    are about structures, and the report carries no dim_* column at all.
    """
    assert not [c for c in table.columns if c.startswith("dim_")]
    assert "size_C" not in table.columns          # the entity count; n_entities says it
    assert "n_entities" in table.columns


def test_the_discovered_unit_is_the_object_folder(table):
    # 4 objects × (1 object row + 3 entity rows), plus the rows below them. The TIFFs inside
    # a claimed folder are never records of their own, so nothing has to be skipped.
    assert table.filter(pl.col("obs_level") <= 1).height == 4 * 4
    assert table["object_id"].unique().sort().to_list() == [
        "object_a", "object_b", "object_c", "object_d"]
    # The group is on the object row; the rows below it are identified by object_id alone.
    objects = table.filter(pl.col("obs_level") == 0)
    assert objects["imported_path_short"].unique().sort().to_list() == ["control", "treated"]
    # And its size describes the volumes it was built from, not the 4 KB inode of the
    # directory holding them.
    assert table["size_bytes"].min() > 100_000


def test_group_comes_from_the_import_path(table):
    groups = dict(
        table.filter(pl.col("obs_level") == 0)
        .select("object_id", "imported_path_short")
        .iter_rows()
    )

    assert groups == {
        "object_a": "control", "object_b": "control",
        "object_c": "treated", "object_d": "treated",
    }


def _instances(table) -> pl.DataFrame:
    """The instance rows, with the group of the object each belongs to joined back on.

    The group lives on the object row - an instance row carries only object_id and what was
    measured - which is the join every widget makes for the same reason.
    """
    groups = (table.filter(pl.col("obs_level") == 0)
              .select("object_id", "imported_path_short"))
    return table.filter(pl.col("row_type") == "instance").drop(
        "imported_path_short").join(groups, on="object_id", how="left")


def test_there_is_one_row_per_instance(table):
    instances = _instances(table)

    # 4 + 4 + 3 + 3 mitochondria, one row each.
    assert instances.height == 14
    assert instances["instance_entity"].unique().to_list() == ["mito"]
    assert instances["instance_volume_um3"].min() > 0
    per_object = instances.group_by("object_id").len().sort("object_id")
    assert per_object["len"].to_list() == [4, 4, 3, 3]


def test_instance_count_on_the_object_row_matches_the_instance_rows(table):
    objects = table.filter(pl.col("obs_level") == 0).sort("object_id")
    counted = _instances(table).group_by("object_id").len().sort("object_id")

    # instance_count comes from the per-entity rows, the rows from a different processor:
    # two of them, one answer.
    assert objects["instance_count"].to_list() == counted["len"].to_list()


def test_treated_objects_have_larger_mitochondria(table):
    """The comparison the viewer's significance brackets will be drawn on."""
    by_group = (
        _instances(table)
        .group_by("imported_path_short")
        .agg(pl.col("instance_volume_um3").mean())
    )
    means = dict(by_group.iter_rows())

    assert means["treated"] > means["control"]


def test_instances_carry_distances_to_the_other_entities(table):
    distances = table.filter(pl.col("row_type") == "distance")

    # Every mitochondrion is measured to the two entities that are not its own.
    assert sorted(distances["distance_target"].unique().to_list()) == ["nucleus", "pm"]
    assert distances.height == 14 * 2
    # 0 is a real reading, not a missing one: the synthetic mitochondria ring overlaps
    # the nucleus, and an instance inside its target is zero away from it.
    assert distances["distance_um"].min() == 0.0
    assert distances["distance_um"].max() > 0
    assert distances["distance_um"].is_finite().all()


def test_each_contact_is_a_row_counted_on_its_object(table):
    objects = table.filter(pl.col("obs_level") == 0)
    contacts = table.filter(pl.col("row_type") == "contact")

    # The synthetic mitochondria sit on a ring around the nucleus, so every object has
    # neighbouring pairs, and the count on the object row is how many rows it has.
    assert objects["contact_count"].min() > 0
    counted = contacts.group_by("object_id").len()
    assert dict(objects.select("object_id", "contact_count").iter_rows()) == dict(
        counted.iter_rows())
    assert (contacts["contact_gap_um"] <= 0.5).all()
    # A pair is two instances of one structure, so a mask - the membrane, the nucleus - is
    # never in one: how close an instance gets to those is a distance.
    assert set(contacts["contact_entity"]) == {"mito"}
    assert (contacts["contact_label_a"] != contacts["contact_label_b"]).all()


def test_contacts_are_absent_from_the_entity_rows(table):
    entities = table.filter(pl.col("obs_level") == 1)

    # A pair belongs to no single entity, so nothing about it lands on an entity row.
    assert entities["contact_count"].null_count() == entities.height
    assert entities["contact_gap_um"].null_count() == entities.height


def test_a_structure_carries_the_colour_the_settings_file_gave_it(table):
    """The palette rides in the report, so a shared report arrives already coloured.

    Named structures carry their colour; the rest are null and every widget falls back to its
    own palette, which is what lets one settings file cover a whole project.
    """
    entities = table.filter(pl.col("obs_level") == 1)
    by_name = dict(zip(entities["entity_name"], entities["entity_colour"]))

    assert by_name["mito"] == MITO_COLOUR
    assert by_name["pm"] is None and by_name["nucleus"] is None


def test_voxel_size_lands_in_the_standard_pixel_size_columns(table):
    objects = table.filter(pl.col("obs_level") == 0)

    assert objects["pixel_size_Z"].to_list() == pytest.approx([0.1] * 4)
    assert objects["pixel_size_X"].to_list() == pytest.approx([0.02] * 4)
    assert objects["voxel_size_source"].unique().to_list() == ["tiff-metadata"]


# ── surviving a worker that is killed rather than raising ────────────────────
#
# The OOM killer sends SIGKILL, so the worker reports nothing and the pool breaks. What must
# not happen is what did happen on a real 7-object run: every finished object thrown away
# because one of them died after two hours.

def _fake_dispatch(killed_by_worker_count):
    """A dispatch that kills the object named 'bad' unless few enough workers are running."""
    def dispatch(work, excluded, n_workers, on_result=None, config=None):
        finished, unrun = [], []
        for folder, group in work:
            if folder.name == "bad" and n_workers >= killed_by_worker_count:
                unrun.append((folder, group))
            else:
                result = pipeline.ObjectResult(folder.name, object_row={"n": folder.name})
                finished.append(result)
                if on_result is not None:
                    on_result(result)
        return finished, unrun
    return dispatch


def _work(*names):
    return [(Path(f"/nowhere/{n}"), "") for n in names]


def test_a_killed_worker_does_not_lose_the_objects_that_finished():
    results = pipeline.measure_every_object(_work("a", "bad", "b"), (), 4,
                                    dispatch=_fake_dispatch(killed_by_worker_count=2))
    measured = {r.object_id for r in results if r.error is None}
    assert {"a", "b"} <= measured, "objects that finished were thrown away"


def test_the_killed_object_is_retried_at_lower_concurrency():
    # 'bad' survives once the pool is down to a single worker, which is the usual cause:
    # too many large objects resident at once.
    results = pipeline.measure_every_object(_work("a", "bad", "b"), (), 4,
                                    dispatch=_fake_dispatch(killed_by_worker_count=2))
    assert {r.object_id for r in results} == {"a", "bad", "b"}
    assert all(r.error is None for r in results)


def test_an_object_that_always_kills_its_worker_is_reported_not_retried_forever():
    results = pipeline.measure_every_object(_work("a", "bad", "b"), (), 4,
                                    dispatch=_fake_dispatch(killed_by_worker_count=1))
    by_id = {r.object_id: r for r in results}
    assert set(by_id) == {"a", "bad", "b"}
    assert by_id["bad"].error and "out of memory" in by_id["bad"].error
    assert by_id["a"].error is None and by_id["b"].error is None


# ── --resume ─────────────────────────────────────────────────────────────────
#
# Nothing was written until the whole batch finished, so a run that died late lost every
# object it had already measured. Each object's rows now go to a sidecar as it completes.
# What matters is that resuming from those sidecars gives the same report as measuring.

def _analysed(root, parts, resume, config=None):
    from organella.cli import find_object_dirs
    return pipeline.analyse(find_object_dirs(root), root, ["control", "treated"],
                            workers=1, parts_dir=parts, resume=resume,
                            config=config or settings())


def test_a_resumed_report_is_the_report_it_would_have_measured(tmp_path):
    from synthetic import make_dataset
    root = make_dataset(tmp_path / "objects")
    parts = tmp_path / "parts"

    fresh = _analysed(root, parts, resume=False)
    assert not fresh.failures, fresh.failures
    assert list(parts.glob("*.parquet")), "no per-object rows were kept"

    resumed = _analysed(root, parts, resume=True)
    assert not resumed.failures, resumed.failures
    assert resumed.rows.shape == fresh.rows.shape
    # Row order follows completion, so compare on the sorted frames.
    keys = ["obs_level", "object_id", "entity_name"]
    by = [k for k in keys if k in fresh.rows.columns]
    assert resumed.rows.sort(by).equals(fresh.rows.sort(by)), \
        "resuming from the sidecars changed the report"


def test_an_unreadable_part_is_measured_again_rather_than_trusted(tmp_path):
    from synthetic import make_dataset
    root = make_dataset(tmp_path / "objects")
    parts = tmp_path / "parts"
    fresh = _analysed(root, parts, resume=False)

    # What a run killed mid-write leaves behind.
    victim = sorted(parts.glob("*.parquet"))[0]
    whole = victim.read_bytes()
    victim.write_bytes(whole[: len(whole) // 2])

    resumed = _analysed(root, parts, resume=True)
    assert not resumed.failures, resumed.failures
    assert resumed.rows.shape == fresh.rows.shape, "a truncated part cost an object its rows"


def test_resume_will_not_reuse_rows_measured_under_other_settings(tmp_path, monkeypatch):
    """The trap this closes: adding --auto-clip and keeping --resume.

    Both caches used to key on the object's name alone, so changing what a run measures and
    resuming would hand back the old answer with nothing to say it no longer matched.
    """
    from synthetic import make_dataset
    root = make_dataset(tmp_path / "objects")
    parts = tmp_path / "parts"

    fresh = _analysed(root, parts, resume=False)
    assert not fresh.failures, fresh.failures
    assert list(parts.glob("*.parquet"))

    # Same objects, different question: measure outside the object mask too.
    resumed = _analysed(root, parts, resume=True, config=settings(clip=False))

    assert not resumed.failures, resumed.failures
    assert resumed.rows.shape[0] == fresh.rows.shape[0]


def test_a_part_from_other_settings_is_refused_outright(tmp_path):
    """The check itself, without relying on the measurements happening to differ."""
    result = pipeline.ObjectResult("cell_a", object_row={"obs_level": 0, "object_id": "cell_a"})
    parts_mod.write_part(tmp_path, result, "settings-as-measured")
    path = parts_mod.part_path(tmp_path, "cell_a")

    assert parts_mod.read_part(path, "settings-as-measured") is not None
    assert parts_mod.read_part(path, "settings-changed-since") is None


def test_a_part_that_records_no_settings_at_all_is_refused(tmp_path):
    """A part from before the settings were recorded says nothing about what it measured."""
    result = pipeline.ObjectResult("cell_a", object_row={"obs_level": 0, "object_id": "cell_a"})
    parts_mod.write_part(tmp_path, result, "settings-as-measured")
    path = parts_mod.part_path(tmp_path, "cell_a")
    pl.read_parquet(path).drop(parts_mod.FINGERPRINT_COLUMN).write_parquet(path)

    assert parts_mod.read_part(path, "settings-as-measured") is None


def test_a_part_carries_no_bookkeeping_into_the_report(tmp_path):
    result = pipeline.ObjectResult("cell_a", object_row={"obs_level": 0, "object_id": "cell_a"})
    parts_mod.write_part(tmp_path, result, "fp")
    rows = parts_mod.read_part(parts_mod.part_path(tmp_path, "cell_a"), "fp")
    assert rows is not None and rows.height
    assert parts_mod.FINGERPRINT_COLUMN not in rows.columns, \
        "the fingerprint column must not reach the report"


def test_the_fingerprint_ignores_settings_that_change_nothing(tmp_path):
    from organella import pipeline as pl_mod

    before = pl_mod.settings_fingerprint((), settings())
    # How fast, not what: a part measured with a different mesh pool is still the same rows.
    assert pl_mod.settings_fingerprint((), settings(mesh_workers=3)) == before
    # ...but excluding a processor changes which columns exist, so it must count.
    assert pl_mod.settings_fingerprint(("organella-contacts",), settings()) != before


# ── saying what it is doing while it does it ─────────────────────────────────
#
# A real batch takes hours. Collected first and logged at the end, that is hours of silence
# from a run that is working perfectly - which looks exactly like one that has hung.

def test_every_object_is_counted_off_against_the_size_of_the_batch(tmp_path, caplog):
    import logging
    import re

    from organella.measure import find_object_dirs
    from synthetic import make_dataset

    root = make_dataset(tmp_path / "objects")
    folders = list(find_object_dirs(root))

    with caplog.at_level(logging.INFO, logger="organella"):
        pipeline.analyse(folders, root, paths=("control", "treated"), config=settings())

    counted = [re.search(r"\[(\d+)/(\d+)\] (\S+) done", m) for m in caplog.messages]
    counted = [m for m in counted if m]

    # One line per object, counting up to the size of the batch and naming which one landed.
    assert [int(m.group(1)) for m in counted] == [1, 2, 3, 4]
    assert {m.group(2) for m in counted} == {"4"}
    assert {m.group(3) for m in counted} == {f.name for f in folders}


def test_a_worker_takes_the_level_the_parent_set(monkeypatch):
    """A spawned worker never runs the CLI, so its own lines had nowhere to go.

    Which object is being read, how big it is and how many instances came out are all
    logged in the worker: without this they were formatted and dropped, and the run said
    nothing at all until the first object finished.
    """
    import logging

    from organella.pipeline import pool

    package = logging.getLogger("organella")
    before = (package.handlers[:], package.level, package.propagate)
    try:
        package.handlers[:] = []
        pool.log_in_this_worker(None)
        assert package.handlers == [], "nothing to pass on means nothing is installed"

        pool.log_in_this_worker(logging.INFO)

        assert len(package.handlers) == 1
        assert package.level == logging.INFO
        assert package.propagate is False

        # And what the pool passes on is what the parent set, read off the logger the
        # parent configured rather than out of its surroundings.
        assert pool._level_to_pass_on() == logging.INFO
        package.setLevel(logging.NOTSET)
        assert pool._level_to_pass_on() is None
    finally:
        package.handlers[:], package.level, package.propagate = before
