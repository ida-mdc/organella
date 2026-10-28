"""An object measured a window at a time gives the report of the object measured whole.

With a coarse view that is the whole object the two are held equal, row for row. With a coarser one the rows that
come from windows stay exact and the ones that come from the view are the view's.
"""

import json
import math
from dataclasses import replace

import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pytest

from organella.config import RunConfig
from organella.pipeline.objects import measure_object

pytest.importorskip("zarr")
from test_windowed_source import _object  # noqa: E402  (the same fixture: a cell, mitochondria as instances, an ER mask)

# Past anything in the fixture, so every distance is exact.
EVERYTHING = dict(window_reach_um=100.0, coarse_voxels=10**9)


def _config(tmp_path, name, **kwargs):
    return RunConfig(object_mask="cell", mesh_dir=str(tmp_path / name), **kwargs)


def _same(a, b, where):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            assert a is b, where
        else:
            assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9) or (math.isnan(a) and math.isnan(b)), (where, a, b)
    else:
        assert a == b, (where, a, b)


def _rows_of(result, row_type, keys):
    frame = result.frame().filter(pl.col("row_type") == row_type)
    return {tuple(r[k] for k in keys): r for r in frame.iter_rows(named=True)}


def _geometry(tmp_path, name):
    table = pq.read_table(next((tmp_path / name).rglob("geometry.parquet")))
    return {(r["row_type"], r.get("entity_name"), r.get("label_id"), r.get("entity_a"), r.get("label_a"),
             r.get("label_b")): r for r in table.to_pylist()}


@pytest.fixture
def both(tmp_path):
    folder = _object(tmp_path)
    whole = measure_object(folder, "g", (), _config(tmp_path, "whole"))
    windowed = measure_object(folder, "g", (), _config(tmp_path, "windows", windowed=True, **EVERYTHING))
    assert whole.error is None and windowed.error is None, (whole.error, windowed.error)
    return tmp_path, whole, windowed


def test_the_object_row_is_the_whole_objects_but_for_what_says_it_was_windowed(both):
    _, whole, windowed = both

    assert windowed.object_row["coarse_factor"] == 1
    assert whole.object_row.get("coarse_factor") is None
    for key, value in whole.object_row.items():
        if key in ("coarse_factor", "modification_date", "mesh_geometry_file"):
            continue
        _same(windowed.object_row[key], value, key)


def test_every_structure_has_the_whole_objects_row(both):
    _, whole, windowed = both

    assert [r["entity_name"] for r in windowed.entity_rows] == [r["entity_name"] for r in whole.entity_rows]
    for got, want in zip(windowed.entity_rows, whole.entity_rows):
        assert got.keys() == want.keys()
        for key, value in want.items():
            if key != "modification_date":
                _same(got[key], value, (want["entity_name"], key))


@pytest.mark.parametrize("row_type, keys", [
    ("instance", ["instance_entity", "instance_label"]),
    ("distance", ["distance_entity", "distance_label", "distance_target"]),
    ("baseline", ["baseline_target"]),
    ("contact", ["contact_entity", "contact_label_a", "contact_label_b"]),
])
def test_every_deep_row_is_the_whole_objects(both, row_type, keys):
    _, whole, windowed = both
    want, got = _rows_of(whole, row_type, keys), _rows_of(windowed, row_type, keys)

    assert want, f"the fixture has no {row_type} rows, so the test would pass on nothing"
    assert got.keys() == want.keys()
    for key, row in want.items():
        for column, value in row.items():
            if column in got[key]:
                _same(got[key][column], value, (row_type, key, column))


def test_the_geometry_is_the_whole_objects(both):
    tmp_path, _, _ = both
    want, got = _geometry(tmp_path, "whole"), _geometry(tmp_path, "windows")

    assert want.keys() == got.keys()
    assert {k[0] for k in want} >= {"instance", "file"}
    for key, row in want.items():
        for column, value in row.items():
            if row["surface_kind"] == "ellipsoid" and column == "surface":
                # The principal directions of an ellipsoid are only defined up to their sign.
                assert np.allclose(np.abs(np.frombuffer(got[key][column], "<f4")),
                                   np.abs(np.frombuffer(value, "<f4")), atol=1e-5), key
            elif isinstance(value, (bytes, str)) or value is None:
                assert got[key][column] == value, (key, column)
            else:
                _same(got[key][column], value, (key, column))


# ── what a coarser view changes, and what it does not ───────────────────────────

def test_the_rows_that_come_from_windows_stay_exact_on_a_coarse_view(tmp_path):
    folder = _object(tmp_path)
    whole = measure_object(folder, "g", (), RunConfig(object_mask="cell"))
    coarse = measure_object(folder, "g", (), RunConfig(object_mask="cell", windowed=True, window_reach_um=100.0,
                                                       coarse_voxels=2000))

    assert coarse.error is None, coarse.error
    assert coarse.object_row["coarse_factor"] > 1
    keys = ["instance_entity", "instance_label"]
    want, got = _rows_of(whole, "instance", keys), _rows_of(coarse, "instance", keys)
    assert got.keys() == want.keys()
    for key, row in want.items():
        for column, value in row.items():
            _same(got[key][column], value, (key, column))
    # What is counted at full resolution is, on the structure's row, whatever the view: the instances of a label
    # structure, and the volume of a mask.
    for got_row, want_row in zip(coarse.entity_rows, whole.entity_rows):
        if want_row["entity_kind"] == "label":
            _same(got_row["instance_count"], want_row["instance_count"], "count")
        _same(got_row["total_volume_um3"], want_row["total_volume_um3"], "total volume")


def test_a_distance_past_the_window_is_read_off_the_coarse_view_and_is_not_under_the_reach(tmp_path):
    folder = _object(tmp_path)
    keys = ["distance_entity", "distance_label", "distance_target"]
    config = dict(windowed=True, coarse_voxels=10**9)
    exact = measure_object(folder, "g", (), RunConfig(object_mask="cell", window_reach_um=100.0, **config))
    short = measure_object(folder, "g", (), RunConfig(object_mask="cell", window_reach_um=0.01, **config))

    want, got = _rows_of(exact, "distance", keys), _rows_of(short, "distance", keys)
    assert any(r["distance_um"] > 0.01 for r in want.values()), "nothing is past the reach, so nothing is tested"
    for key, row in want.items():
        # A coarse view that is the whole object reads the distance the window did not see exactly.
        _same(got[key]["distance_um"], row["distance_um"], key)


# ── what a window cannot do is said before anything is read ────────────────────

@pytest.mark.parametrize("settings, message", [
    (dict(skeletons=frozenset({"mito"})), "skeletons"),
    (dict(distance_histograms=True), "distance-histograms"),
])
def test_what_cannot_be_measured_a_window_at_a_time_is_refused_by_name(tmp_path, settings, message):
    result = measure_object(_object(tmp_path), "g", (), RunConfig(object_mask="cell", windowed=True, **settings))

    assert result.error and message in result.error


def test_an_object_with_nothing_bounding_it_is_refused(tmp_path):
    folder = _object(tmp_path)
    manifest = json.loads((folder / "source.json").read_text())
    del manifest["object_mask"]
    (folder / "source.json").write_text(json.dumps(manifest))

    result = measure_object(folder, "g", (), RunConfig(windowed=True))

    assert result.error and "bounds the object" in result.error


def test_a_binary_array_declared_as_labels_is_refused_with_what_to_do(tmp_path):
    folder = _object(tmp_path)
    manifest = json.loads((folder / "source.json").read_text())
    manifest["entities"]["er"]["kind"] = "label"                      # the ER holds one id: 255
    (folder / "source.json").write_text(json.dumps(manifest))

    result = measure_object(folder, "g", (), RunConfig(object_mask="cell", windowed=True))

    assert result.error and "declare it a mask" in result.error
