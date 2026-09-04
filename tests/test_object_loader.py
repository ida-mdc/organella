import pytest

from label_anatomy.measure import find_object_dirs, is_object_dir, load_object
from synthetic import VOXEL_SIZE_UM, make_object, make_dataset


@pytest.fixture
def object_dir(tmp_path):
    make_object(tmp_path / "object_a", prefix="sample_a")
    return tmp_path / "object_a"


def test_loads_object_folder_as_one_czyx_stack(object_dir):
    record = load_object(object_dir)

    assert record.dim_order == "CZYX"
    assert record.data.shape[0] == 3          # pm, nucleus, mito
    assert record.meta["object_id"] == "object_a"
    assert record.meta["object_mask_name"] == "pm"
    assert record.meta["n_entities"] == 3


def test_the_object_mask_is_the_first_channel_then_alphabetical(object_dir):
    record = load_object(object_dir)

    assert record.meta["channel_names"] == ["pm", "mito", "nucleus"]
    assert record.meta["entity_kinds"] == ["mask", "label", "mask"]


def test_voxel_size_read_from_tiff_metadata(object_dir):
    record = load_object(object_dir)

    assert record.meta["voxel_size_source"] == "tiff-metadata"
    assert record.meta["pixel_size_Z"] == pytest.approx(VOXEL_SIZE_UM[0])
    assert record.meta["pixel_size_Y"] == pytest.approx(VOXEL_SIZE_UM[1])
    assert record.meta["pixel_size_X"] == pytest.approx(VOXEL_SIZE_UM[2])


def test_voxel_size_override_from_env(object_dir, monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_VOXEL_SIZE_UM", "0.5,0.25,0.25")
    record = load_object(object_dir)

    assert record.meta["voxel_size_source"] == "config"
    assert record.meta["pixel_size_Z"] == pytest.approx(0.5)


def test_the_region_is_cropped_to_the_object_mask_bbox(object_dir):
    record = load_object(object_dir)

    # The membrane ellipsoid is inset from the image border, so the analysed volume
    # is smaller than the source image and object_shape reports what was measured.
    assert list(record.data.shape[1:]) == record.meta["object_shape"]
    assert record.data.shape[1:] < (20, 40, 40)


# ── Folder discovery ──────────────────────────────────────────────────────────

def test_an_object_folder_is_one_object(object_dir):
    # Recognising the folder is what stops the TIFFs inside it from being read as objects
    # of their own.
    assert is_object_dir(object_dir) is True


def test_folders_that_only_contain_objects_are_not_objects(tmp_path):
    root = make_dataset(tmp_path / "experiment")

    assert is_object_dir(root) is False
    assert is_object_dir(root / "control") is False
    assert is_object_dir(root / "control" / "object_a") is True
    # ...and the batch is found by walking into them.
    assert [d.name for d in find_object_dirs(root)] == [
        "object_a", "object_b", "object_c", "object_d"]


def test_folder_without_entity_volumes_is_not_an_object(tmp_path, object_dir):
    lonely = tmp_path / "just_an_image"
    lonely.mkdir()
    (object_dir / "sample_a.tif").replace(lonely / "sample_a.tif")

    assert is_object_dir(lonely) is False


# ── which mask bounds the object ──────────────────────────────────────────────

def test_the_named_mask_is_the_one_that_bounds_the_object(object_dir, monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", "nucleus")

    record = load_object(object_dir)

    # Everything downstream follows: the region is cropped to the nucleus, and polarity is
    # measured from its centroid.
    assert record.meta["object_mask_name"] == "nucleus"


def test_cropping_follows_the_named_mask(object_dir, monkeypatch):
    to_membrane = load_object(object_dir).meta["object_shape"]
    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", "nucleus")

    to_nucleus = load_object(object_dir).meta["object_shape"]

    # The nucleus is a small blob inside the membrane, so naming it shrinks the analysed
    # volume - which is the point: the object mask decides what "inside" means.
    assert all(n < m for n, m in zip(to_nucleus, to_membrane))


def test_naming_a_mask_the_folder_does_not_have_is_an_error(object_dir, monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", "cortex")

    with pytest.raises(FileNotFoundError, match="No mask named 'cortex'"):
        load_object(object_dir)


def test_a_label_entity_cannot_be_the_object_mask(object_dir, monkeypatch):
    # mito is instance-segmented, so it is not a boundary: asking for it is the same
    # mistake as asking for a mask that is not there, and gets the same refusal.
    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", "mito")

    with pytest.raises(FileNotFoundError, match="No mask named 'mito'"):
        load_object(object_dir)


def test_naming_no_mask_measures_the_entities_where_they_lie(object_dir, monkeypatch):
    """No object mask is a choice, not an omission.

    Nothing is guessed either way - a mask called "pm" is no more self-explanatory than one
    called "cortex" - so without a name nothing bounds the object, and the columns that need
    a boundary are simply not filled.
    """
    monkeypatch.delenv("LABEL_ANATOMY_OBJECT_MASK", raising=False)

    record = load_object(object_dir)

    assert record.meta["object_mask_name"] is None
    assert not any(k.startswith("object_center_") for k in record.meta), \
        "polarity has no origin without an object mask, so it must not claim one"


def test_without_a_mask_nothing_is_cropped_or_clipped_away(tmp_path, monkeypatch):
    """The same folder, with and without a mask named: the full field versus the object."""
    import numpy as np

    folder = _object_with_something_outside(tmp_path)
    monkeypatch.setenv("LABEL_ANATOMY_VOXEL_SIZE_UM", "0.1,0.02,0.02")

    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", "pm")
    bounded = load_object(folder)
    monkeypatch.delenv("LABEL_ANATOMY_OBJECT_MASK", raising=False)
    whole = load_object(folder)

    # Cropped to the mask's bounding box when there is one, the full field when there is not.
    assert bounded.data.shape[1:] != whole.data.shape[1:]
    assert whole.data.shape[1:] == (12, 40, 40)

    def labels(record):
        names = list(record.meta["channel_names"])
        c = record.dim_order.index("C")
        vol = np.take(record.data, names.index("mito"), axis=c)
        return set(int(v) for v in np.unique(vol) if v)

    assert labels(bounded) == {1}      # clipped to the object
    assert labels(whole) == {1, 2}     # nothing to clip to


# ── clipping to the object mask ──────────────────────────────────────────────
#
# Naming a mask that bounds the object and then measuring what lies outside it is not what
# --object-mask says. A field of view often holds neighbouring cells: on one real alpha cell,
# 3678 of 8800 granules were entirely outside the plasma membrane and were all being counted.

def _object_with_something_outside(root):
    """An object whose mask holds instance 1, with instance 2 outside it but inside its bbox."""
    import numpy as np, tifffile
    d = root / "cell"; d.mkdir(parents=True)
    shape = (12, 40, 40)
    mask = np.zeros(shape, dtype=np.uint8)
    mask[2:10, 4:20, 4:20] = 1     # the object itself
    mask[3, 34, 18] = 1            # one voxel that stretches its bbox past instance 2
    labels = np.zeros(shape, dtype=np.uint16)
    labels[4:8, 8:14, 8:14] = 1    # inside the mask
    labels[4:8, 26:32, 8:14] = 2   # outside the mask, inside its bounding box
    tifffile.imwrite(d / "cell.tif", np.zeros(shape, np.uint8))
    tifffile.imwrite(d / "cell_pm_mask.tif", mask)
    tifffile.imwrite(d / "cell_mito_label.tif", labels)
    return d


def _labels_seen(folder, no_clip):
    import os, numpy as np
    os.environ["LABEL_ANATOMY_OBJECT_MASK"] = "pm"
    os.environ["LABEL_ANATOMY_VOXEL_SIZE_UM"] = "0.1,0.02,0.02"
    os.environ.pop("LABEL_ANATOMY_NO_CLIP", None)
    if no_clip:
        os.environ["LABEL_ANATOMY_NO_CLIP"] = "1"
    record = load_object(folder)
    c = record.dim_order.index("C")
    names = list(record.meta["channel_names"])
    mito = np.take(record.data, names.index("mito"), axis=c)
    return set(int(v) for v in np.unique(mito) if v)


def test_clipping_is_on_by_default_so_what_is_outside_is_not_measured(tmp_path):
    folder = _object_with_something_outside(tmp_path)
    assert _labels_seen(folder, no_clip=False) == {1}, \
        "an instance outside the object mask was still measured"


def test_no_clip_keeps_what_lies_outside_the_mask(tmp_path):
    folder = _object_with_something_outside(tmp_path)
    assert _labels_seen(folder, no_clip=True) == {1, 2}
