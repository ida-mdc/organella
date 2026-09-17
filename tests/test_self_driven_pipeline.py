"""The pipeline that measures the batch itself.
"""

import polars as pl
import pyarrow.parquet as pq
import pytest

from conftest import settings
from organella import pipeline, report_io
from organella.measure import find_object_dirs
from synthetic import make_object


@pytest.fixture
def batch(tmp_path):
    make_object(tmp_path / "control" / "object_a", prefix="sample_a")
    make_object(tmp_path / "treated" / "object_b", prefix="sample_b")
    return tmp_path


def test_every_object_is_measured_whole(batch):
    report = pipeline.analyse(find_object_dirs(batch), batch, ["control", "treated"], workers=1, config=settings())

    assert not report.failures
    objects = report.rows.filter(pl.col("obs_level") == 0)
    # Cross-entity columns exist for every object: nothing was measured from a fragment.
    assert objects["object_volume_um3"].null_count() == 0
    assert objects["contact_count"].null_count() == 0


def test_one_bad_object_does_not_stop_the_batch(batch):
    (batch / "treated" / "object_b" / "sample_b_pm_mask.tif").write_bytes(b"not a tiff")

    report = pipeline.analyse(find_object_dirs(batch), batch, ["control", "treated"], workers=1, config=settings())

    assert list(report.failures) == ["object_b"]
    assert report.rows.filter(pl.col("obs_level") == 0).height == 1
    assert "object_a" in report.rows["object_id"].to_list()


def test_workers_are_capped_by_what_an_object_costs():
    assert pipeline.worker_count(None, 3, peak_gb=10_000) == 1     # memory decides
    assert pipeline.worker_count(8, 2, peak_gb=0.1) == 2           # never more than objects
    assert pipeline.worker_count(1, 8, peak_gb=0.1) == 1           # an explicit request wins


def test_the_report_describes_an_object_not_a_file(batch, tmp_path):
    report = pipeline.analyse(find_object_dirs(batch), batch, [], workers=1, config=settings())
    out = report_io.write(report, tmp_path / "r.parquet", root=batch, paths=[])

    columns = set(pl.read_parquet(out).columns)

    assert {"object_id", "path", "size_bytes", "file_extension", "obs_level"} <= columns
    # PixelPatrol's per-file columns, and the artifacts of stacking entities, are left out.
    assert not ({"name", "parent", "depth", "type", "common_base",
                 "dtype", "ndim", "dim_names"} & columns)


def test_the_footer_carries_the_paths(batch, tmp_path):

    report = pipeline.analyse(find_object_dirs(batch), batch, ["control"], workers=1, config=settings())
    out = report_io.write(report, tmp_path / "r.parquet", root=batch, paths=["control"])

    meta = {k.decode(): v.decode() for k, v in pq.read_metadata(out).metadata.items()
            if k.startswith(b"organella_")}

    assert meta["organella_loader"] == "organella"
    assert '"control"' in meta["organella_paths"]


def test_every_column_says_what_it_means(report_path):
    """The parquet is self-describing, which is what a report handed to someone else needs.

    Descriptions come from column_schema, which gathers them from the measurers that fill
    the columns. A measurer that adds one without describing it leaves that column bare.
    """
    schema = pq.read_schema(report_path)
    bare = [f.name for f in schema if not (f.metadata or {}).get(b"description")]

    assert not bare, f"columns with no description: {bare}"


def test_measurements_are_stored_as_float32(batch, tmp_path):
    """The page loads the whole file; µm measurements do not need 15 digits."""
    report = pipeline.analyse(find_object_dirs(batch), batch, [], workers=1, config=settings())
    out = report_io.write(report, tmp_path / "r.parquet", root=batch, paths=[])

    schema = pl.read_parquet(out).schema

    assert schema["object_volume_um3"] == pl.Float32
    # Per-instance measurements are rows now, so this is the scalar column, not a list of them.
    assert schema["instance_volume_um3"] == pl.Float32
    assert schema["contact_gap_um"] == pl.Float32
