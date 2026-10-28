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
