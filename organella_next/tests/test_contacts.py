import dask.array as da
import numpy as np
from scipy import ndimage

from organella.core import VoxelSize
from organella.measure.contacts import lazy_gaps, merged
from organella.measure.windows import compute_in_batches


def brute_force(labels, spacing, max_gap):
    """The smallest voxel-to-voxel distance of every pair of instances, from one transform per instance."""
    found = {}
    ids = [i for i in np.unique(labels) if i]
    for a in ids:
        distance = ndimage.distance_transform_edt(labels != a, sampling=spacing)
        for b in ids:
            if b > a and (gap := distance[labels == b].min()) <= max_gap:
                found[(int(a), int(b))] = gap
    return found


def test_the_gaps_between_instances_equal_a_transform_per_instance_across_chunk_borders():
    labels = np.zeros((30, 40, 40), np.uint16)
    labels[2:8, 3:12, 3:12] = 1
    labels[8:14, 3:12, 3:12] = 2          # shares a face with 1
    labels[10:16, 14:20, 3:12] = 3        # a few voxels from 2
    labels[22:28, 30:38, 30:38] = 4       # far from everything
    labels[22:28, 25:29, 30:38] = 5       # 1-3 voxels from 4
    voxel_size, max_gap = VoxelSize((0.1, 0.05, 0.05)), 0.4
    data = da.from_array(labels, chunks=9)
    lazies = []
    for label in np.unique(labels)[1:]:
        where = np.argwhere(labels == label)
        lazies.append(lazy_gaps(data, where.min(axis=0), where.max(axis=0), int(label), voxel_size, max_gap))
    table = merged(compute_in_batches(lazies, batch=3), "m")
    got = {(a, b): gap for a, b, gap in zip(table["contact_label_a"], table["contact_label_b"], table["contact_gap_um"])}
    expected = brute_force(labels, voxel_size.um, max_gap)
    assert got.keys() == expected.keys() and got
    np.testing.assert_allclose([got[k] for k in expected], list(expected.values()), rtol=1e-6)


def test_no_pairs_gives_an_empty_table_with_its_columns():
    assert merged([], "m").columns == ["contact_entity", "contact_label_a", "contact_label_b", "contact_gap_um"]
