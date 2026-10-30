"""What the measurements come to on small arrays whose answer is known: against scipy where there is an exact one."""
import json
from dataclasses import replace

import dask.array as da
import numpy as np
import polars as pl
import pytest
from scipy import ndimage

from organella.app.process import _measure
from organella.app.settings import Settings
from organella.core import Kind, Sample, Structure, VoxelSize
from organella.measure import DEFAULT, Statistics


def sample_of(arrays: dict[str, np.ndarray], kinds: dict[str, Kind], voxel_um, mask=None) -> Sample:
    return Sample("s", None, VoxelSize(voxel_um),
                  {n: Structure(n, kinds[n], da.from_array(a, chunks=a.shape)) for n, a in arrays.items()}, mask)


def measure_all(sample: Sample, settings: Settings, measurements=DEFAULT) -> pl.DataFrame:
    return pl.concat(_measure(sample, measurements, settings), how="diagonal_relaxed")


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


def test_a_plane_is_measured_with_area_perimeter_circularity_and_distances():
    labels = np.zeros((40, 40), np.uint16)
    labels[8:20, 8:20] = 1
    target = np.zeros((40, 40), np.uint8)
    target[24, :] = 1
    kinds = {"cell": Kind.MASK, "m": Kind.LABEL, "t": Kind.MASK}
    s = sample_of({"cell": np.ones((40, 40), np.uint8), "m": labels, "t": target}, kinds, (0.1, 0.2), mask="cell")
    report = measure_all(s, Settings(reach_um=1.0, chunk_edge=32))
    exact = ndimage.distance_transform_edt(target == 0, sampling=(0.1, 0.2))
    assert distances(report, "t")["distance_um"][0] == pytest.approx(exact[labels == 1].min(), abs=1e-6)
    row = report.filter(pl.col("row_type") == "instance").row(0, named=True)
    assert row["instance_area_um2"] == pytest.approx(144 * 0.02)
    assert 0.7 < row["instance_circularity"] <= 1.1 and row["instance_perimeter_um"] > 0
    assert "instance_volume_um3" not in report.columns and "total_area_um2" in report.columns
    assert report.filter(pl.col("row_type") == "object")["object_area_um2"][0] == pytest.approx(1600 * 0.02)


def test_a_structure_with_no_instances_or_no_voxels_sits_beside_a_measured_one():
    present = np.zeros((16, 16, 16), np.uint16)
    present[2:6, 2:6, 2:6] = 1
    kinds = {"a": Kind.LABEL, "b": Kind.LABEL}
    report = measure_all(sample_of({"a": present, "b": np.zeros_like(present)}, kinds, (0.1,) * 3), Settings(reach_um=0.3, chunk_edge=8))
    assert report.filter(pl.col("row_type") == "instance")["instance_entity"].unique().to_list() == ["a"]
    assert distances(report, "b")["distance_um"].to_list() == [0.3]                       # nothing to be near: the reach
    assert report.filter(pl.col("row_type") == "baseline")["baseline_target"].to_list() == ["a"]   # and no baseline to it


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


def test_skeleton_columns_exist_only_for_what_was_asked_and_tips_need_a_target_with_voxels():
    shape = (16, 16, 40)
    z, y, x = np.indices(shape)
    tube = (((y - 8) ** 2 + (z - 8) ** 2 <= 3.5 ** 2) & (x >= 5) & (x <= 34)).astype(np.uint16)
    blobs = np.zeros(shape, np.uint16)
    blobs[2:5, 2:5, 2:5] = 1
    wall = np.zeros(shape, np.uint8)
    wall[:, :, 0:2] = 1
    kinds = {"tube": Kind.LABEL, "blobs": Kind.LABEL, "wall": Kind.MASK, "ghost": Kind.MASK}
    s = sample_of({"tube": tube, "blobs": blobs, "wall": wall, "ghost": np.zeros(shape, np.uint8)}, kinds, (0.1,) * 3)
    settings = Settings(reach_um=1.0, chunk_edge=48, contacts=False)
    plain = measure_all(s, settings)
    assert not [c for c in plain.columns if "branches" in c or "tortuosity" in c or "_end_" in c]
    report = measure_all(s, replace(settings, skeletons=frozenset({"tube"})))
    got = report.filter(pl.col("row_type") == "instance").sort("instance_entity")
    assert np.isnan(got["instance_branches"][0]) and got["instance_branches"][1] == 1          # blobs were not traced, the tube was
    towards = lambda name: report.filter((pl.col("row_type") == "distance") & (pl.col("distance_entity") == "tube")
                                         & (pl.col("distance_target") == name)).row(0, named=True)
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
    report = measure_all(blobs_in_a_cell(), Settings(reach_um=1.0, chunk_edge=48, instances=False, contacts=False), (Statistics(),))
    assert set(report["row_type"]) == {"object", "entity"}
    entities = report.filter(pl.col("row_type") == "entity")
    assert entities.filter(pl.col("entity_name") == "blobs")["instance_count"][0] == 2 and "surface_area_um2" in report.columns
