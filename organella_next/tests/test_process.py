import json

import numpy as np
import polars as pl
import pytest
from scipy import ndimage

from conftest import VOXEL_UM
from organella.app.process import process
from organella.app.settings import Settings
from organella.measure import DEFAULT
from organella.sinks import ParquetSink
from organella.sources import FolderSource


@pytest.fixture(scope="module")
def report(object_folder, tmp_path_factory) -> pl.DataFrame:
    out = tmp_path_factory.mktemp("out") / "report.parquet"
    failures = process(
        FolderSource(object_folder.parent, object_mask="cell"),
        DEFAULT, ParquetSink(out), Settings(reach_um=0.4),
    )
    assert failures == {}
    return pl.read_parquet(out)


def rows(report, row_type):
    return report.filter(pl.col("row_type") == row_type)


def test_voxel_size_is_read_from_the_file(report):
    row = rows(report, "object").row(0, named=True)
    assert (row["pixel_size_z"], row["pixel_size_y"], row["pixel_size_x"]) == pytest.approx(VOXEL_UM)


def test_instance_volumes_match_voxel_counts(report, arrays):
    got = rows(report, "instance").sort("instance_label")
    ids, counts = np.unique(arrays["mito"][arrays["mito"] > 0], return_counts=True)
    assert got["instance_label"].to_list() == ids.tolist()
    assert got["instance_volume_um3"].to_numpy() == pytest.approx(counts * np.prod(VOXEL_UM))


def test_distances_match_an_in_memory_transform_up_to_the_reach(report, arrays):
    reach = 0.4
    exact = ndimage.distance_transform_edt(arrays["wall"] == 0, sampling=VOXEL_UM)
    got = rows(report, "distance").filter(pl.col("distance_target") == "wall").sort("distance_label")
    expected = [min(exact[arrays["mito"] == i].min(), reach) for i in got["distance_label"]]
    assert got["distance_um"].to_numpy() == pytest.approx(expected, abs=1e-5)


def test_distance_past_the_reach_is_flagged(report):
    far = rows(report, "distance").filter(pl.col("distance_um") >= 0.4)
    assert far["distance_beyond_reach"].all()


def test_baseline_covers_the_object_without_the_target_itself(report, arrays):
    baseline = rows(report, "baseline").filter(pl.col("baseline_target") == "wall")
    inside_wall = int(((arrays["wall"] > 0) & (arrays["cell"] > 0)).sum())
    assert baseline["baseline_voxels"][0] == int(arrays["cell"].sum()) - inside_wall
    assert sum(json.loads(baseline["baseline_hist_counts"][0])) == baseline["baseline_voxels"][0]


def test_chunk_borders_change_nothing(object_folder, tmp_path, arrays):
    reach = 0.4
    out = tmp_path / "chunked.parquet"
    process(FolderSource(object_folder.parent, object_mask="cell"), DEFAULT, ParquetSink(out),
            Settings(reach_um=reach, chunk_edge=24))
    got = rows(pl.read_parquet(out), "distance").filter(pl.col("distance_target") == "wall").sort("distance_label")
    exact = ndimage.distance_transform_edt(arrays["wall"] == 0, sampling=VOXEL_UM)
    expected = [min(exact[arrays["mito"] == i].min(), reach) for i in got["distance_label"]]
    assert got["distance_um"].to_numpy() == pytest.approx(expected, abs=1e-5)


def test_a_reach_wider_than_a_chunk_is_refused(object_folder, tmp_path):
    failures = process(FolderSource(object_folder.parent, object_mask="cell"), DEFAULT,
                       ParquetSink(tmp_path / "x.parquet"), Settings(reach_um=3.0, chunk_edge=24))
    assert "does not fit" in failures["cell_a"]


def test_no_column_of_the_report_has_the_null_type(report_path):
    """DuckDB-WASM cannot skip through a Null-typed column once a file has more than a few rows."""
    assert [c for c, t in pl.read_parquet_schema(report_path).items() if t == pl.Null] == []


def test_a_sample_of_masks_alone_is_measured_with_its_geometry(tmp_path, object_folder):
    """No labelled structure means no instance, distance or contact rows: frames with columns and no rows."""
    import shutil

    from organella.geometry.writer import GeometryWriter
    from organella.sources import FolderSource
    parent = tmp_path / "batch"
    shutil.copytree(object_folder, parent / "cell_m")
    (parent / "cell_m" / "sample_mito_label.tif").unlink()
    report = tmp_path / "masks.parquet"
    failures = process(FolderSource(parent, object_mask="cell"), DEFAULT, ParquetSink(report), Settings(reach_um=0.4),
                       GeometryWriter(tmp_path / "meshes"))
    assert failures == {}
    rows = pl.read_parquet(report)
    assert set(rows["row_type"]) >= {"object", "entity"} and rows.filter(pl.col("row_type") == "object")["mesh_geometry_file"][0]
