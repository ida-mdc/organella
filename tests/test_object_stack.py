"""The object the measurers are handed: what it exposes, and what it refuses.

Every measurement here is cross-entity or whole-region, so "this is a whole object" is an
invariant of the type rather than a check each measurer repeats. It used to be repeated,
three times, in three slightly different spellings.
"""

import numpy as np
import pytest

from conftest import object_stack

SHAPE = (10, 20, 20)
VOXEL = (0.1, 0.02, 0.02)


def _volume(label: int = 1) -> np.ndarray:
    vol = np.zeros(SHAPE, dtype=np.int32)
    vol[2:5, 2:5, 2:5] = label
    return vol


def test_a_spatial_fragment_is_refused():
    with pytest.raises(ValueError, match="measured whole"):
        object_stack({"mito": (_volume(), "label")},
                     voxel_size=VOXEL, object_shape=[10, 40, 40])


def test_an_entity_that_is_not_in_the_data_is_refused():
    """Splitting along C is as damaging as splitting the region: a pair spans entities."""
    stack = object_stack({"mito": (_volume(), "label")}, voxel_size=VOXEL)
    named_one_too_many = {**stack.meta, "channel_names": ["mito", "nucleus"]}

    with pytest.raises(ValueError, match="fragment"):
        type(stack)(stack.data, stack.dim_order, named_one_too_many)


def test_what_the_object_knows_about_itself():
    stack = object_stack(
        {"pm": (np.ones(SHAPE, np.int32), "mask"), "mito": (_volume(), "label")},
        voxel_size=VOXEL)

    assert stack.object_id == "object_a"
    assert stack.entity_names == ["pm", "mito"]
    assert stack.kinds == {"pm": "mask", "mito": "label"}
    assert stack.label_names == ["mito"]          # a mask is one structure, not one instance
    assert stack.object_mask_name == "pm"
    assert stack.sample_size == VOXEL
    assert stack.spatial_dims == 3


def test_an_entity_is_a_view_not_a_copy():
    stack = object_stack({"mito": (_volume(), "label")}, voxel_size=VOXEL)

    # np.take would copy, which on a real channel is a gigabyte per entity.
    assert stack.volume(0).base is stack.data
    assert stack.entity(0).name == "mito"
    assert stack.entity(0).volume.max() == 1


def test_a_plane_is_a_plane_and_not_a_volume_one_voxel_deep():
    image = np.zeros((20, 20), np.int32)
    image[2:5, 2:5] = 1
    stack = object_stack({"mito": (image, "label")}, voxel_size=(0.02, 0.02))

    assert stack.dim_order == "CYX"
    assert stack.spatial_dims == 2
    assert stack.sample_size == (0.02, 0.02)
