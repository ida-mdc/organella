"""How a shape is represented for drawing: a mesh, or something far smaller.

A mesh per instance does not scale - one 512³ crop of a HeLa cell produced 2,698 meshes and
4.8 million vertices, a whole macrophage 34,110 instances - and most of those are not shapes
that need one: a vesicle at sphericity 1.0 *is* an ellipsoid, a microtubule *is* a tube.

    ellipsoid   round and compact          10 floats, drawn from one shared sphere
    tube        elongated, one branch      a skeleton polyline with a radius per node
    mesh        everything else            marching cubes

The kind comes from metrics the report already carries, and ``--geometry-as NAME=KIND``
overrides it per structure. No measurement depends on any of it: volume, surface area and
sphericity come from the voxels, and the surface is only ever drawn.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# Every kind a surface may be stored as.
SURFACE_KINDS = ("mesh", "ellipsoid", "tube")

# Rounder than this is within a few percent of its own ellipsoid. Below it shape is the
# point: a mitochondrion at 0.72 and an ER sheet at 0.05 are not ellipsoids in any useful
# sense.
ELLIPSOID_MIN_SPHERICITY = 0.75
# ... unless also elongated: roundness alone cannot tell a sphere from a smooth short rod,
# the rod drawn as an ellipsoid of the same moments is too fat in the middle.
ELLIPSOID_MAX_ASPECT = 3.0

# A blob's skeleton is one branch the length of its own diameter, which a tube would draw as
# a stubby cylinder for no gain.
TUBE_MIN_ASPECT = 3.0

# Below this an aspect ratio is a collapsed measurement, not a flat shape: ITK's elongation
# and flatness are each a larger principal axis over a smaller one, so 0 only ever means a
# principal moment was zero.
ASPECT_FLOOR = 1.0

# How far an ellipsoid's longest radius may stand out from the sphere of the same volume
# before it is refused. The equivalent ellipsoid preserves volume, so a collapsed principal
# moment pushes that volume into whichever axes survived and the longest runs away: across
# 47,414 real ellipsoids the legitimate ones reached 1.95 (median 1.08) and the six collapsed
# ones 338 to 1182. Not the radii ratio, which calls a genuinely flat shape collapsed.
MAX_ELLIPSOID_STRETCH = 100.0


def choose_surface(
    sphericity: Optional[float],
    aspect: Optional[float],
    branches: Optional[float],
    has_skeleton: bool = False,
) -> str:
    """Which kind of surface to store for one instance.

    A missing metric falls through to ``mesh``, which draws the voxels it actually has,
    where a primitive chosen on a NaN would be wrong about the shape.
    """
    def known(value: Optional[float]) -> bool:
        return value is not None and not (isinstance(value, float) and math.isnan(value))

    # No reading at all rather than 0, which would read as "not elongated" - the one thing
    # such an instance is certainly not (see ASPECT_FLOOR).
    if known(aspect) and aspect < ASPECT_FLOOR:
        aspect = None

    if (has_skeleton and known(aspect) and aspect >= TUBE_MIN_ASPECT
            and known(branches) and branches >= 1):
        return "tube"
    if (known(sphericity) and sphericity >= ELLIPSOID_MIN_SPHERICITY
            and known(aspect) and aspect <= ELLIPSOID_MAX_ASPECT):
        return "ellipsoid"
    return "mesh"


def ellipsoid_payload(
    centre_xyz: Sequence[float],
    diameters: Sequence[float],
    axes: Sequence[float],
) -> bytes:
    """One instance as the ellipsoid of its own second moments.

    Binary payload, all float32 and little-endian, 60 bytes:

      [float32×3 centre_xyz][float32×3 radii_xyz][float32×9 axes row-major]

    The axes are the principal directions as rows, matching the radii in order: one shared
    unit sphere, scaled by the radii, rotated by the axes and moved to the centre.
    Unquantised, since 60 bytes is already less than a mesh header.
    """
    if len(centre_xyz) != 3 or len(diameters) != 3 or len(axes) != 9:
        return b""
    values = (
        [float(v) for v in centre_xyz]
        + [float(d) / 2.0 for d in diameters]
        + [float(v) for v in axes]
    )
    # A collapsed moment, not a thin ellipsoid: a few voxels in a line put the whole volume
    # into one axis, which for a two-voxel ER instance in a 2 µm crop was 6.9 *metres*. It is
    # finite, and one of them sets the scene's bounding box, which leaves every real
    # structure a sub-pixel speck. Returning nothing hands it to the mesher.
    radii = values[3:6]
    if not all(math.isfinite(v) for v in values) or min(radii) <= 0.0:
        return b""
    # The same failure one step milder, which the check above passes: a four-voxel nucleus
    # speck came back at 1e-7, 9.2 and 39.2 µm - all positive, 78 µm across in a 10 µm cell.
    equivalent_sphere = (radii[0] * radii[1] * radii[2]) ** (1.0 / 3.0)
    if max(radii) / equivalent_sphere > MAX_ELLIPSOID_STRETCH:
        return b""
    return struct.pack("<15f", *values)


# Laplacian passes over a centre line, and how far each moves a node toward its neighbours'
# average. Ten at 0.5 takes the lattice out without pulling the curve straight: on a real
# microtubule the median turn per step went from 33.6° to about 4°, against the couple of
# degrees a filament actually has.
CENTRE_LINE_PASSES = 10
CENTRE_LINE_STRENGTH = 0.5


def smooth_centre_line(vertices: np.ndarray, edges: np.ndarray,
                       radii: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Take the voxel lattice out of a skeleton, without moving where it branches or ends.

    A skeleton walks voxel to voxel, so it turns about 34° at every step, and that zig-zag is
    in the *data*: no amount of tessellation can smooth a centre line that is not smooth.

    Only nodes with exactly two neighbours move. Branch points and ends are anchors -
    smoothing them pulls a junction off the arms that share it and shortens every filament
    by half a step.
    """
    if len(vertices) < 3 or len(edges) == 0:
        return vertices, radii
    left, right = edges[:, 0].astype(np.intp), edges[:, 1].astype(np.intp)
    degree = np.bincount(left, minlength=len(vertices)) + np.bincount(right, minlength=len(vertices))
    moving = degree == 2
    if not moving.any():
        return vertices, radii

    # Scattered adds, not a loop: one object's filaments are 1.2 million edges, where ten
    # Python passes is minutes and this is milliseconds.
    points = vertices.astype(np.float64, copy=True)
    widths = radii.astype(np.float64, copy=True)
    share = np.maximum(degree, 1)
    for _ in range(CENTRE_LINE_PASSES):
        neighbour_sum = np.zeros_like(points)
        radius_sum = np.zeros_like(widths)
        np.add.at(neighbour_sum, left, points[right])
        np.add.at(neighbour_sum, right, points[left])
        np.add.at(radius_sum, left, widths[right])
        np.add.at(radius_sum, right, widths[left])
        mean_point = neighbour_sum / share[:, None]
        mean_width = radius_sum / share
        points[moving] += CENTRE_LINE_STRENGTH * (mean_point[moving] - points[moving])
        widths[moving] += CENTRE_LINE_STRENGTH * (mean_width[moving] - widths[moving])
    return points.astype(np.float32), widths.astype(np.float32)


def tube_payload(skeleton, radius_floor_um: float = 0.0) -> bytes:
    """One instance as its centre line, swept by its own radius.

    Binary payload:

      [uint32 nV][uint32 nE]
      [float32×3 min_xyz][float32×3 scale_xyz]   ← dequantisation params
      [uint16 × nV×3 quantised XYZ vertices]
      [uint32 × nE×2 edge index pairs]
      [float32 × nV radii in µm]

    The first four blocks are exactly the skeleton payload, so a reader that decodes
    skeletons reads a tube by taking one more array off the end. kimimaro computes the radii
    while skeletonising, so a tube costs 4 bytes a node more than the skeleton already
    stored - and a polyline, unlike a deformed cylinder, can differ in length, curvature and
    branch count.
    """
    if skeleton is None:
        return b""
    verts = np.asarray(getattr(skeleton, "vertices", ()), dtype=np.float32)
    edges = np.asarray(getattr(skeleton, "edges", ()), dtype=np.uint32)
    radii = np.asarray(getattr(skeleton, "radii", ()), dtype=np.float32)
    if len(verts) < 2 or len(edges) == 0 or len(radii) != len(verts):
        return b""
    if verts.shape[1] != 3:
        return b""
    verts, radii = smooth_centre_line(verts, edges, radii)
    verts_xyz = np.column_stack([verts[:, 2], verts[:, 1], verts[:, 0]]).astype(np.float32)
    radii = np.maximum(radii, float(radius_floor_um)).astype(np.float32)
    if not np.all(np.isfinite(verts_xyz)) or not np.all(np.isfinite(radii)):
        return b""
    from organella.analysis.meshes import quantised_payload

    return quantised_payload(verts_xyz, edges) + radii.tobytes()


def surface_counts(kind: str, payload: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Vertex and element counts for a surface, or nulls where they mean nothing.

    A mesh and a tube carry them in their header. An ellipsoid's would describe whatever the
    viewer tessellated, which is a rendering choice and not a property of the data.
    """
    from organella.analysis.meshes import payload_counts

    if kind == "ellipsoid" or not payload:
        return None, None
    return payload_counts(payload)


def surface_of(
    kind: str,
    stats: Dict[str, object],
    skeleton=None,
) -> bytes:
    """The payload for a parametric kind, from measurements already taken.

    ``mesh`` is not here: it needs a cropped mask and a marching-cubes pass, farmed out to a
    pool, so the caller keeps that branch. Nothing in this half reads a voxel.
    """
    if kind == "ellipsoid":
        return ellipsoid_payload(
            stats.get("centroid_xyz_um") or (),
            stats.get("ellipsoid_diameters_um") or (),
            stats.get("ellipsoid_axes") or (),
        )
    if kind == "tube":
        return tube_payload(skeleton)
    return b""
