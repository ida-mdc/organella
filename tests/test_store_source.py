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


def test_an_array_whose_metadata_tensorstore_refuses_is_read_by_zarr_python(tmp_path):
    """Published stores carry members tensorstore's zarr driver does not know, such as a checksum on a zstd compressor."""
    import json

    import numpy as np
    import zarr

    from organella.sources.arrays import open_store_array

    data = np.arange(6 * 8 * 10, dtype=np.uint16).reshape(6, 8, 10)
    zarr.create_array(str(tmp_path), name="labels/mito", data=data, chunks=(4, 4, 4), zarr_format=2)
    descriptor = tmp_path / "labels" / "mito" / ".zarray"
    metadata = json.loads(descriptor.read_text())
    metadata["compressor"] = {"id": "zstd", "level": 3, "checksum": False}
    descriptor.write_text(json.dumps(metadata))
    array = open_store_array(str(tmp_path), "labels/mito")
    np.testing.assert_array_equal(array.compute(), data)
    with pytest.raises(FileNotFoundError):
        open_store_array(str(tmp_path), "labels/missing")


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


def test_the_cache_size_reaches_the_store_source(tmp_path):
    manifest = tmp_path / "cell.json"
    manifest.write_text("{}")
    assert open_source(manifest, cache_bytes=123).cache_bytes == 123
    assert isinstance(open_source(manifest), StoreSource)


def test_a_read_the_network_drops_is_asked_for_again_and_a_dead_store_still_fails():
    import numpy as np
    from obstore.exceptions import GenericError

    from organella.sources.arrays import _Retried

    class Flaky:
        shape, ndim, dtype, chunks = (4, 4), 2, np.dtype("uint8"), (2, 2)

        def __init__(self, failures):
            self.failures, self.calls = failures, 0

        def __getitem__(self, index):
            self.calls += 1
            if self.calls <= self.failures:
                raise GenericError("request or response body error")
            return np.ones((2, 2), "uint8")

    recovering = Flaky(2)
    assert _Retried(recovering, attempts=4, pause=0)[0:2, 0:2].sum() == 4 and recovering.calls == 3
    dead = Flaky(99)
    with pytest.raises(GenericError):
        _Retried(dead, attempts=3, pause=0)[0:2, 0:2]
    assert dead.calls == 3


def test_cells_run_as_separate_jobs_one_manifest_each_join_into_the_report_of_the_single_run(tmp_path):
    """What the job array does: ``process`` on one cell's source.json into a shared geometry folder, then ``merge``."""
    import tifffile
    import polars as pl
    from click.testing import CliRunner
    from organella.cli import main

    root = zarr.open_group(tmp_path / "cell.zarr", mode="w")
    labels = np.zeros((12, 16, 16), np.uint16)
    labels[2:6, 3:8, 3:8] = 4
    labels[7:11, 9:14, 9:14] = 5
    array = root.create_array("labels/mito/s1", data=labels, chunks=(6, 8, 8))
    array.attrs["transform"] = {"axes": ["z", "y", "x"], "scale": [20.0, 10.0, 10.0], "units": ["nm", "nm", "nm"]}
    for name in ("cell_a", "cell_b"):
        folder = tmp_path / "cells" / name
        folder.mkdir(parents=True)
        tifffile.imwrite(folder / "cell_mask.tif", np.ones((12, 16, 16), np.uint8))
        (folder / "source.json").write_text(json.dumps({
            "store": str(tmp_path / "cell.zarr"), "scale": "s1", "crop": ":,:,:",
            "entities": {"mito": {"array": "labels/mito", "kind": "label"}},
            "masks": {"cell": "cell_mask.tif"}, "object_mask": "cell"}))
    meshes = tmp_path / "meshes"
    for name in ("cell_a", "cell_b"):
        result = CliRunner().invoke(main, ["process", str(tmp_path / "cells" / name / "source.json"), "-o", str(tmp_path / "reports" / f"{name}.parquet"),
                                           "--mesh-dir", str(meshes), "--with-mesh", "--object-noun", "cell"])
        assert result.exit_code == 0, result.output
    assert sorted(p.name for p in meshes.iterdir()) == ["cell_a", "cell_b"]
    merged = tmp_path / "merged.parquet"
    result = CliRunner().invoke(main, ["merge", str(tmp_path / "reports" / "cell_a.parquet"), str(tmp_path / "reports" / "cell_b.parquet"), "-o", str(merged)])
    assert result.exit_code == 0, result.output
    from organella.report.file import read_report
    rows, _ = read_report(merged)
    assert sorted(rows["object_id"].unique().to_list()) == ["cell_a", "cell_b"]
    assert all((meshes / name / "geometry.parquet").is_file() for name in ("cell_a", "cell_b"))
