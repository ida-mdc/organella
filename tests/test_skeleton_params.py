"""How coarse a curve skeleton is: relative to the data, with nothing to tune by hand."""

import numpy as np
import pytest

from organella.analysis.shapes import (SkeletonParams, _teasar_params, compute_curve_skeletons,
                                       prune_spurs, skeleton_graph_metrics)
from organella.config import RunConfig


class _Skeleton:
    def __init__(self, vertices, edges, radii):
        self.vertices = np.asarray(vertices, dtype=np.float32)
        self.edges = np.asarray(edges, dtype=np.uint32)
        self.radii = np.asarray(radii, dtype=np.float32)
        self.vertex_types = np.zeros(len(vertices), dtype=np.uint8)

    def cable_length(self):
        a, b = self.vertices[self.edges[:, 0]], self.vertices[self.edges[:, 1]]
        return float(np.linalg.norm(a - b, axis=1).sum())


def _y(spur_length: float, radius: float = 1.0):
    """A long straight path along x with a spur of the given length off its middle."""
    xs = np.arange(0, 21, dtype=float)
    path = [(0, 0, x) for x in xs]
    edges = [(i, i + 1) for i in range(20)]
    spur = [(s, 0, 10) for s in np.arange(1, spur_length + 1e-9, 1.0)]
    vertices = path + spur
    edges += [(10, 21)] + [(21 + i, 22 + i) for i in range(len(spur) - 1)]
    return _Skeleton(vertices, edges, [radius] * len(vertices))


def test_a_spur_shorter_than_the_radius_reach_is_cut():
    skeleton = prune_spurs(_y(spur_length=2, radius=2.0), factor=2.0)
    assert len(skeleton.vertices) == 21 and len(skeleton.radii) == 21
    assert skeleton_graph_metrics(skeleton)["branches"] == 1


def test_a_spur_longer_than_that_is_a_branch_and_stays():
    skeleton = prune_spurs(_y(spur_length=6, radius=2.0), factor=2.0)
    assert len(skeleton.vertices) == 27
    assert skeleton_graph_metrics(skeleton)["branches"] == 3


def test_a_wider_tube_forgives_a_longer_stub():
    # The same 3-long stub is a bulge on a tube of radius 2 and a branch on a thin one.
    assert len(prune_spurs(_y(3, radius=2.0), 2.0).vertices) == 21
    assert len(prune_spurs(_y(3, radius=0.5), 2.0).vertices) == 24


def test_zero_keeps_every_spur():
    assert len(prune_spurs(_y(2, radius=2.0), 0).vertices) == 23


def test_an_unbranched_path_is_left_alone():
    path = _Skeleton([(0, 0, x) for x in range(5)], [(i, i + 1) for i in range(4)], [9] * 5)
    assert len(prune_spurs(path, 2.0).vertices) == 5


def test_a_junction_of_short_spurs_keeps_a_path_through_it():
    vertices = [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0), (0, 0, -1)]
    star = _Skeleton(vertices, [(0, 1), (0, 2), (0, 3), (0, 4)], [5] * 5)
    pruned = prune_spurs(star, 2.0)
    assert len(pruned.edges) >= 1
    assert skeleton_graph_metrics(pruned)["length_um"] > 0


def test_the_reach_follows_the_resolution_the_data_has():
    coarse = _teasar_params(SkeletonParams(), (0.05, 0.02, 0.02))
    fine = _teasar_params(SkeletonParams(), (0.008, 0.008, 0.008))
    # Staircase noise is a few voxels whatever a voxel is, so the constant is in voxels.
    assert coarse["const"] == pytest.approx(6 * 0.05)
    assert fine["const"] == pytest.approx(6 * 0.008)
    assert coarse["scale"] == fine["scale"] == 3.0


def test_the_run_settings_reach_the_tracer():
    params = RunConfig(skeleton_scale=2.0, skeleton_const_voxels=4.0,
                       skeleton_prune=0.0).skeleton_params()
    assert (params.scale, params.const_voxels, params.prune) == (2.0, 4.0, 0.0)


def _rough_tube(radius: float, length: int, seed: int = 0) -> np.ndarray:
    zz, yy, xx = np.mgrid[0:2 * radius + 6, 0:2 * radius + 6, 0:length]
    centre = radius + 3
    rng = np.random.default_rng(seed)
    wobble = rng.uniform(-1.2, 1.2, size=(1, 1, length))
    inside = (zz - centre) ** 2 + (yy - centre) ** 2 <= (radius + wobble) ** 2
    return inside.astype(np.int32)


@pytest.mark.parametrize("voxel", [0.008, 0.016, 0.04])
def test_a_rough_tube_is_one_centre_line_at_any_resolution(voxel):
    tube = _rough_tube(radius=8, length=120)
    skeleton = compute_curve_skeletons(tube, (voxel,) * 3)[1]
    metrics = skeleton_graph_metrics(skeleton)
    assert metrics["branches"] <= 3
    assert metrics["length_um"] == pytest.approx(120 * voxel, rel=0.15)
