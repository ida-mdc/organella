import json

import numpy as np
import polars as pl
import pytest
import SimpleITK as sitk
import tifffile

from organella.app.process import process
from organella.app.settings import Settings
from organella.core import Kind
from organella.measure import DEFAULT
from organella.sinks import ParquetSink
from organella.sources import FolderSource
from organella.sources.label_map import read_label_map


def write_nifti(path, array, spacing_mm=(2.0, 0.5, 0.5)):
    image = sitk.GetImageFromArray(array)
    image.SetSpacing(tuple(reversed(spacing_mm)))   # ITK orders x, y, z
    sitk.WriteImage(image, str(path))


def only(source):
    (ref,) = source.samples()
    return ref.open()


def test_a_nifti_object_is_read_with_its_spacing_taken_as_millimetres(tmp_path):
    folder = tmp_path / "subject"
    folder.mkdir()
    write_nifti(folder / "subject.nii.gz", np.zeros((6, 20, 24), np.uint8))
    labels = np.zeros((6, 20, 24), np.uint16)
    labels[1:4, 5:9, 5:9] = 1
    write_nifti(folder / "subject_vessel_label.nii.gz", labels)
    sample = only(FolderSource(tmp_path))
    assert {n: s.kind for n, s in sample.structures.items()} == {"vessel": Kind.LABEL}
    assert sample.voxel_size.um == pytest.approx((2000.0, 500.0, 500.0)) and sample.shape == (6, 20, 24)


def test_files_in_a_segmentation_folder_are_named_by_themselves_and_read_as_label_or_mask_from_their_content(tmp_path):
    folder = tmp_path / "ct"
    (folder / "segmentations").mkdir(parents=True)
    write_nifti(folder / "ct.nii.gz", np.zeros((5, 12, 12), np.uint8))
    single = np.zeros((5, 12, 12), np.uint8)
    single[1:3, 2:5, 2:5] = 1
    several = np.zeros((5, 12, 12), np.uint8)
    several[1:3, 2:5, 2:5], several[1:3, 7:10, 7:10] = 4, 9
    write_nifti(folder / "segmentations" / "liver.nii.gz", single)
    write_nifti(folder / "segmentations" / "vessels.nii.gz", several)
    sample = only(FolderSource(tmp_path))
    assert {n: s.kind for n, s in sample.structures.items()} == {"liver": Kind.MASK, "vessels": Kind.LABEL}


def test_a_three_dimensional_file_of_one_slice_is_a_plane(tmp_path):
    folder = tmp_path / "slide"
    folder.mkdir()
    plane = np.zeros((1, 30, 30), np.uint16)
    plane[0, 5:12, 5:12] = 1
    write_nifti(folder / "slide.nii.gz", np.zeros((1, 30, 30), np.uint8), (1.0, 0.1, 0.1))
    write_nifti(folder / "slide_cell_label.nii.gz", plane, (1.0, 0.1, 0.1))
    sample = only(FolderSource(tmp_path))
    assert sample.shape == (30, 30) and sample.voxel_size.um == pytest.approx((100.0, 100.0))


@pytest.fixture
def organs(tmp_path):
    folder = tmp_path / "scan"
    folder.mkdir()
    volume = np.zeros((6, 24, 24), np.uint16)
    volume[1:4, 2:8, 2:8], volume[1:4, 12:18, 12:18], volume[4:6, 20:23, 1:4] = 1, 2, 3
    write_nifti(folder / "scan.nii.gz", np.zeros_like(volume, np.uint8))
    write_nifti(folder / "scan_organs_label.nii.gz", volume)
    return tmp_path


def test_a_label_map_splits_one_volume_into_a_mask_per_named_id_that_is_present(organs):
    names = {1: "Liver", 2: "left-spleen", 9: "ghost"}
    sample = only(FolderSource(organs, label_map=names))
    assert {n: s.kind for n, s in sample.structures.items()} == {"liver": Kind.MASK, "left_spleen": Kind.MASK}
    assert int(sample.structures["liver"].data.sum().compute()) == 3 * 6 * 6


def test_a_label_map_naming_nothing_present_is_an_error_that_shows_the_ids_there_are(organs):
    with pytest.raises(ValueError, match=r"names no id|named no id present.*\[1, 2, 3\]"):
        only(FolderSource(organs, label_map={7: "nothing"}))


def test_a_label_map_needs_to_be_told_which_structure_when_there_are_several(organs):
    write_nifti(organs / "scan" / "scan_other_label.nii.gz", np.ones((6, 24, 24), np.uint16))
    with pytest.raises(ValueError, match="--label-map-entity"):
        only(FolderSource(organs, label_map={1: "liver"}))
    assert "liver" in only(FolderSource(organs, label_map={1: "liver"}, label_map_entity="organs")).structures


def test_a_split_volume_is_measured_like_any_other_object_and_the_map_file_is_read(organs, tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"1": "liver", "2": "spleen"}))
    assert read_label_map(tmp_path / "map.json") == {1: "liver", 2: "spleen"}
    out = tmp_path / "r.parquet"
    failures = process(FolderSource(organs, label_map=read_label_map(tmp_path / "map.json")), DEFAULT, ParquetSink(out),
                       Settings(reach_um=3000.0, chunk_edge=128, contacts=False))
    assert failures == {}
    entities = pl.read_parquet(out).filter(pl.col("row_type") == "entity")
    assert sorted(entities["entity_name"]) == ["liver", "spleen"]


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


def test_a_compressed_and_an_uncompressed_tiff_both_open_and_slice(tmp_path):
    from organella.sources.arrays import open_tiff

    volume = np.random.default_rng(4).integers(0, 9, (6, 20, 24)).astype(np.uint16)
    for name, kwargs in (("plain.tif", {}), ("packed.tif", {"compression": "zlib"})):
        tifffile.imwrite(tmp_path / name, volume, **kwargs)
        np.testing.assert_array_equal(open_tiff(tmp_path / name)[1:4, 5:9].compute(), volume[1:4, 5:9])


def test_only_the_named_structures_are_measured_and_the_object_mask_is_kept(object_folder):
    from organella.sources import FolderSource
    (ref,) = FolderSource(object_folder.parent, object_mask="cell", entities=frozenset({"mito"})).samples()
    assert sorted(ref.open().structures) == ["cell", "mito"]
    with pytest.raises(ValueError, match="nothing.*does not have|--entities names nothing"):
        (FolderSource(object_folder.parent, object_mask="cell", entities=frozenset({"nothing"})).samples().__iter__().__next__()).open()
