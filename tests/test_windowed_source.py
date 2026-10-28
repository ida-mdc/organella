"""An object read a window at a time is the object read whole, cut to that window."""

import json
import warnings
from pathlib import Path

import numpy as np
import pytest
import tifffile

from organella.config import RunConfig
from organella.measure import load_object
from organella.measure.source import ObjectSource

zarr = pytest.importorskip("zarr")

SHAPE = (12, 30, 28)


def _blob(shape, center, radius) -> np.ndarray:
    zz, yy, xx = np.ogrid[: shape[0], : shape[1], : shape[2]]
    return ((zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2) <= radius**2


def _store(tmp_path: Path) -> Path:
    """An N5 store with a cell, mitochondria as instances and a mask, and the cell's own boundary beside it."""
    path = tmp_path / "sample.n5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        from zarr.n5 import N5Store

        root = zarr.open(N5Store(str(path)), mode="w")
        cell = _blob(SHAPE, (6, 15, 14), 9)
        root.create_dataset("em/fibsem/s0", data=(cell * 200).astype(np.uint16), chunks=(5, 11, 9))
        mito = np.zeros(SHAPE, np.uint16)
        mito[_blob(SHAPE, (4, 12, 10), 2)] = 1
        mito[_blob(SHAPE, (8, 18, 18), 3)] = 2
        mito[_blob(SHAPE, (6, 3, 3), 2)] = 3                       # outside the cell: clipped away
        root.create_dataset("labels/mito_seg/s0", data=mito, chunks=(5, 11, 9))
        er = (_blob(SHAPE, (6, 15, 14), 6) & ~_blob(SHAPE, (6, 15, 14), 3)).astype(np.uint8)
        root.create_dataset("labels/er_seg/s0", data=er * 255, chunks=(5, 11, 9))
    return path


def _object(tmp_path: Path, crop: str | None = None) -> Path:
    store = _store(tmp_path)
    folder = tmp_path / "cell_01"
    folder.mkdir()
    shape = SHAPE
    body = {"store": str(store), "scale": "s0", "source": "em/fibsem", "voxel_size_um": [0.02, 0.01, 0.01],
            "entities": {"mito": {"array": "labels/mito_seg", "kind": "label"},
                         "er": {"array": "labels/er_seg", "kind": "mask"}},
            "masks": {"cell": "cell_mask.tif"}, "object_mask": "cell"}
    if crop:
        body["crop"] = crop
        shape = tuple(int(p.split(":")[1]) - int(p.split(":")[0]) for p in crop.split(","))
    (folder / "source.json").write_text(json.dumps(body))
    mask = _blob(SHAPE, (6, 15, 14), 8)
    if crop:
        mask = mask[tuple(slice(*map(int, p.split(":"))) for p in crop.split(","))]
    tifffile.imwrite(folder / "cell_mask.tif", mask.astype(np.uint8))
    assert mask.shape == shape
    return folder


BOXES = [
    None,
    (slice(0, 3), slice(0, 5), slice(0, 4)),
    (slice(3, 9), slice(7, 20), slice(5, 17)),
    (slice(0, 99), slice(10, 99), slice(0, 99)),         # past the edge: clipped to it
    (slice(5, 6), slice(4, 9), slice(4, 9)),             # one slice deep must stay a volume
]


@pytest.mark.parametrize("crop", [None, "1:11,2:28,1:26"])
def test_a_window_is_the_whole_object_cut_to_that_window(tmp_path, crop):
    folder = _object(tmp_path, crop)
    cfg = RunConfig(object_mask="cell")
    dense = load_object(folder, cfg)
    source = ObjectSource(folder, cfg)

    assert source.shape == tuple(dense.data.shape[1:])
    assert source.names == dense.entity_names
    assert source.kinds == dense.kinds
    assert source.sample_size == dense.sample_size
    whole = dense.volumes()
    for box in BOXES:
        got = source.window(box)
        want_box = tuple(slice(*s.indices(n)) for s, n in zip(box, source.shape)) if box else \
            tuple(slice(0, n) for n in source.shape)
        for name in dense.entity_names:
            assert got[name].ndim == 3
            assert np.array_equal(got[name], whole[name][want_box]), (name, box)


def test_a_window_reads_only_the_chunks_it_needs(tmp_path, monkeypatch):
    folder = _object(tmp_path)
    source = ObjectSource(folder, RunConfig(object_mask="cell"))
    from organella.measure import source as module

    asked = []
    real = module._array

    def watching(ref):
        array = real(ref)

        class Spy:
            shape = array.shape
            dtype = array.dtype

            def __getitem__(self, box):
                asked.append(box)
                return array[box]

        return Spy()

    monkeypatch.setattr(module, "_array", watching)
    source.window((slice(0, 3), slice(0, 4), slice(0, 4)))

    assert asked and all(b[0].stop - b[0].start <= 3 and b[1].stop - b[1].start <= 4 for b in asked)


def test_what_cannot_be_read_a_window_at_a_time_is_refused_when_the_object_is_opened(tmp_path):
    folder = _object(tmp_path)
    manifest = json.loads((folder / "source.json").read_text())
    manifest["entities"]["mito"] = "labels/mito_seg"                 # no declared kind: read off the content
    (folder / "source.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="no declared kind"):
        ObjectSource(folder, RunConfig(object_mask="cell"))


def test_a_source_names_the_labels_a_manifest_declared(tmp_path):
    source = ObjectSource(_object(tmp_path), RunConfig(object_mask="cell"))

    assert source.declared_labels() == ["mito"]


# ── instances, one window each ───────────────────────────────────────────────

def _dense_and_source(tmp_path, crop=None):
    folder = _object(tmp_path, crop)
    cfg = RunConfig(object_mask="cell")
    return load_object(folder, cfg), ObjectSource(folder, cfg)


@pytest.mark.parametrize("block", [5, 9, 256])
def test_the_box_and_size_of_every_instance_is_found_a_block_at_a_time(tmp_path, block):
    from scipy import ndimage

    from organella.measure.instance_windows import instance_boxes

    dense, source = _dense_and_source(tmp_path)
    labels = dense.volume(dense.entity_names.index("mito")).astype(np.int64)

    found = instance_boxes(source, "mito", block=block)

    expected = ndimage.find_objects(labels)
    assert sorted(found) == [i + 1 for i, b in enumerate(expected) if b is not None]
    for label, instance in found.items():
        assert instance.box == expected[label - 1]
        assert instance.voxels == int((labels == label).sum())


def test_an_instance_clipped_away_by_the_boundary_is_not_found(tmp_path):
    from organella.measure.instance_windows import instance_boxes

    _, source = _dense_and_source(tmp_path)

    assert 3 not in instance_boxes(source, "mito")


def test_label_ids_too_large_to_index_are_found_too():
    from organella.measure.instance_windows import _objects_in

    part = np.zeros((6, 6, 6), np.uint64)
    part[1:3, 1:3, 1:3] = 5_000_000_000
    part[4:6, 0:2, 3:5] = 7
    found = {label: (box, n) for label, box, n in _objects_in(part)}

    assert found[5_000_000_000] == ((slice(1, 3), slice(1, 3), slice(1, 3)), 8)
    assert found[7] == ((slice(4, 6), slice(0, 2), slice(3, 5)), 8)
    assert sorted(found) == [7, 5_000_000_000]


@pytest.mark.parametrize("crop", [None, "1:11,2:28,1:26"])
def test_an_instance_measured_in_its_own_window_has_the_dense_numbers(tmp_path, crop):
    from organella.analysis.shapes import label_metrics
    from organella.measure.instance_windows import instance_boxes, instance_shape

    dense, source = _dense_and_source(tmp_path, crop)
    labels = dense.volume(dense.entity_names.index("mito"))
    whole = label_metrics(labels, dense.sample_size)

    for label, instance in instance_boxes(source, "mito").items():
        got = instance_shape(source, "mito", instance)
        assert sorted(got) == sorted(whole[label])
        for key, value in whole[label].items():
            if key == "ellipsoid_axes":
                # The directions of an ellipsoid are only defined up to sign, and not at all for a round one.
                d = sorted(whole[label]["ellipsoid_diameters_um"])
                if d[1] - d[0] > 0.05 * d[2] and d[2] - d[1] > 0.05 * d[2]:
                    assert np.allclose(np.abs(got[key]), np.abs(value), atol=1e-6), (label, got[key], value)
                continue
            assert np.allclose(got[key], value), (label, key, got[key], value)


# ── distances and contact area, in a window ──────────────────────────────────

def test_the_nearest_a_structure_gets_to_another_is_the_dense_distance_where_it_is_within_reach(tmp_path):
    from organella.analysis.distances import distance_target, distance_transform_um
    from organella.measure.instance_windows import instance_boxes, instance_distances

    dense, source = _dense_and_source(tmp_path)
    boxes = instance_boxes(source, "mito")
    volumes = dense.volumes()
    for reach in (0.05, 3.0):
        for label, instance in boxes.items():
            got = instance_distances(source, "mito", instance, reach_um=reach)
            for target in dense.entity_names:
                if target == "mito":
                    continue
                transform = distance_transform_um(
                    distance_target(volumes[target], target, dense.kinds[target], dense.object_mask_name),
                    dense.sample_size)
                exact = float(transform[volumes["mito"] == label].min())
                if exact <= reach:
                    assert got[target]["distance_um"] == pytest.approx(exact, rel=1e-5), (label, target, reach)
                else:
                    assert got[target]["distance_um"] == float("inf"), (label, target, reach)


def test_the_area_of_an_instance_near_a_target_is_the_area_of_its_faces_that_near(tmp_path):
    from organella.measure.instance_windows import contact_area_um2
    from organella.analysis.distances import distance_transform_um

    shape = (14, 16, 16)
    instance = np.zeros(shape, bool)
    instance[2:6, 4:8, 4:8] = True                      # a cube, 4 voxels a side
    target = np.zeros(shape, bool)
    target[8:, :, :] = True                              # a slab two empty voxels above it
    sample = (1.0, 1.0, 1.0)
    distance = distance_transform_um(target, sample)

    # The layer facing the slab is 3 voxels from it: 16 faces towards the slab and its 16 side faces.
    assert contact_area_um2(instance, distance, sample, threshold_um=2.9) == 0.0
    assert contact_area_um2(instance, distance, sample, threshold_um=3.0) == 32.0
    # 2 µm deep in z and 1 µm in y and x: the layer facing the slab is 6 µm from it, and its faces are 16 across z
    # (1 µm² each) and 8 across y and 8 across x (2 µm² each).
    odd = (2.0, 1.0, 1.0)
    distance = distance_transform_um(target, odd)
    assert contact_area_um2(instance, distance, odd, threshold_um=5.9) == 0.0
    assert contact_area_um2(instance, distance, odd, threshold_um=6.0) == 16 * 1.0 + 8 * 2.0 + 8 * 2.0


def test_the_area_on_the_edge_of_a_window_does_not_wrap_round():
    from organella.measure.instance_windows import contact_area_um2

    instance = np.zeros((4, 4, 4), bool)
    instance[0, :, :] = True                             # a layer on the face of the window
    near = np.zeros((4, 4, 4))                           # everything is at distance 0

    # Two big faces of 16 and four side strips of 4 each, with nothing counted across the edge.
    assert contact_area_um2(instance, near, (1.0, 1.0, 1.0), 1.0) == 16 + 16 + 4 * 4


# ── the same rows as the dense measurer ──────────────────────────────────────

def _as_float(value):
    return float("nan") if value is None else float(value)


def test_the_instance_and_distance_rows_from_windows_are_the_dense_ones(tmp_path):
    from organella.measure.instance_windows import instance_boxes, instance_tables, shape_stats
    from organella.measure.instances import DISTANCE_ROW, INSTANCE_ROW, InstanceMeasurer

    dense, source = _dense_and_source(tmp_path)
    tables = InstanceMeasurer(RunConfig(object_mask="cell")).measure(dense).tables
    want_inst, want_dist = tables[INSTANCE_ROW], tables[DISTANCE_ROW]

    boxes = {"mito": instance_boxes(source, "mito")}
    got_inst, got_dist = instance_tables(source, boxes, shape_stats(source, boxes), reach_um=100.0)

    def keyed(table, keys):
        columns = list(table)
        rows = {}
        for i in range(len(table[keys[0]])):
            rows[tuple(table[k][i] for k in keys)] = {c: table[c][i] for c in columns}
        return rows

    a = keyed(want_inst, ["instance_entity", "instance_label"])
    b = keyed(got_inst, ["instance_entity", "instance_label"])
    assert sorted(a) == sorted(b)
    for key in a:
        for column, value in a[key].items():
            if column in ("instance_entity",):
                continue
            assert np.isclose(_as_float(b[key][column]), _as_float(value), equal_nan=True, rtol=1e-6), (key, column)

    keys = ["distance_entity", "distance_label", "distance_target"]
    a, b = keyed(want_dist, keys), keyed(got_dist, keys)
    assert sorted(a) == sorted(b)
    for key in a:
        assert b[key]["distance_um"] == pytest.approx(a[key]["distance_um"], rel=1e-5), key


@pytest.mark.parametrize("crop", [None, "1:11,2:28,1:26"])
def test_what_a_source_knows_of_the_object_is_what_a_loaded_stack_does(tmp_path, crop):
    """The box, the centre and the count come from the mask a block at a time, and the metadata is shared."""
    folder = _object(tmp_path, crop)
    cfg = RunConfig(object_mask="cell")
    dense = load_object(folder, cfg)
    source = ObjectSource(folder, cfg)

    assert source.meta.keys() == dense.meta.keys()
    for key, want in dense.meta.items():
        assert source.meta[key] == pytest.approx(want), key
    assert source.enclosed_voxels == int((dense.volumes()["cell"] > 0).sum())
    assert source.center == pytest.approx(dense.center)


@pytest.mark.parametrize("factor", [1, 2, 3])
def test_a_coarse_stack_is_every_nth_voxel_of_the_object(tmp_path, factor):
    folder = _object(tmp_path)
    cfg = RunConfig(object_mask="cell")
    dense = load_object(folder, cfg)
    coarse = ObjectSource(folder, cfg).coarse_stack(factor)

    assert np.array_equal(coarse.data, dense.data[:, ::factor, ::factor, ::factor])
    assert coarse.entity_names == dense.entity_names
    assert coarse.kinds == dense.kinds
    assert coarse.sample_size == pytest.approx(tuple(size * factor for size in dense.sample_size))
    assert coarse.center == pytest.approx(dense.center)          # the full-resolution centre, not one recomputed


def test_the_coarse_factor_is_the_smallest_that_fits(tmp_path):
    source = ObjectSource(_object(tmp_path), RunConfig(object_mask="cell"))
    whole = int(np.prod(source.shape))

    assert source.coarse_factor(whole) == 1
    factor = source.coarse_factor(whole // 8)
    assert int(np.prod([-(-n // factor) for n in source.shape])) <= whole // 8
    assert int(np.prod([-(-n // (factor - 1)) for n in source.shape])) > whole // 8


@pytest.mark.parametrize("gap", [0.02, 0.1, 5.0])
def test_the_contacts_found_from_windows_are_the_dense_ones(tmp_path, gap):
    from organella.analysis.gaps import pairwise_instance_gaps
    from organella.measure.instance_windows import instance_boxes, instance_contacts

    dense, source = _dense_and_source(tmp_path)
    boxes = {"mito": instance_boxes(source, "mito")}
    want = pairwise_instance_gaps(dense.volumes(), dense.kinds, dense.sample_size, gap)
    got = instance_contacts(source, boxes, gap)

    assert got == want
    if gap == 5.0:
        assert want, "the fixture has no pair this close, so the test would pass on nothing"
