"""Reading object folders that were not laid out for this tool.

Published segmentations are not going to be renamed to suit a reader, so the reader has to
cope: names with underscores in them, formats that are not TIFF, entities in a
``segmentations/`` subfolder named by nothing but the structure, and one volume whose ids
each mean a different structure.
"""

from pathlib import Path

import numpy as np
import pytest
import tifffile

from label_anatomy.config import AnatomyConfig
from label_anatomy.measure import load_object
from label_anatomy.measure.discovery import inspect_object_dir
from label_anatomy.measure.readers import image_stem, read_header, read_voxel_size_um

sitk = pytest.importorskip("SimpleITK")

SHAPE = (12, 24, 24)


def _blob(shape, center, radius) -> np.ndarray:
    zz, yy, xx = np.ogrid[: shape[0], : shape[1], : shape[2]]
    return ((zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2) <= radius**2


def _write_nifti(path: Path, arr: np.ndarray, spacing_mm=(1.5, 1.5, 1.5)) -> None:
    """Write (Z, Y, X) as NIfTI, whose spacing is (x, y, z)."""
    image = sitk.GetImageFromArray(arr)
    image.SetSpacing(tuple(reversed(spacing_mm)))
    sitk.WriteImage(image, str(path))


# ── Entity names with underscores in them ─────────────────────────────────────

def test_a_name_with_underscores_survives_the_prefix(tmp_path):
    # The bug this replaced: 's0011_rib_left_11_mask' parsed as the entity '11', which
    # collided with rib_right_11 and lost one of the two without an error.
    d = tmp_path / "s0011"
    d.mkdir()
    body = _blob(SHAPE, (6, 12, 12), 8)
    tifffile.imwrite(d / "s0011.tif", (body * 200).astype(np.uint8))
    for name in ("liver", "rib_left_11", "rib_right_11", "vertebrae_T5"):
        tifffile.imwrite(d / f"s0011_{name}_mask.tif", body.astype(np.uint8))

    found = inspect_object_dir(d)

    assert sorted(e.name for e in found.entities.values()) == [
        "liver", "rib_left_11", "rib_right_11", "vertebrae_t5",
    ]
    assert found.rejected == []


def test_a_file_that_is_only_the_source_name_and_a_kind_is_rejected(tmp_path):
    d = tmp_path / "s0011"
    d.mkdir()
    body = _blob(SHAPE, (6, 12, 12), 8)
    tifffile.imwrite(d / "s0011.tif", (body * 200).astype(np.uint8))
    tifffile.imwrite(d / "s0011_mask.tif", body.astype(np.uint8))
    tifffile.imwrite(d / "s0011_liver_mask.tif", body.astype(np.uint8))

    found = inspect_object_dir(d)

    assert [e.name for e in found.entities.values()] == ["liver"]
    assert "no entity name" in found.rejected[0][1]


# ── Formats that are not TIFF ─────────────────────────────────────────────────

def test_image_stem_strips_a_double_suffix():
    assert image_stem(Path("ct.nii.gz")) == "ct"
    assert image_stem(Path("liver.nii")) == "liver"
    assert image_stem(Path("sample_pm_mask.tif")) == "sample_pm_mask"


def test_nifti_header_is_read_without_the_pixels(tmp_path):
    path = tmp_path / "ct.nii.gz"
    _write_nifti(path, np.zeros(SHAPE, np.int16))

    shape, dtype = read_header(path)

    assert shape == SHAPE          # (z, y, x), not the (x, y, z) the file stores
    assert dtype == "int16"


def test_nifti_spacing_becomes_micrometres(tmp_path):
    path = tmp_path / "ct.nii.gz"
    _write_nifti(path, np.zeros(SHAPE, np.int16), spacing_mm=(2.0, 0.5, 0.5))

    assert read_voxel_size_um(path, 3) == (2000.0, 500.0, 500.0)


def test_an_object_of_nifti_files_loads(tmp_path):
    d = tmp_path / "subject"
    d.mkdir()
    body = _blob(SHAPE, (6, 12, 12), 9)
    liver = _blob(SHAPE, (6, 10, 10), 3)
    _write_nifti(d / "ct.nii.gz", (body * 200).astype(np.int16))
    _write_nifti(d / "ct_body_mask.nii.gz", body.astype(np.uint8))
    _write_nifti(d / "ct_liver_mask.nii.gz", liver.astype(np.uint8))

    record = load_object(d, AnatomyConfig(object_mask="body"))

    assert record.dim_order == "CZYX"
    assert record.meta["channel_names"] == ["body", "liver"]
    assert record.meta["voxel_size_source"] == "image-header-mm"
    assert record.meta["pixel_size_Z"] == pytest.approx(1500.0)


# ── Entities in a segmentations/ subfolder, named by the structure alone ──────

def _subfolder_object(tmp_path: Path) -> Path:
    d = tmp_path / "s0011"
    (d / "segmentations").mkdir(parents=True)
    body = _blob(SHAPE, (6, 12, 12), 9)
    _write_nifti(d / "ct.nii.gz", (body * 200).astype(np.int16))
    _write_nifti(d / "segmentations" / "body.nii.gz", body.astype(np.uint8))
    _write_nifti(d / "segmentations" / "liver.nii.gz",
                 _blob(SHAPE, (6, 9, 9), 3).astype(np.uint8))
    two = np.zeros(SHAPE, np.uint8)
    two[_blob(SHAPE, (4, 16, 16), 2)] = 1
    two[_blob(SHAPE, (9, 16, 16), 2)] = 2
    _write_nifti(d / "segmentations" / "kidneys.nii.gz", two)
    return d


def test_a_segmentations_subfolder_is_discovered_without_a_prefix(tmp_path):
    found = inspect_object_dir(_subfolder_object(tmp_path))

    assert found.source is not None and found.source.name == "ct.nii.gz"
    assert sorted(e.name for e in found.entities.values()) == ["body", "kidneys", "liver"]
    # What each one is cannot be known from the name, so discovery does not claim to.
    assert {e.kind for e in found.entities.values()} == {"auto"}


def test_what_an_auto_entity_is_comes_from_its_content(tmp_path):
    record = load_object(_subfolder_object(tmp_path), AnatomyConfig(object_mask="body"))

    kinds = dict(zip(record.meta["channel_names"], record.meta["entity_kinds"]))
    assert kinds["liver"] == "mask"      # one non-zero value
    assert kinds["kidneys"] == "label"   # two of them
    assert kinds["body"] == "mask"       # named as the boundary, so a mask by definition


def test_an_auto_entity_can_be_named_as_the_object_mask(tmp_path):
    record = load_object(_subfolder_object(tmp_path), AnatomyConfig(object_mask="body"))

    assert record.meta["object_mask_name"] == "body"
    assert record.meta["channel_names"][0] == "body"


# ── Selecting entities ────────────────────────────────────────────────────────

def test_only_the_named_entities_are_stacked(tmp_path):
    record = load_object(
        _subfolder_object(tmp_path),
        AnatomyConfig(object_mask="body", entities=frozenset({"liver"})),
    )

    # The object mask comes along whether or not it was named: everything is measured
    # relative to it.
    assert record.meta["channel_names"] == ["body", "liver"]


def test_an_entity_that_is_not_there_is_an_error_naming_what_is(tmp_path):
    with pytest.raises(FileNotFoundError, match="pancreas"):
        load_object(_subfolder_object(tmp_path),
                    AnatomyConfig(object_mask="body", entities=frozenset({"pancreas"})))


# ── One volume whose ids each mean a different structure ──────────────────────

def _labelmap_object(tmp_path: Path) -> Path:
    d = tmp_path / "case"
    d.mkdir()
    body = _blob(SHAPE, (6, 12, 12), 10)
    organs = np.zeros(SHAPE, np.uint8)
    organs[_blob(SHAPE, (5, 9, 9), 3)] = 1
    organs[_blob(SHAPE, (8, 16, 16), 2)] = 2
    organs[_blob(SHAPE, (3, 16, 8), 2)] = 5
    tifffile.imwrite(d / "case.tif", (body * 200).astype(np.uint8))
    tifffile.imwrite(d / "case_body_mask.tif", body.astype(np.uint8))
    tifffile.imwrite(d / "case_organs_label.tif", organs)
    return d


def test_a_label_map_splits_one_volume_into_an_entity_per_id(tmp_path):
    record = load_object(
        _labelmap_object(tmp_path),
        AnatomyConfig(voxel_size_um=(1.0, 1.0, 1.0), object_mask="body", label_map={1: "liver", 2: "spleen", 5: "aorta"}),
    )

    assert record.meta["channel_names"] == ["body", "aorta", "liver", "spleen"]
    assert record.meta["entity_kinds"] == ["mask", "mask", "mask", "mask"]


def test_a_label_map_ignores_ids_that_are_not_in_the_volume(tmp_path):
    record = load_object(
        _labelmap_object(tmp_path),
        AnatomyConfig(voxel_size_um=(1.0, 1.0, 1.0), object_mask="body", label_map={1: "liver", 9: "pancreas"}),
    )

    assert "pancreas" not in record.meta["channel_names"]
    assert "liver" in record.meta["channel_names"]


def test_a_label_map_and_an_entity_filter_only_materialise_what_was_asked_for(tmp_path):
    record = load_object(
        _labelmap_object(tmp_path),
        AnatomyConfig(voxel_size_um=(1.0, 1.0, 1.0), object_mask="body", label_map={1: "liver", 2: "spleen", 5: "aorta"},
                      entities=frozenset({"spleen"})),
    )

    assert record.meta["channel_names"] == ["body", "spleen"]


def test_which_entity_to_split_is_an_error_rather_than_a_guess(tmp_path):
    d = _labelmap_object(tmp_path)
    tifffile.imwrite(d / "case_vessels_label.tif",
                     _blob(SHAPE, (6, 6, 6), 2).astype(np.uint8) * 3)

    with pytest.raises(ValueError, match="--label-map-entity"):
        load_object(d, AnatomyConfig(voxel_size_um=(1.0, 1.0, 1.0), object_mask="body", label_map={1: "liver"}))


def test_naming_the_entity_to_split_resolves_it(tmp_path):
    d = _labelmap_object(tmp_path)
    tifffile.imwrite(d / "case_vessels_label.tif",
                     _blob(SHAPE, (6, 6, 6), 2).astype(np.uint8) * 3)

    record = load_object(d, AnatomyConfig(voxel_size_um=(1.0, 1.0, 1.0), object_mask="body", label_map={1: "liver"},
                                          label_map_entity="organs"))

    assert "liver" in record.meta["channel_names"]
    assert "vessels" in record.meta["channel_names"]
    assert "organs" not in record.meta["channel_names"]


# ── an object described by a manifest rather than stored as files ─────────────

zarr = pytest.importorskip("zarr")


def _local_n5(tmp_path: Path) -> Path:
    """A small N5 store laid out the way a published one is: em/ beside labels/."""
    import warnings

    store_path = tmp_path / "sample.n5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        from zarr.n5 import N5Store

        root = zarr.open(N5Store(str(store_path)), mode="w")
        body = _blob(SHAPE, (6, 12, 12), 9)
        root.create_dataset("em/fibsem/s0", data=(body * 200).astype(np.uint16),
                            chunks=(6, 12, 12))
        root.create_dataset("labels/body_seg/s0", data=body.astype(np.uint8),
                            chunks=(6, 12, 12))
        two = np.zeros(SHAPE, np.uint16)
        two[_blob(SHAPE, (4, 16, 16), 2)] = 1
        two[_blob(SHAPE, (9, 16, 16), 2)] = 2
        root.create_dataset("labels/spots_seg/s0", data=two, chunks=(6, 12, 12))
    return store_path


def _manifest(folder: Path, store: Path, **extra) -> Path:
    import json

    folder.mkdir(parents=True, exist_ok=True)
    body = {"store": str(store), "scale": "s0", "source": "em/fibsem",
            "entities": {"body": "labels/body_seg", "spots": "labels/spots_seg"},
            "voxel_size_um": [0.02, 0.01, 0.01]}
    body.update(extra)
    (folder / "source.json").write_text(json.dumps(body))
    return folder


def test_a_folder_holding_only_a_manifest_is_an_object(tmp_path):
    from label_anatomy.measure import is_object_dir

    folder = _manifest(tmp_path / "described", _local_n5(tmp_path))

    assert is_object_dir(folder)
    found = inspect_object_dir(folder)
    assert sorted(e.name for e in found.entities.values()) == ["body", "spots"]
    # Nothing about an array says whether it is one structure or many.
    assert {e.kind for e in found.entities.values()} == {"auto"}


def test_a_described_object_loads_and_says_where_its_pixels_came_from(tmp_path):
    store = _local_n5(tmp_path)
    folder = _manifest(tmp_path / "described", store)

    record = load_object(folder, AnatomyConfig(object_mask="body"))

    # Two channels, cropped to the mask's bounding box like any other object.
    assert record.data.shape[0] == 2
    assert record.data.shape[1:] <= SHAPE
    assert record.meta["channel_names"] == ["body", "spots"]
    assert record.meta["voxel_size_source"] == "manifest"
    assert record.meta["pixel_size_Z"] == pytest.approx(0.02)
    # The object row records the store and array, not the manifest's own file name.
    assert all("labels/" in origin for origin in record.meta["entity_files"])


def test_a_manifest_crop_reads_only_that_window(tmp_path):
    store = _local_n5(tmp_path)
    whole = load_object(_manifest(tmp_path / "whole", store),
                        AnatomyConfig(object_mask="body", clip=False))
    cropped = load_object(_manifest(tmp_path / "part", store, crop="0:6,0:24,0:24"),
                          AnatomyConfig(object_mask="body", clip=False))

    assert whole.data.shape[1] == SHAPE[0]
    assert cropped.data.shape[1] == 6, "the crop should halve the Z extent"


def test_a_described_entity_still_learns_its_kind_from_content(tmp_path):
    record = load_object(_manifest(tmp_path / "described", _local_n5(tmp_path)),
                         AnatomyConfig(object_mask="body"))

    kinds = dict(zip(record.meta["channel_names"], record.meta["entity_kinds"]))
    assert kinds["body"] == "mask"
    assert kinds["spots"] == "label"


def test_a_manifest_missing_the_store_is_an_error_that_says_so(tmp_path):
    import json

    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "source.json").write_text(json.dumps({"source": "em/fibsem"}))

    found = inspect_object_dir(folder)

    assert found.source is None
    assert "store" in found.errors[0]
