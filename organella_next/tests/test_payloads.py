import struct

import numpy as np
import pytest

from organella.geometry.payloads import (
    NARROW_INDEX_LIMIT, ellipsoid_payload, payload_counts, quantised_payload, skeleton_payload, smooth_centre_line,
    tube_payload,
)


def decode(payload, per_index=3, with_radii=False):
    """What the viewer's decoder does with a vertex payload."""
    n_vertices, n_indices = struct.unpack_from("<II", payload, 0)
    low, scale = np.frombuffer(payload, np.float32, 3, 8), np.frombuffer(payload, np.float32, 3, 20)
    quantised = np.frombuffer(payload, np.uint16, n_vertices * 3, 32).reshape(-1, 3)
    wide = np.dtype(np.uint16 if n_vertices < NARROW_INDEX_LIMIT else np.uint32)
    start = 32 + n_vertices * 6
    count = n_indices * per_index          # the header counts faces or segments, the decoder multiplies
    indices = np.frombuffer(payload, wide, count, start)
    radii = np.frombuffer(payload, np.float32, n_vertices, start + count * wide.itemsize) if with_radii else None
    return low + quantised / 65535.0 * scale, indices, radii


def test_a_quantised_payload_decodes_to_within_one_step_and_counts_come_from_the_header():
    rng = np.random.default_rng(1)
    vertices = (rng.random((200, 3)) * [3.0, 5.0, 2.0] + [1.0, -2.0, 0.5]).astype(np.float32)
    faces = rng.integers(0, 200, (50, 3)).astype(np.uint32)
    payload = quantised_payload(vertices, faces)
    decoded, indices, _ = decode(payload)
    np.testing.assert_allclose(decoded, vertices, atol=5.0 / 65535)
    np.testing.assert_array_equal(indices, faces.ravel())
    assert payload_counts(payload) == (200, 50) and payload_counts(b"") == (None, None)


def test_indices_are_four_bytes_only_when_the_vertex_count_needs_it():
    few = quantised_payload(np.zeros((10, 3), np.float32), np.zeros((4, 3), np.uint32))
    many = quantised_payload(np.zeros((70000, 3), np.float32), np.zeros((4, 3), np.uint32))
    assert len(few) == 32 + 10 * 6 + 12 * 2 and len(many) == 32 + 70000 * 6 + 12 * 4


def test_a_skeleton_overlays_its_instance_in_xyz_and_a_plane_gets_z_zero():
    z_y_x = np.array([[0, 1, 2], [3, 4, 5]], np.float32)
    decoded, _, _ = decode(skeleton_payload(z_y_x, np.array([[0, 1]])), per_index=2)
    np.testing.assert_allclose(decoded, [[2, 1, 0], [5, 4, 3]], atol=1e-3)
    flat, _, _ = decode(skeleton_payload(np.array([[1, 2], [3, 4]], np.float32), np.array([[0, 1]])), per_index=2)
    np.testing.assert_allclose(flat, [[2, 1, 0], [4, 3, 0]], atol=1e-3)
    assert skeleton_payload(z_y_x[:1], np.zeros((0, 2))) == b""


def test_an_ellipsoid_is_sixty_bytes_and_a_collapsed_moment_is_refused():
    payload = ellipsoid_payload((1, 2, 3), (2, 4, 6), np.eye(3).ravel())
    assert len(payload) == 60 and struct.unpack("<15f", payload)[3:6] == (1.0, 2.0, 3.0)
    assert ellipsoid_payload((1, 2, 3), (0, 4, 6), np.eye(3).ravel()) == b""
    assert ellipsoid_payload((1, 2, 3), (1e-7, 18.4, 78.4), np.eye(3).ravel()) == b""


def test_smoothing_moves_only_nodes_with_two_neighbours():
    vertices = np.array([[0, 0, 0], [1, 1, 0], [2, 0, 0], [3, 1, 0], [4, 0, 0]], np.float32)
    edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4]])
    smoothed, _ = smooth_centre_line(vertices, edges, np.ones(5, np.float32))
    np.testing.assert_array_equal(smoothed[[0, 4]], vertices[[0, 4]])          # the tips stay
    assert np.ptp(smoothed[:, 1]) < np.ptp(vertices[:, 1])                      # the zig-zag is flatter


def test_a_tube_is_the_skeleton_payload_with_a_radius_per_node():
    vertices = np.array([[0, 0, 0], [0, 0, 1], [0, 0, 2]], np.float32)
    payload = tube_payload(vertices, np.array([[0, 1], [1, 2]]), np.array([0.2, 0.3, 0.4]))
    decoded, indices, radii = decode(payload, per_index=2, with_radii=True)
    np.testing.assert_allclose(decoded[:, 0], [0, 1, 2], atol=1e-3)
    np.testing.assert_allclose(radii, [0.2, 0.3, 0.4], atol=1e-6)
    assert tube_payload(vertices, np.array([[0, 1]]), np.array([0.2])) == b""        # a radius per node, or nothing
