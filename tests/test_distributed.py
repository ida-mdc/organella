import json

import numpy as np
import pytest
import zarr
from click.testing import CliRunner

from organella.cli import main
from organella.report.file import read_report

distributed = pytest.importorskip("distributed")


def make_cell(tmp_path):
    root = zarr.open_group(tmp_path / "cell.zarr", mode="w")
    labels = np.zeros((16, 24, 24), np.uint16)
    labels[2:7, 3:9, 3:9] = 4
    labels[9:14, 12:19, 12:19] = 5
    array = root.create_array("labels/mito/s1", data=labels, chunks=(8, 12, 12))
    array.attrs["transform"] = {"axes": ["z", "y", "x"], "scale": [20.0, 10.0, 10.0], "units": ["nm", "nm", "nm"]}
    mask = root.create_array("labels/cell/s1", data=np.ones((16, 24, 24), np.uint16), chunks=(8, 12, 12))
    mask.attrs["transform"] = {"axes": ["z", "y", "x"], "scale": [20.0, 10.0, 10.0], "units": ["nm", "nm", "nm"]}
    folder = tmp_path / "cells" / "cell_a"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(json.dumps({
        "store": str(tmp_path / "cell.zarr"), "scale": "s1", "crop": ":,:,:",
        "entities": {"mito": {"array": "labels/mito", "kind": "label"}, "cell": "labels/cell"},
        "masks": ["cell"], "object_mask": "cell"}))
    return folder / "source.json"


def test_a_run_on_a_distributed_cluster_measures_what_the_run_on_threads_does(tmp_path):
    """The chunks go to worker processes, which read the store themselves; what comes back is the same report."""
    from distributed import Client, LocalCluster
    from polars.testing import assert_frame_equal

    manifest = make_cell(tmp_path)
    options = ["--with-mesh", "--object-noun", "cell", "--chunk-edge", "12", "--reach-um", "0.05", "--mesh-workers", "0"]
    threads = CliRunner().invoke(main, ["process", str(manifest), "-o", str(tmp_path / "threads.parquet"), "--mesh-dir", str(tmp_path / "m1"), *options])
    assert threads.exit_code == 0, threads.output
    with LocalCluster(n_workers=2, threads_per_worker=1, dashboard_address=None) as cluster, Client(cluster):
        cluster_run = CliRunner().invoke(main, ["process", str(manifest), "-o", str(tmp_path / "cluster.parquet"), "--mesh-dir", str(tmp_path / "m2"),
                                                "--scheduler", cluster.scheduler_address, *options])
        assert cluster_run.exit_code == 0, cluster_run.output
        assert "running on 2 worker(s)" in cluster_run.output
    a, b = read_report(tmp_path / "threads.parquet")[0], read_report(tmp_path / "cluster.parquet")[0]
    columns = [c for c in a.columns if c in b.columns and c != "mesh_geometry_file"]
    keys = [c for c in ("object_id", "row_type", "entity_name", "instance_label", "contact_label_a", "contact_label_b", "distance_label",
                        "distance_target", "baseline_target") if c in columns]
    assert_frame_equal(a.select(columns).sort(keys), b.select(columns).sort(keys), check_exact=False, rel_tol=1e-9)
    assert (tmp_path / "m2" / "cell_a" / "geometry.parquet").is_file()


def test_the_scheduler_option_says_what_is_missing_without_the_distributed_package(monkeypatch, tmp_path):
    import sys
    monkeypatch.setitem(sys.modules, "distributed", None)
    result = CliRunner().invoke(main, ["process", str(make_cell(tmp_path)), "-o", str(tmp_path / "x.parquet"), "--scheduler", "tcp://127.0.0.1:1"])
    assert result.exit_code != 0 and "uv sync --extra distributed" in result.output
