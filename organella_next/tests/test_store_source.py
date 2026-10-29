import json

import numpy as np
import zarr

import pytest
from organella.sources import StoreSource, open_source


def test_a_store_manifest_opens_a_cropped_sample(tmp_path):
    root = zarr.open_group(tmp_path / "cell.zarr", mode="w")
    data = np.arange(8 * 8 * 8, dtype=np.uint16).reshape(8, 8, 8)
    root.create_array("labels/mito", data=data, chunks=(4, 4, 4))
    root.create_array("labels/cell", data=(data > 0).astype(np.uint8), chunks=(4, 4, 4))
    manifest = tmp_path / "cell_a.json"
    manifest.write_text(json.dumps({
        "store": str(tmp_path / "cell.zarr"), "voxel_size_um": [0.1, 0.1, 0.1], "crop": "2:6,0:8,0:4",
        "entities": {"mito": "labels/mito", "cell": "labels/cell"}, "masks": ["cell"],
    }))
    (ref,) = StoreSource(manifest, object_mask="cell").samples()
    sample = ref.open()
    assert sample.shape == (4, 8, 4)
    assert sample.structures["cell"].kind.value == "mask"
    np.testing.assert_array_equal(sample.structures["mito"].data.compute(), data[2:6, :, :4])


def test_a_manifest_in_the_older_form_opens_with_a_level_a_local_mask_and_the_voxel_size_from_the_store(tmp_path):
    import tifffile
    root = zarr.open_group(tmp_path / "cell.zarr", mode="w")
    labels = np.zeros((6, 8, 8), np.uint16)
    labels[1:3, 2:4, 2:4] = 4
    array = root.create_array("labels/mito/s1", data=labels, chunks=(3, 4, 4))
    array.attrs["transform"] = {"axes": ["z", "y", "x"], "scale": [20.0, 10.0, 10.0], "units": ["nm", "nm", "nm"]}
    folder = tmp_path / "cells" / "cell_a"
    folder.mkdir(parents=True)
    tifffile.imwrite(folder / "cell_mask.tif", np.ones((4, 8, 8), np.uint8))
    (folder / "source.json").write_text(json.dumps({
        "store": str(tmp_path / "cell.zarr"), "scale": "s1", "crop": "1:5,:,:",
        "entities": {"mito": {"array": "labels/mito", "kind": "label"}},
        "masks": {"cell": "cell_mask.tif"}, "object_mask": "cell"}))
    (ref,) = open_source(tmp_path / "cells").samples()
    sample = ref.open()
    assert ref.id == "cell_a" and sample.object_mask == "cell" and sample.shape == (4, 8, 8)
    assert sample.voxel_size.um == pytest.approx((0.02, 0.01, 0.01))
    assert {n: s.kind.value for n, s in sample.structures.items()} == {"mito": "label", "cell": "mask"}


def write_n5(root, array_path, zyx):
    """A one-block raw N5 array. N5 lists dimensions fastest-varying first: x, y, z."""
    import struct
    array_dir = root / array_path
    (array_dir / "0" / "0").mkdir(parents=True)
    (root / "attributes.json").write_text(json.dumps({"n5": "2.0.0"}))
    z, y, x = zyx.shape
    (array_dir / "attributes.json").write_text(json.dumps({
        "dimensions": [x, y, z], "blockSize": [x, y, z], "dataType": "uint16", "compression": {"type": "raw"}}))
    header = struct.pack(">HH3I", 0, 3, x, y, z)
    (array_dir / "0" / "0" / "0").write_bytes(header + zyx.astype(">u2").tobytes())


def test_an_n5_array_comes_back_in_z_y_x_order(tmp_path):
    from organella.sources.arrays import open_store_array
    zyx = np.arange(4 * 6 * 8, dtype=np.uint16).reshape(4, 6, 8)
    write_n5(tmp_path / "cell.n5", "labels/mito", zyx)
    got = open_store_array(str(tmp_path / "cell.n5"), "labels/mito")
    assert got.shape == (4, 6, 8)
    np.testing.assert_array_equal(got.compute(), zyx)


def test_a_manifest_over_an_n5_store_clips_in_the_right_axes(tmp_path):
    """The mask and the crop are z, y, x. With the store's axes swapped the clip keeps the wrong voxels."""
    import tifffile
    from organella.app.prepare import prepared
    from organella.app.settings import Settings

    z, y, x = 4, 6, 8
    labels = np.zeros((z, y, x), np.uint16)
    labels[:, :, :4] = 1                      # the left half in x
    write_n5(tmp_path / "cell.n5", "labels/mito", labels)
    folder = tmp_path / "cells" / "cell_a"
    folder.mkdir(parents=True)
    mask = np.zeros((z, y, x), np.uint8)
    mask[:, :, 2:] = 1                        # x from 2 on: 2 of the 4 labelled columns are inside
    tifffile.imwrite(folder / "cell_mask.tif", mask)
    (folder / "source.json").write_text(json.dumps({
        "store": str(tmp_path / "cell.n5"), "voxel_size_um": [1, 1, 1],
        "entities": {"mito": {"array": "labels/mito", "kind": "label"}},
        "masks": {"cell": "cell_mask.tif"}, "object_mask": "cell"}))
    (ref,) = open_source(tmp_path / "cells").samples()
    sample = prepared(ref.open(), Settings(chunk_edge=8))
    assert int((sample.structures["mito"].data > 0).sum().compute()) == z * y * 2
