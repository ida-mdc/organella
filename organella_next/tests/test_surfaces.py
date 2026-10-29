import numpy as np
import pytest

from organella.geometry.surfaces import choose_surface, generate_mesh, generate_outline, instance_sigma, sigma_for_shape
from test_payloads import decode


def enclosed_volume(vertices, faces):
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    return abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def test_a_sphere_is_meshed_to_about_its_own_volume():
    z, y, x = np.indices((30, 30, 30))
    binary = (z - 15) ** 2 + (y - 15) ** 2 + (x - 15) ** 2 <= 10 ** 2
    voxel = (0.1, 0.1, 0.1)
    payload = generate_mesh(binary, (0, 0, 0), voxel)
    vertices, indices, _ = decode(payload)
    faces = indices.reshape(-1, 3).astype(np.int64)
    expected = binary.sum() * 0.1 ** 3
    assert enclosed_volume(vertices, faces) == pytest.approx(expected, rel=0.08)


def test_the_mesh_sits_where_the_instance_is_in_the_volume_frame():
    binary = np.zeros((20, 20, 20), bool)
    binary[5:15, 5:15, 5:15] = True
    vertices, _, _ = decode(generate_mesh(binary, (100, 200, 300), (0.1, 0.2, 0.3), smooth_sigma=0))
    low, high = vertices.min(axis=0), vertices.max(axis=0)       # x, y, z in µm
    assert low[0] == pytest.approx((300 + 5) * 0.3, abs=0.4) and high[2] == pytest.approx((100 + 15) * 0.1, abs=0.2)


def test_a_speck_gets_no_mesh_and_a_square_gets_one_closed_outline():
    assert generate_mesh(np.ones((2, 2, 1), bool), (0, 0, 0), (0.1,) * 3) == b""
    square = np.zeros((20, 20), bool)
    square[5:15, 5:15] = True
    vertices, indices, _ = decode(generate_outline(square, (0, 0), (0.1, 0.1)), per_index=2)
    pairs = indices.reshape(-1, 2)
    assert len(vertices) == len(pairs) and sorted(pairs[:, 1].tolist()) == list(range(len(pairs)))   # one closed ring
    assert np.allclose(vertices[:, 2], 0, atol=1e-3) and vertices[:, 0].min() == pytest.approx(0.45, abs=0.06)


@pytest.mark.parametrize("sphericity,aspect,branches,skeleton,expected", [
    (0.9, 1.2, 1, True, "ellipsoid"), (0.9, 1.2, None, False, "ellipsoid"),
    (0.5, 6.0, 1, True, "tube"), (0.5, 6.0, 1, False, "mesh"), (0.5, 6.0, 0, True, "mesh"),
    (0.9, 3.5, 1, True, "tube"), (0.9, 3.5, 1, False, "mesh"), (0.4, 1.5, 3, True, "mesh"),
    (float("nan"), 1.2, 1, True, "mesh"), (0.9, 0.0, 1, True, "mesh"),
])
def test_the_surface_kind_follows_the_measured_shape(sphericity, aspect, branches, skeleton, expected):
    assert choose_surface(sphericity, aspect, branches, skeleton) == expected


def test_smoothing_scales_with_roundness_and_zero_means_none():
    assert sigma_for_shape(1.0, 0.6) > sigma_for_shape(0.2, 0.1)
    assert sigma_for_shape(float("nan"), 0.5) == pytest.approx(0.9)
    assert instance_sigma(0, 1.0, 0.6) == 0.0 and instance_sigma((0.7, 0, 0.7), 1.0, 0.6)[1] == 0.0


def test_meshes_made_in_threads_are_the_meshes_made_one_at_a_time():
    """Mesh decimation shares state between threads: without a lock around it some meshes came back wrong."""
    from concurrent.futures import ThreadPoolExecutor

    rng = np.random.default_rng(11)
    masks = []
    for _ in range(64):
        n = int(rng.choice([30, 44, 60]))
        z, y, x = np.indices((n, n, n))
        radius, stretch = float(rng.uniform(0.25, 0.42) * n), float(rng.choice([1.0, 1.6]))
        masks.append((((z - n / 2) / stretch) ** 2 + (y - n / 2) ** 2 + (x - n / 2) ** 2 <= radius ** 2) | (rng.random((n, n, n)) > 0.999))
    job = lambda mask: generate_mesh(mask, (0, 0, 0), (0.03, 0.032, 0.032))
    alone = [job(m) for m in masks]
    for _ in range(3):
        with ThreadPoolExecutor(8) as pool:
            assert list(pool.map(job, masks)) == alone
