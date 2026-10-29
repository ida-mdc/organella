import struct

import dask.array as da
import numpy as np
import polars as pl
import pytest
import SimpleITK as sitk

from organella.app.process import process
from organella.app.ports import SampleRef
from organella.app.settings import Settings, parse_geometry_as
from organella.core import Kind, Sample, Structure, VoxelSize
from organella.geometry.rows import ellipsoid_of
from organella.geometry.writer import FILENAME, GeometryWriter
from organella.measure import DEFAULT
from organella.measure.per_label import counts_and_centroids
from organella.sinks import ParquetSink


class OneSample:
    def __init__(self, sample):
        self.sample = sample

    def samples(self):
        yield SampleRef(self.sample.id, lambda: self.sample)


@pytest.mark.parametrize("semi_axes", [(8.0, 12.0, 20.0), (2.5, 3.5, 4.5), (1.6, 2.2, 3.1)])
def test_the_ellipsoid_is_the_one_ITK_reports_even_for_an_instance_a_few_voxels_across(semi_axes):
    z, y, x = np.indices((40, 40, 60))
    labels = ((((z - 20) / semi_axes[0]) ** 2 + ((y - 20) / semi_axes[1]) ** 2 + ((x - 30) / semi_axes[2]) ** 2) <= 1).astype(np.uint16)
    voxel = (0.2, 0.1, 0.05)
    image = sitk.GetImageFromArray(labels)
    image.SetSpacing(tuple(reversed(voxel)))
    itk = sitk.LabelShapeStatisticsImageFilter()
    itk.Execute(image)
    found = counts_and_centroids(da.from_array(labels, chunks=16)).compute()
    covariance = np.array([[found[f"v{min(i, j)}{max(i, j)}"][0] for j in range(3)] for i in range(3)])
    centroid = np.array([found[f"c{i}"][0] for i in range(3)])
    values = struct.unpack("<15f", ellipsoid_of(covariance, centroid, np.array(voxel), int(found["count"][0])))
    np.testing.assert_allclose(values[0:3], itk.GetCentroid(1), rtol=1e-5)                  # centre, x y z in µm
    np.testing.assert_allclose(np.array(values[3:6]) * 2, itk.GetEquivalentEllipsoidDiameter(1), rtol=1e-5)
    np.testing.assert_allclose(np.abs(values[6:15]), np.abs(itk.GetPrincipalAxes(1)), atol=1e-4)  # up to sign


def build_sample():
    shape, voxel = (40, 60, 80), VoxelSize((0.1, 0.1, 0.1))
    z, y, x = np.indices(shape)
    cell = np.ones(shape, np.uint8)
    nucleus = ((z - 20) ** 2 + (y - 15) ** 2 + (x - 60) ** 2 <= 36).astype(np.uint8)
    vesicles = np.zeros(shape, np.uint16)
    vesicles[((z - 20) ** 2 + (y - 40) ** 2 + (x - 15) ** 2) <= 16] = 1                         # small and round: an ellipsoid
    vesicles[((z - 20) ** 2 + (y - 40) ** 2 + (x - 22) ** 2) <= 16] = 2                         # touches it
    filament = (((y - 15) ** 2 + (z - 20) ** 2) <= 9) & (x >= 5) & (x <= 45)                    # long: a tube
    kinked = np.zeros(shape, bool)
    kinked[10:30, 30:34, 50:75] = True
    kinked[10:14, 30:56, 50:75] = True                                                           # an L: neither round nor a line
    labels = np.zeros(shape, np.uint16)
    labels[filament] = 1
    labels[kinked] = 2
    make = lambda a: da.from_array(a, chunks=32)
    return Sample("cell_a", "treated", voxel, {
        "cell": Structure("cell", Kind.MASK, make(cell)), "nucleus": Structure("nucleus", Kind.MASK, make(nucleus)),
        "vesicles": Structure("vesicles", Kind.LABEL, make(vesicles)), "mito": Structure("mito", Kind.LABEL, make(labels)),
    }, "cell")


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    folder = tmp_path_factory.mktemp("geometry")
    settings = Settings(reach_um=0.5, chunk_edge=32, contact_max_um=0.3, keep_skeletons=True,
                        geometry_as=parse_geometry_as("mito=skeleton"), mesh_target_reduction=0.0)
    report = folder / "report.parquet"
    failures = process(OneSample(build_sample()), DEFAULT, ParquetSink(report), settings, GeometryWriter(folder / "meshes"))
    assert failures == {}
    return pl.read_parquet(report), pl.read_parquet(folder / "meshes" / "cell_a" / FILENAME), folder


def test_the_object_row_says_where_its_geometry_went(written):
    report, _, folder = written
    path = report.filter(pl.col("row_type") == "object")["mesh_geometry_file"][0]
    assert path.endswith("cell_a/" + FILENAME) and (folder / "meshes" / "cell_a" / FILENAME).samefile(path)


def test_each_instance_is_drawn_as_the_kind_its_shape_calls_for(written):
    _, geometry, _ = written
    kinds = {(r["entity_name"], r["label_id"]): r["surface_kind"] for r in geometry.filter(pl.col("row_type") == "instance").to_dicts()}
    assert kinds[("vesicles", 1)] == kinds[("vesicles", 2)] == "ellipsoid"
    assert kinds[("mito", 1)] == "tube" and kinds[("mito", 2)] == "mesh"
    row = geometry.filter((pl.col("entity_name") == "mito") & (pl.col("label_id") == 1)).row(0, named=True)
    assert row["skeleton"] is not None and row["skeleton_vertices"] > 2 and row["surface_vertices"] > 2


def test_rows_carry_their_measurements_and_every_surface_has_geometry(written):
    report, geometry, _ = written
    instances = geometry.filter(pl.col("row_type") == "instance")
    assert instances["volume_um3"].null_count() == 0 and instances["sphericity"].null_count() == 0
    assert instances["surface"].null_count() == 0
    assert instances.filter(pl.col("surface_kind") == "ellipsoid")["surface_vertices"].null_count() == 2   # none by design
    reported = report.filter((pl.col("row_type") == "instance") & (pl.col("instance_entity") == "vesicles")).sort("instance_label")
    carried = instances.filter(pl.col("entity_name") == "vesicles").sort("label_id")
    assert carried["volume_um3"].to_list() == pytest.approx(reported["instance_volume_um3"].to_list())


def test_every_whole_structure_mask_is_a_file_row_the_object_mask_included(written):
    _, geometry, _ = written
    files = geometry.filter(pl.col("row_type") == "file").sort("entity_name")
    assert files["entity_name"].to_list() == ["cell", "nucleus"] and files["surface"].null_count() == 0
    assert set(files["entity_kind"]) == {"mask"} and files["volume_um3"].min() > 0


def test_touching_instances_are_contact_rows_in_the_geometry_too(written):
    _, geometry, _ = written
    contacts = geometry.filter(pl.col("row_type") == "contact")
    assert contacts.select("entity_a", "label_a", "label_b").rows() == [("vesicles", 1, 2)]
    assert contacts["gap_um"][0] == pytest.approx(0.1, abs=0.05)


def test_geometry_from_the_same_settings_is_kept_when_asked_and_made_again_otherwise(tmp_path):
    settings = Settings(reach_um=0.5, chunk_edge=32, keep_skeletons=True, mesh_target_reduction=0.0)
    for reuse, expect_new in ((False, True), (True, False), (False, True)):
        before = (tmp_path / "m" / "cell_a" / FILENAME).stat().st_mtime_ns if (tmp_path / "m" / "cell_a" / FILENAME).exists() else 0
        process(OneSample(build_sample()), DEFAULT, ParquetSink(tmp_path / "r.parquet"), settings, GeometryWriter(tmp_path / "m", reuse))
        after = (tmp_path / "m" / "cell_a" / FILENAME).stat().st_mtime_ns
        assert (after != before) == expect_new


def test_every_instance_row_holds_the_surface_of_its_own_instance(tmp_path):
    """Many instances of different sizes and kinds: a payload on the wrong row puts a surface far from its centroid."""
    rng = np.random.default_rng(7)
    shape, voxel = (50, 90, 90), VoxelSize((0.1, 0.1, 0.1))
    labels = np.zeros(shape, np.uint16)
    z, y, x = np.indices(shape)
    for label in range(1, 41):
        c = [int(rng.integers(8, n - 8)) for n in shape]
        r = float(rng.choice([2.2, 3.0, 4.5]))
        stretch = float(rng.choice([1.0, 1.0, 2.5]))
        labels[(((z - c[0]) / stretch) ** 2 + (y - c[1]) ** 2 + (x - c[2]) ** 2) <= r ** 2] = label
    sample = Sample("many", None, voxel, {"m": Structure("m", Kind.LABEL, da.from_array(labels, chunks=32))}, None)
    settings = Settings(reach_um=0.5, chunk_edge=32, contacts=False, geometry_as=parse_geometry_as("m=mesh"), mesh_target_reduction=0.0)
    process(OneSample(sample), DEFAULT, ParquetSink(tmp_path / "r.parquet"), settings, GeometryWriter(tmp_path / "g"))
    geometry = pl.read_parquet(tmp_path / "g" / "many" / FILENAME).filter(pl.col("row_type") == "instance")
    report = pl.read_parquet(tmp_path / "r.parquet").filter(pl.col("row_type") == "instance").sort("instance_label")
    checked = 0
    for row in geometry.sort("label_id").to_dicts():
        if row["surface"] is None:
            continue
        found = report.filter(pl.col("instance_label") == row["label_id"]).row(0, named=True)
        low = np.frombuffer(row["surface"], np.float32, 3, 8)
        high = low + np.frombuffer(row["surface"], np.float32, 3, 20)
        centre = np.array([found["instance_centroid_x_um"], found["instance_centroid_y_um"], found["instance_centroid_z_um"]])
        assert ((centre >= low - 0.1) & (centre <= high + 0.1)).all(), f"label {row['label_id']} holds another instance's mesh"
        checked += 1
    assert checked >= 30
