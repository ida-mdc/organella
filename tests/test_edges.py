"""Inputs that broke or silently misled the first version."""
import json

import numpy as np
import polars as pl
import pytest
import tifffile
from scipy import ndimage

from organella.app.process import process
from organella.app.settings import Settings
from organella.core import Kind, Sample, Structure, VoxelSize
from organella.measure import DEFAULT
from organella.sinks import ParquetSink
from organella.sources import FolderSource, StoreSource
import dask.array as da


def sample_of(arrays: dict[str, np.ndarray], kinds: dict[str, Kind], voxel_um, mask=None) -> Sample:
    return Sample("s", None, VoxelSize(voxel_um),
                  {n: Structure(n, kinds[n], da.from_array(a, chunks=a.shape)) for n, a in arrays.items()}, mask)


def measure_all(sample: Sample, settings: Settings) -> pl.DataFrame:
    from organella.app.process import _measure
    return pl.concat(_measure(sample, DEFAULT, settings), how="diagonal_relaxed")


def distances(report: pl.DataFrame, target: str) -> pl.DataFrame:
    return report.filter((pl.col("row_type") == "distance") & (pl.col("distance_target") == target)).sort("distance_label")


@pytest.mark.parametrize("shape,voxel,reach,chunk", [
    ((130, 40, 40), (0.1, 0.1, 0.1), 1.0, 128),
    ((40, 40, 130), (0.1, 0.1, 0.1), 1.0, 128),
    ((40, 40, 40), (0.4, 0.05, 0.02), 0.3, 16),
])
def test_a_shape_that_is_not_a_multiple_of_the_chunk_gives_the_in_memory_answer(shape, voxel, reach, chunk):
    labels = np.zeros(shape, np.uint16)
    labels[3:7, 3:7, 3:7] = 1
    labels[-6:-2, -6:-2, -6:-2] = 2
    target = np.zeros(shape, np.uint8)
    target[shape[0] // 2, :, :] = 1
    report = measure_all(sample_of({"m": labels, "t": target}, {"m": Kind.LABEL, "t": Kind.MASK}, voxel),
                         Settings(reach_um=reach, chunk_edge=chunk))
    exact = ndimage.distance_transform_edt(target == 0, sampling=voxel)
    expected = [min(exact[labels == i].min(), reach) for i in (1, 2)]
    assert distances(report, "t")["distance_um"].to_numpy() == pytest.approx(expected, abs=1e-6)


def test_a_sample_that_cannot_be_opened_is_a_failure_and_the_others_still_run(tmp_path, object_folder):
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "a_broken").mkdir()
    tifffile.imwrite(batch / "a_broken" / "x_cell_mask.tif", np.ones((4, 4, 4), np.uint8))
    (batch / "z_good").symlink_to(object_folder)
    out = tmp_path / "r.parquet"
    failures = process(FolderSource(batch, object_mask="cell"), DEFAULT, ParquetSink(out), Settings(reach_um=0.4))
    assert list(failures) == ["a_broken"] and "voxel size" in failures["a_broken"]
    assert pl.read_parquet(out)["object_id"].unique().to_list() == ["z_good"]


def test_nothing_to_measure_is_reported(tmp_path):
    (tmp_path / "empty").mkdir()
    failures = process(FolderSource(tmp_path / "empty"), DEFAULT, ParquetSink(tmp_path / "r.parquet"), Settings())
    assert failures and not (tmp_path / "r.parquet").exists()


@pytest.mark.parametrize("dtype", [np.uint8, np.int32, np.uint64])
def test_label_dtypes_give_the_same_instances(dtype):
    labels = np.zeros((20, 20, 20), dtype)
    labels[2:6, 2:6, 2:6] = 5
    labels[10:14, 10:14, 10:14] = 9
    report = measure_all(sample_of({"m": labels}, {"m": Kind.LABEL}, (0.1,) * 3), Settings(reach_um=0.3, chunk_edge=8))
    got = report.filter(pl.col("row_type") == "instance").sort("instance_label")
    assert got["instance_label"].to_list() == [5, 9]
    assert got["instance_volume_um3"].to_numpy() == pytest.approx([64 * 1e-3] * 2)


def test_an_id_that_does_not_fit_int64_is_refused_not_wrapped():
    labels = np.zeros((8, 8, 8), np.uint64)
    labels[1:3, 1:3, 1:3] = 2**63 + 5
    with pytest.raises(ValueError, match="int64"):
        measure_all(sample_of({"m": labels}, {"m": Kind.LABEL}, (0.1,) * 3), Settings(reach_um=0.3, chunk_edge=8))


def test_an_empty_object_mask_has_no_centre():
    mask = np.zeros((8, 8, 8), np.uint8)
    report = measure_all(sample_of({"c": mask}, {"c": Kind.MASK}, (0.1,) * 3, mask="c"), Settings(reach_um=0.3, chunk_edge=8))
    row = report.filter(pl.col("row_type") == "object").row(0, named=True)
    assert row["object_volume_um3"] == 0 and row.get("object_center_z_um") is None


def test_a_2d_sample_is_measured():
    labels = np.zeros((30, 30), np.uint16)
    labels[5:9, 5:9] = 1
    target = np.zeros((30, 30), np.uint8)
    target[20, :] = 1
    report = measure_all(sample_of({"m": labels, "t": target}, {"m": Kind.LABEL, "t": Kind.MASK}, (0.1, 0.2)),
                         Settings(reach_um=2.0, chunk_edge=32))
    exact = ndimage.distance_transform_edt(target == 0, sampling=(0.1, 0.2))
    assert distances(report, "t")["distance_um"][0] == pytest.approx(exact[labels == 1].min(), abs=1e-6)


def test_a_voxel_size_of_the_wrong_rank_is_refused():
    with pytest.raises(ValueError, match="voxel size"):
        sample_of({"m": np.zeros((4, 4), np.uint8)}, {"m": Kind.LABEL}, (0.1, 0.1, 0.1))


def test_a_target_touching_the_volume_border_is_zero_away(tmp_path):
    labels = np.zeros((16, 16, 16), np.uint8)
    labels[0:2, 4:6, 4:6] = 1
    target = np.zeros_like(labels)
    target[0, :, :] = 1
    report = measure_all(sample_of({"m": labels, "t": target}, {"m": Kind.LABEL, "t": Kind.MASK}, (0.1,) * 3),
                         Settings(reach_um=0.5, chunk_edge=8))
    assert distances(report, "t")["distance_um"][0] == 0.0


def test_clipping_to_the_object_mask_can_be_turned_off():
    cell = np.zeros((16, 16, 16), np.uint8)
    cell[:, :, :8] = 1
    labels = np.zeros_like(cell, dtype=np.uint16)
    labels[4:8, 4:8, 4:12] = 1
    s = sample_of({"cell": cell, "m": labels}, {"cell": Kind.MASK, "m": Kind.LABEL}, (0.1,) * 3, mask="cell")
    vol = lambda r: r.filter(pl.col("row_type") == "instance")["instance_volume_um3"][0]
    clipped = vol(measure_all(s, Settings(reach_um=0.3, chunk_edge=8)))
    whole = vol(measure_all(s, Settings(reach_um=0.3, chunk_edge=8, clip_to_object_mask=False)))
    assert (clipped, whole) == pytest.approx((4 * 4 * 4 * 1e-3, 4 * 4 * 8 * 1e-3))


def test_a_manifest_crop_accepts_open_ends_and_refuses_empty_or_strided(tmp_path):
    import zarr
    root = zarr.open_group(tmp_path / "c.zarr", mode="w")
    root.create_array("a", data=np.ones((8, 8, 8), np.uint8), chunks=(4, 4, 4))
    def sample(crop):
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"store": str(tmp_path / "c.zarr"), "voxel_size_um": [1, 1, 1],
                                        "crop": crop, "entities": {"a": "a"}}))
        (ref,) = StoreSource(manifest).samples()
        return ref.open()
    assert sample(":,:,:").shape == (8, 8, 8)
    assert sample("-5:,0:3,:3").shape == (5, 3, 3)
    for bad in ("5:2,0:3,0:3", "::2,:,:"):
        with pytest.raises(ValueError):
            sample(bad)


def test_prefixes_are_matched_longest_first_and_duplicates_are_refused(tmp_path):
    folder = tmp_path / "o"
    folder.mkdir()
    for name in ("a.tif", "a_b.tif", "a_b_m_label.tif"):
        tifffile.imwrite(folder / name, np.zeros((4, 4, 4), np.uint8))
    from organella.sources.folder import object_files
    assert [e.name for e in object_files(folder).entities] == ["m"]
    tifffile.imwrite(folder / "a_m_label.tif", np.zeros((4, 4, 4), np.uint8))
    with pytest.raises(ValueError, match="'m'"):
        object_files(folder)


def test_a_labelled_structure_with_no_instances_beside_one_with_instances():
    present = np.zeros((16, 16, 16), np.uint16)
    present[2:6, 2:6, 2:6] = 1
    absent = np.zeros_like(present)
    report = measure_all(sample_of({"a": present, "b": absent}, {"a": Kind.LABEL, "b": Kind.LABEL}, (0.1,) * 3),
                         Settings(reach_um=0.3, chunk_edge=8))
    assert report.filter(pl.col("row_type") == "instance")["instance_entity"].unique().to_list() == ["a"]
    assert distances(report, "b")["distance_um"].to_list() == [0.3]


def test_distance_to_the_object_mask_is_the_depth_inside_it():
    shape, voxel, reach = (30, 40, 40), (0.1, 0.05, 0.05), 0.6
    cell = np.zeros(shape, np.uint8)
    cell[5:25, 8:32, 8:32] = 1
    labels = np.zeros(shape, np.uint16)
    labels[12:16, 18:22, 18:22] = 1      # deep inside
    labels[6:8, 9:11, 9:11] = 2          # near the corner of the cell
    s = sample_of({"cell": cell, "m": labels}, {"cell": Kind.MASK, "m": Kind.LABEL}, voxel, mask="cell")
    report = measure_all(s, Settings(reach_um=reach, chunk_edge=16))
    depth = ndimage.distance_transform_edt(cell, sampling=voxel)
    expected = [min(depth[labels == i].min(), reach) for i in (1, 2)]
    assert distances(report, "cell")["distance_um"].to_numpy() == pytest.approx(expected, abs=1e-6)
    baseline = report.filter((pl.col("row_type") == "baseline") & (pl.col("baseline_target") == "cell"))
    assert baseline["baseline_voxels"][0] == int(cell.sum())


def test_a_target_with_no_voxels_has_no_baseline():
    labels = np.zeros((16, 16, 16), np.uint16)
    labels[2:6, 2:6, 2:6] = 1
    report = measure_all(sample_of({"a": labels, "b": np.zeros_like(labels)}, {"a": Kind.LABEL, "b": Kind.LABEL}, (0.1,) * 3),
                         Settings(reach_um=0.3, chunk_edge=8))
    assert report.filter(pl.col("row_type") == "baseline")["baseline_target"].to_list() == ["a"]


def test_a_plane_reports_area_perimeter_and_circularity():
    labels = np.zeros((40, 40), np.uint16)
    labels[8:20, 8:20] = 1
    cell = np.ones((40, 40), np.uint8)
    report = measure_all(sample_of({"cell": cell, "m": labels}, {"cell": Kind.MASK, "m": Kind.LABEL}, (0.1, 0.1), mask="cell"),
                         Settings(reach_um=0.5, chunk_edge=16))
    row = report.filter(pl.col("row_type") == "instance").row(0, named=True)
    assert row["instance_area_um2"] == pytest.approx(144 * 0.01)
    assert 0.7 < row["instance_circularity"] <= 1.1 and row["instance_perimeter_um"] > 0
    assert "instance_volume_um3" not in report.columns and "total_area_um2" in report.columns
    assert report.filter(pl.col("row_type") == "object")["object_area_um2"][0] == pytest.approx(16.0)


def test_a_compressed_and_an_uncompressed_tiff_both_open_and_slice(tmp_path):
    from organella.sources.arrays import open_tiff

    volume = np.random.default_rng(4).integers(0, 9, (6, 20, 24)).astype(np.uint16)
    for name, kwargs in (("plain.tif", {}), ("packed.tif", {"compression": "zlib"})):
        tifffile.imwrite(tmp_path / name, volume, **kwargs)
        np.testing.assert_array_equal(open_tiff(tmp_path / name)[1:4, 5:9].compute(), volume[1:4, 5:9])


def test_contacts_are_rows_with_a_count_on_the_object_and_can_be_switched_off():
    labels = np.zeros((20, 20, 20), np.uint16)
    labels[2:6, 2:6, 2:6] = 1
    labels[6:10, 2:6, 2:6] = 2          # shares a face with 1
    labels[15:19, 15:19, 15:19] = 3     # far from both
    cell = np.ones_like(labels, dtype=np.uint8)
    s = sample_of({"cell": cell, "m": labels}, {"cell": Kind.MASK, "m": Kind.LABEL}, (0.1,) * 3, mask="cell")
    report = measure_all(s, Settings(reach_um=0.3, chunk_edge=8, contact_max_um=0.3))
    contacts = report.filter(pl.col("row_type") == "contact")
    assert contacts.select("contact_entity", "contact_label_a", "contact_label_b").rows() == [("m", 1, 2)]
    assert contacts["contact_gap_um"][0] == pytest.approx(0.1)      # one voxel step, not zero
    assert report.filter(pl.col("row_type") == "object")["contact_count"][0] == 1
    off = measure_all(s, Settings(reach_um=0.3, chunk_edge=8, contacts=False))
    assert "contact_count" not in off.columns and off.filter(pl.col("row_type") == "contact").is_empty()


def test_skeleton_columns_and_how_far_the_tips_of_an_instance_are_from_a_structure():
    shape = (16, 16, 60)
    z, y, x = np.indices(shape)
    tube = ((y - 8) ** 2 + (z - 8) ** 2 <= 3.5 ** 2) & (x >= 6) & (x <= 53)    # 48 voxels long, along x
    wall = np.zeros(shape, np.uint8)
    wall[:, :, 0:3] = 1                                                           # beside the tube's low-x end
    labels = tube.astype(np.uint16)
    s = sample_of({"t": labels, "wall": wall}, {"t": Kind.LABEL, "wall": Kind.MASK}, (0.1,) * 3)
    report = measure_all(s, Settings(reach_um=4.0, chunk_edge=48, contacts=False, skeletons=frozenset({"t"})))
    row = report.filter(pl.col("row_type") == "instance").row(0, named=True)
    assert row["instance_branches"] == 1 and row["instance_length_um"] > 4.0 and row["instance_tortuosity"] >= 1.0
    far = report.filter((pl.col("row_type") == "distance") & (pl.col("distance_target") == "wall")).row(0, named=True)
    assert far["distance_end_min_um"] < far["distance_end_max_um"]               # one end by the wall, the other not
    assert far["distance_end_min_um"] == pytest.approx(far["distance_um"], abs=0.35)


def test_without_a_skeleton_request_there_are_no_skeleton_or_end_columns():
    labels = np.zeros((16, 16, 16), np.uint16)
    labels[4:10, 4:10, 4:10] = 1
    report = measure_all(sample_of({"t": labels, "w": np.ones_like(labels, np.uint8)}, {"t": Kind.LABEL, "w": Kind.MASK}, (0.1,) * 3),
                         Settings(reach_um=0.5, chunk_edge=16, contacts=False))
    assert not [c for c in report.columns if "branches" in c or "tortuosity" in c or "_end_" in c]


def test_a_skeletonised_structure_beside_one_that_is_not():
    shape = (16, 16, 40)
    z, y, x = np.indices(shape)
    tube = (((y - 8) ** 2 + (z - 8) ** 2 <= 3.5 ** 2) & (x >= 5) & (x <= 34)).astype(np.uint16)
    blobs = np.zeros(shape, np.uint16)
    blobs[2:5, 2:5, 2:5] = 1
    s = sample_of({"tube": tube, "blobs": blobs}, {"tube": Kind.LABEL, "blobs": Kind.LABEL}, (0.1,) * 3)
    report = measure_all(s, Settings(reach_um=1.0, chunk_edge=48, contacts=False, skeletons=frozenset({"tube"})))
    got = report.filter(pl.col("row_type") == "instance").sort("instance_entity").select("instance_entity", "instance_branches")
    assert got["instance_entity"].to_list() == ["blobs", "tube"]
    assert np.isnan(got["instance_branches"][0]) and got["instance_branches"][1] == 1


def test_tip_distances_to_a_structure_with_no_voxels_are_null_where_another_target_has_them():
    shape = (16, 16, 40)
    z, y, x = np.indices(shape)
    tube = (((y - 8) ** 2 + (z - 8) ** 2 <= 3.5 ** 2) & (x >= 5) & (x <= 34)).astype(np.uint16)
    wall = np.zeros(shape, np.uint8)
    wall[:, :, 0:2] = 1
    s = sample_of({"tube": tube, "wall": wall, "ghost": np.zeros(shape, np.uint8)},
                  {"tube": Kind.LABEL, "wall": Kind.MASK, "ghost": Kind.MASK}, (0.1,) * 3)
    report = measure_all(s, Settings(reach_um=1.0, chunk_edge=48, contacts=False, skeletons=frozenset({"tube"})))
    towards = lambda name: report.filter((pl.col("row_type") == "distance") & (pl.col("distance_target") == name)).row(0, named=True)
    assert towards("wall")["distance_end_min_um"] is not None
    assert towards("ghost")["distance_end_min_um"] is None and towards("ghost")["distance_end_max_um"] is None


def blobs_in_a_cell():
    shape = (30, 60, 80)
    z, y, x = np.indices(shape)
    cell = np.ones(shape, np.uint8)
    nucleus = (((z - 15) ** 2 + (y - 30) ** 2 + (x - 60) ** 2) <= 8 ** 2).astype(np.uint8)
    blobs = np.zeros(shape, np.uint16)
    blobs[(((z - 15) ** 2 + (y - 20) ** 2 + (x - 15) ** 2) <= 9)] = 1                               # compact, near the left
    ring = (np.hypot(y - 30, x - 40) <= 22) & (np.hypot(y - 30, x - 40) >= 19) & (np.abs(z - 15) <= 1)
    blobs[ring] = 2                                                                                 # wraps around the middle
    return sample_of({"cell": cell, "nucleus": nucleus, "blobs": blobs}, {"cell": Kind.MASK, "nucleus": Kind.MASK, "blobs": Kind.LABEL}, (0.1,) * 3, mask="cell")


def test_a_structure_left_out_of_the_baseline_takes_its_voxels_out_and_says_so():
    s = blobs_in_a_cell()
    plain = measure_all(s, Settings(reach_um=1.0, chunk_edge=48, contacts=False))
    left_out = measure_all(s, Settings(reach_um=1.0, chunk_edge=48, contacts=False, baseline_exclude=frozenset({"nucleus"})))
    pick = lambda r: r.filter((pl.col("row_type") == "baseline") & (pl.col("baseline_target") == "blobs")).row(0, named=True)
    nucleus_voxels = int(s.structures["nucleus"].data.sum().compute())
    assert pick(plain)["baseline_voxels"] - pick(left_out)["baseline_voxels"] == nucleus_voxels - int(((s.structures["blobs"].data > 0) & (s.structures["nucleus"].data > 0)).sum().compute())
    assert pick(left_out)["baseline_excluded"] == "nucleus" and pick(plain)["baseline_excluded"] is None
    own = left_out.filter((pl.col("row_type") == "baseline") & (pl.col("baseline_target") == "nucleus")).row(0, named=True)
    assert own["baseline_excluded"] == "nucleus"       # named by the user, so listed even where it is also the target


def test_the_baseline_median_is_the_median_of_the_distances_to_the_target():
    s = blobs_in_a_cell()
    report = measure_all(s, Settings(reach_um=1.5, chunk_edge=48, contacts=False))
    row = report.filter((pl.col("row_type") == "baseline") & (pl.col("baseline_target") == "nucleus")).row(0, named=True)
    nucleus = s.structures["nucleus"].data.compute() > 0
    exact = ndimage.distance_transform_edt(~nucleus, sampling=(0.1,) * 3)[~nucleus]
    assert row["baseline_median_um"] == pytest.approx(float(np.median(np.minimum(exact, 1.5))), abs=0.005)
    assert sum(json.loads(row["baseline_hist_counts"])) == row["baseline_voxels"]


def test_distance_histograms_count_every_voxel_of_the_instance_and_have_a_mean():
    s = blobs_in_a_cell()
    report = measure_all(s, Settings(reach_um=1.5, chunk_edge=48, contacts=False, distance_histograms=True))
    rows = report.filter((pl.col("row_type") == "distance") & (pl.col("distance_target") == "nucleus")).sort("distance_label")
    for row in rows.to_dicts():
        voxels = int((s.structures["blobs"].data.compute() == row["distance_label"]).sum())
        counts = json.loads(row["distance_hist_counts"])
        assert len(counts) == 20 and sum(counts) == voxels
        assert row["distance_um"] <= row["distance_mean_um"] <= 1.5 and row["distance_hist_max_um"] == 1.5


def test_polarity_spread_is_small_for_a_compact_instance_and_large_for_one_that_wraps_the_centre():
    report = measure_all(blobs_in_a_cell(), Settings(reach_um=1.0, chunk_edge=48, contacts=False, polarity_spread=True))
    spread = dict(report.filter(pl.col("row_type") == "instance").select("instance_label", "instance_polar_spread_deg").rows())
    assert spread[1] < 8 and spread[2] > 3 * spread[1]      # a compact blob far away, against a ring around the centre
    assert "instance_polar_spread_deg" not in measure_all(blobs_in_a_cell(), Settings(reach_um=1.0, chunk_edge=48, contacts=False)).columns


def test_without_instances_only_the_per_structure_rows_remain_and_contacts_stay_their_own_switch():
    from organella.measure import Statistics
    from organella.app.process import _measure
    report = pl.concat(_measure(blobs_in_a_cell(), (Statistics(),), Settings(reach_um=1.0, chunk_edge=48, instances=False, contacts=False), None),
                       how="diagonal_relaxed")
    assert set(report["row_type"]) == {"object", "entity"}
    entities = report.filter(pl.col("row_type") == "entity")
    assert entities.filter(pl.col("entity_name") == "blobs")["instance_count"][0] == 2 and "surface_area_um2" in report.columns


def test_only_the_named_structures_are_measured_and_the_object_mask_is_kept(object_folder):
    from organella.sources import FolderSource
    (ref,) = FolderSource(object_folder.parent, object_mask="cell", entities=frozenset({"mito"})).samples()
    assert sorted(ref.open().structures) == ["cell", "mito"]
    with pytest.raises(ValueError, match="nothing.*does not have|--entities names nothing"):
        (FolderSource(object_folder.parent, object_mask="cell", entities=frozenset({"nothing"})).samples().__iter__().__next__()).open()
