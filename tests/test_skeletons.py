import dask
import dask.array as da
import numpy as np
import pytest

from organella.measure.skeletons import SkeletonParams, lazy_skeleton

VOXEL = (0.1, 0.1, 0.1)


def tube(shape, points, radius):
    """A union of balls of ``radius`` voxels centred along a polyline of voxel coordinates."""
    z, y, x = np.indices(shape)
    mask = np.zeros(shape, bool)
    for (a, b) in zip(points[:-1], points[1:]):
        for t in np.linspace(0, 1, int(np.linalg.norm(np.subtract(b, a)) * 2) + 2):
            c = np.add(a, np.subtract(b, a) * t)
            mask |= (z - c[0]) ** 2 + (y - c[1]) ** 2 + (x - c[2]) ** 2 <= radius ** 2
    return mask


def traced(mask, params=SkeletonParams(), voxel=VOXEL, max_voxels=None):
    labels = mask.astype(np.uint16)
    where = np.argwhere(labels == 1)
    return dask.compute(lazy_skeleton(da.from_array(labels, chunks=16), where.min(axis=0), where.max(axis=0), 1,
                                      voxel, params, max_voxels))[0]


def test_a_straight_tube_is_one_branch_as_long_as_the_tube_and_straight():
    result = traced(tube((16, 16, 60), [(8, 8, 6), (8, 8, 53)], 3.5))
    assert result.branches == 1
    assert result.length_um == pytest.approx((47 + 2 * 3.5) * 0.1, rel=0.06)   # a skeleton runs into the rounded ends
    assert 1.0 <= result.tortuosity < 1.05
    assert len(result.tips) == 2


def test_a_y_has_three_branches_and_three_tips():
    result = traced(tube((40, 40, 60), [(20, 20, 5), (20, 20, 30)], 3.5) | tube((40, 40, 60), [(20, 20, 30), (6, 6, 52)], 3.5)
                    | tube((40, 40, 60), [(20, 20, 30), (34, 34, 52)], 3.5))
    assert result.branches == 3 and len(result.tips) == 3


def test_a_bent_tube_is_less_straight_than_a_straight_one():
    straight = traced(tube((30, 30, 60), [(15, 15, 5), (15, 15, 55)], 3.5))
    bent = traced(tube((50, 30, 60), [(5, 15, 5), (5, 15, 30), (40, 15, 30), (40, 15, 55)], 3.5))
    assert bent.tortuosity > straight.tortuosity


class Hand:
    """A skeleton written down by hand: a 5-vertex path with a stub off its middle."""

    def __init__(self, stub_at_um):
        self.vertices = np.array([[0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3], [0, 0, 4], [stub_at_um, 0, 2]], dtype=np.float32)
        self.edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4], [2, 5]])
        self.radii = np.full(6, 1.0)


def test_a_stub_shorter_than_the_pruning_reach_is_cut_and_a_longer_branch_is_kept():
    from organella.measure.skeletons import prune_spurs
    short, long = prune_spurs(Hand(0.5), 2.0), prune_spurs(Hand(3.0), 2.0)
    assert len(short.vertices) == 5 and len(long.vertices) == 6
    assert len(prune_spurs(Hand(0.5), 0.0).vertices) == 6      # 0 keeps everything


def test_an_instance_over_the_size_cap_is_not_measured_and_a_speck_has_no_branches():
    big = traced(tube((16, 16, 60), [(8, 8, 6), (8, 8, 53)], 3.5), max_voxels=100)
    assert np.isnan(big.branches) and np.isnan(big.length_um)
    speck = np.zeros((16, 16, 16), bool)
    speck[8, 8, 8] = True
    result = traced(speck)
    assert result.branches == 0 and result.length_um == 0.0 and np.isnan(result.tortuosity)


def test_a_plane_instance_is_thinned_to_one_branch():
    labels = np.zeros((30, 60), np.uint16)
    labels[12:17, 5:55] = 1
    where = np.argwhere(labels == 1)
    result = dask.compute(lazy_skeleton(da.from_array(labels, chunks=16), where.min(axis=0), where.max(axis=0), 1,
                                        (0.1, 0.1), SkeletonParams(), None))[0]
    assert result.branches == 1 and result.length_um == pytest.approx(4.9, rel=0.1)
