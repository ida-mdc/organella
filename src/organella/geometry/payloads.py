"""The binary payloads the viewer draws from: a quantised mesh or line set, an ellipsoid, a tube.

Every vertex payload is ``[uint32 nV][uint32 nIdx][float32x3 min_xyz][float32x3 scale_xyz]`` followed by the vertices
as uint16 on that box, then the indices; an ellipsoid is fifteen float32. Vertices are µm, in x, y, z order.
"""
from __future__ import annotations

import math
import struct
from typing import Sequence

import numpy as np

# Where an index stops fitting in 2 bytes. Indices are three quarters of a payload, so below this the whole thing is
# 40% smaller for nothing.
NARROW_INDEX_LIMIT = 65536

# A radius collapsed to nothing, or one standing out from the equal-volume sphere by more than this, is a moment that
# failed (a few voxels in a line), not a thin ellipsoid: across 47,414 real ellipsoids the legitimate ones reached 1.95
# and the collapsed ones 338 to 1182.
MAX_ELLIPSOID_STRETCH = 100.0

# Laplacian passes over a centre line, and how far each moves a node toward its neighbours' average. Ten at 0.5 takes
# the voxel lattice out without pulling the curve straight.
CENTRE_LINE_PASSES = 10
CENTRE_LINE_STRENGTH = 0.5


def index_dtype(n_vertices: int) -> np.dtype:
    """The integer type a surface's indices are stored in, derived from the vertex count and not recorded."""
    return np.dtype(np.uint16 if n_vertices < NARROW_INDEX_LIMIT else np.uint32)


def quantised_payload(vertices_xyz: np.ndarray, indices: np.ndarray) -> bytes:
    """Vertices quantised to uint16 over their own box, plus an index array narrow where it can be."""
    low = vertices_xyz.min(axis=0)
    scale = vertices_xyz.max(axis=0) - low
    scale[scale == 0] = 1.0
    quantised = np.clip((vertices_xyz - low) / scale * 65535 + 0.5, 0, 65535).astype(np.uint16)
    header = np.array([len(vertices_xyz), len(indices)], dtype=np.uint32)
    box = np.concatenate([low, scale]).astype(np.float32)
    narrowed = np.asarray(indices).astype(index_dtype(len(vertices_xyz)), copy=False)
    return header.tobytes() + box.tobytes() + quantised.tobytes() + narrowed.tobytes()


def payload_counts(payload: bytes) -> tuple[int | None, int | None]:
    """Vertex and index counts from a payload header, without decoding the arrays."""
    if len(payload) < 8:
        return None, None
    n_vertices, n_indices = struct.unpack_from("<II", payload, 0)
    return int(n_vertices), int(n_indices)


def skeleton_payload(vertices: np.ndarray, edges: np.ndarray) -> bytes:
    """A skeleton as line segments. Vertices are (Z, Y, X) or (Y, X) in µm, reordered so it overlays its instance."""
    vertices, edges = np.asarray(vertices, dtype=np.float32), np.asarray(edges, dtype=np.uint32)
    if len(vertices) < 2 or len(edges) == 0:
        return b""
    if vertices.shape[1] == 2:  # a plane: (Y, X) becomes (X, Y, 0), the frame the outlines use
        xyz = np.column_stack([vertices[:, 1], vertices[:, 0], np.zeros(len(vertices))])
    else:
        xyz = np.column_stack([vertices[:, 2], vertices[:, 1], vertices[:, 0]])
    return quantised_payload(xyz.astype(np.float32), edges)


def ellipsoid_payload(centre_xyz: Sequence[float], diameters: Sequence[float], axes: Sequence[float]) -> bytes:
    """An instance as the ellipsoid of its own second moments: centre, three radii, and the principal axes as rows.

    Empty when a moment collapsed, which hands the instance to the mesher.
    """
    if len(centre_xyz) != 3 or len(diameters) != 3 or len(axes) != 9:
        return b""
    values = [float(v) for v in centre_xyz] + [float(d) / 2.0 for d in diameters] + [float(v) for v in axes]
    radii = values[3:6]
    if not all(math.isfinite(v) for v in values) or min(radii) <= 0.0:
        return b""
    if max(radii) / (radii[0] * radii[1] * radii[2]) ** (1.0 / 3.0) > MAX_ELLIPSOID_STRETCH:
        return b""
    return struct.pack("<15f", *values)


def smooth_centre_line(vertices: np.ndarray, edges: np.ndarray, radii: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Take the voxel lattice out of a skeleton without moving where it branches or ends.

    Only nodes with exactly two neighbours move: junctions and tips are anchors, since smoothing them pulls a junction
    off the arms that share it and shortens every filament by half a step.
    """
    if len(vertices) < 3 or len(edges) == 0:
        return vertices, radii
    left, right = edges[:, 0].astype(np.intp), edges[:, 1].astype(np.intp)
    degree = np.bincount(left, minlength=len(vertices)) + np.bincount(right, minlength=len(vertices))
    moving = degree == 2
    if not moving.any():
        return vertices, radii
    points, widths = vertices.astype(np.float64, copy=True), radii.astype(np.float64, copy=True)
    share = np.maximum(degree, 1)
    for _ in range(CENTRE_LINE_PASSES):
        neighbour_points, neighbour_widths = np.zeros_like(points), np.zeros_like(widths)
        np.add.at(neighbour_points, left, points[right])
        np.add.at(neighbour_points, right, points[left])
        np.add.at(neighbour_widths, left, widths[right])
        np.add.at(neighbour_widths, right, widths[left])
        points[moving] += CENTRE_LINE_STRENGTH * (neighbour_points / share[:, None] - points)[moving]
        widths[moving] += CENTRE_LINE_STRENGTH * (neighbour_widths / share - widths)[moving]
    return points.astype(np.float32), widths.astype(np.float32)


def tube_payload(vertices: np.ndarray, edges: np.ndarray, radii: np.ndarray) -> bytes:
    """An instance as its centre line swept by its own radius: the skeleton payload plus one float32 radius per node."""
    vertices, edges = np.asarray(vertices, dtype=np.float32), np.asarray(edges, dtype=np.uint32)
    radii = np.asarray(radii, dtype=np.float32)
    if len(vertices) < 2 or len(edges) == 0 or len(radii) != len(vertices) or vertices.shape[1] != 3:
        return b""
    vertices, radii = smooth_centre_line(vertices, edges, radii)
    xyz = np.column_stack([vertices[:, 2], vertices[:, 1], vertices[:, 0]]).astype(np.float32)
    radii = np.maximum(radii, 0.0).astype(np.float32)
    if not np.all(np.isfinite(xyz)) or not np.all(np.isfinite(radii)):
        return b""
    return quantised_payload(xyz, edges) + radii.tobytes()
