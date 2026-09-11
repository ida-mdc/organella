"""How a shape is represented for drawing: as a mesh, or as something far smaller.

A mesh per instance does not scale. One 512³ crop of a HeLa cell produced 2,698 meshes and
4.8 million vertices, and a whole macrophage at the same settings holds 34,110 instances -
too much to store and, worse, too many draw calls for a browser. But most of those
instances are not shapes that need a mesh: a vesicle at sphericity 1.0 *is* an ellipsoid,
and a microtubule *is* a tube.

So a surface is one of a few kinds, chosen per instance from what was already measured:

    ellipsoid   round and compact          10 floats, drawn from one shared sphere
    tube        elongated, one branch      a skeleton polyline with a radius per node
    mesh        everything else            marching cubes, as before

Nothing here is a guess about intent - each kind is picked from `sphericity`,
`aspect_ratio_major_minor` and `branches`, all of which the report already carries, and
`--geometry-as NAME=KIND` overrides it per structure when you know better than a threshold
does.

**No measurement depends on any of this.** Volume, surface area, sphericity and aspect
ratio all come from ITK's Crofton estimator on the voxels; the surface is only ever drawn.
That is what makes approximating it legitimate: an ellipsoid here changes no number in the
report, only the picture, and the page says which kind it drew.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# Every kind the writer can emit. The page has a builder per kind and a test asserts the
# two sets are equal, so a kind that nothing can draw fails the suite rather than quietly
# disappearing in somebody's browser.
SURFACE_KINDS = ("mesh", "ellipsoid", "tube")

# A surface this round is within a few percent of its own ellipsoid, so drawing the
# ellipsoid instead loses nothing anyone could see. Below it, shape is the point: a
# mitochondrion at 0.72 and an ER sheet at 0.05 are not ellipsoids in any useful sense.
ELLIPSOID_MIN_SPHERICITY = 0.75
# ... as long as it is not also strongly elongated. Roundness alone cannot tell a ball from
# a short rod once the rod is smooth, and a rod drawn as an ellipsoid of the same moments is
# noticeably too fat in the middle.
ELLIPSOID_MAX_ASPECT = 3.0

# A tube needs a centre line to follow, so it needs a skeleton, and it has to be long
# enough that one is meaningful: a blob's skeleton is a single branch the length of its own
# diameter, which a tube would draw as a stubby cylinder for no gain.
TUBE_MIN_ASPECT = 3.0

# Below this an aspect ratio is not a flat shape, it is a collapsed measurement. ITK's
# elongation and flatness are each a larger principal axis over a smaller one, so neither
# is below 1 and nor is their product; 0 is what comes back when an instance is small
# enough - a voxel, or a few in a line - that a principal moment is zero.
ASPECT_FLOOR = 1.0

# How far an ellipsoid's longest radius may stand out from the sphere of the same volume
# before it is refused as collapsed. ITK's equivalent ellipsoid preserves volume, so a
# principal moment that collapses does not shrink the shape - it pushes the volume into
# whichever axes survived, and the longest one runs away.
#
# Measured across one real batch of 47,414 ellipsoids: the legitimate ones reach 1.95 and
# sit at a median of 1.08, while the six with a collapsed moment are 338 to 1182. A hundred
# is fifty times clear of anything real and three times under the mildest collapse.
#
# Against the radii ratio rather than this, because ratio alone calls a thin shape
# collapsed: a genuinely flat instance can be four orders of magnitude across its thinnest
# axis and still be a shape somebody wants drawn. What is not a shape is one drawn far
# larger than the volume it is made of.
MAX_ELLIPSOID_STRETCH = 100.0


def choose_surface(
    sphericity: Optional[float],
    aspect: Optional[float],
    branches: Optional[float],
    has_skeleton: bool = False,
) -> str:
    """Which kind of surface to store for one instance.

    Pure, so the thresholds above are the whole of the policy and are testable on their
    own. A missing metric falls through to ``mesh``: the fallback is always allowed to be
    wrong about nothing, where a primitive chosen on a NaN would be wrong about a shape.
    """
    def known(value: Optional[float]) -> bool:
        return value is not None and not (isinstance(value, float) and math.isnan(value))

    # An aspect ratio below 1 is not a reading of a shape (see ASPECT_FLOOR), so it is
    # treated as no reading at all and the instance falls through to a mesh - which draws
    # the voxels it actually has. Left as it stands, 0 reads as "not elongated", which is
    # the one thing such an instance is certainly not: the two principal axes that did not
    # collapse take the whole volume between them, and the equivalent ellipsoid comes out
    # tens of µm long for a speck of four voxels.
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

    The axes are the principal directions as rows, matching the radii in order; the viewer
    scales one shared unit sphere by the radii, rotates it by the axes and moves it to the
    centre. Unquantised because 60 bytes is already less than the header of a mesh.
    """
    if len(centre_xyz) != 3 or len(diameters) != 3 or len(axes) != 9:
        return b""
    values = (
        [float(v) for v in centre_xyz]
        + [float(d) / 2.0 for d in diameters]
        + [float(v) for v in axes]
    )
    # A radius of zero is not a thin ellipsoid, it is a collapsed one, and the moments it
    # came from say nothing about a shape. An instance of one or two voxels arrives that
    # way: two diameters come back as zero and the whole volume goes into the third, which
    # for a two-voxel ER instance in a 2 µm crop was 6.9 *metres*. That is finite, so the
    # check above passed it, and one such instance is enough to set the scene's bounding
    # box - the camera then fits a million µm and every real structure is a sub-pixel
    # speck. Returning nothing hands it back to the mesher, which draws the voxels.
    radii = values[3:6]
    if not all(math.isfinite(v) for v in values) or min(radii) <= 0.0:
        return b""
    # And the same failure one step less extreme, which the check above lets through: a
    # four-voxel nucleus speck of one real batch came back with radii of 1e-7, 9.2 and
    # 39.2 µm - finite, all positive, and 78 µm across in a cell 10 µm wide. One of those
    # is enough to set the scene's bounding box, and then every real structure in the
    # object is a sub-pixel speck beside it.
    equivalent_sphere = (radii[0] * radii[1] * radii[2]) ** (1.0 / 3.0)
    if max(radii) / equivalent_sphere > MAX_ELLIPSOID_STRETCH:
        return b""
    return struct.pack("<15f", *values)


# Passes of Laplacian smoothing over a centre line, and how far each moves a node toward
# the average of its neighbours. Ten at 0.5 takes the lattice out without pulling a curve
# straight: measured on a real microtubule, the median turn between consecutive steps went
# from 33.6° to about 4°, where the turn a filament actually has is a couple of degrees.
CENTRE_LINE_PASSES = 10
CENTRE_LINE_STRENGTH = 0.5


def smooth_centre_line(vertices: np.ndarray, edges: np.ndarray,
                       radii: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Take the voxel lattice out of a skeleton, without moving where it branches or ends.

    A skeleton walks from voxel to voxel, so its steps are one of a handful of lattice
    lengths - 16 nm, 16√2, 16√3 on 16 nm data - and it turns about 34° at every one of
    them. That zig-zag is what a swept tube shows as blocky, and it is in the *data*: no
    amount of tessellation or ray-casting can smooth a centre line that is not smooth.

    Only nodes with exactly two neighbours move. A branch point is where arms meet and an
    end is where the structure stops, so both are anchors: smoothing them would pull a
    junction off the arms that share it and shorten every filament by half a step.
    """
    if len(vertices) < 3 or len(edges) == 0:
        return vertices, radii
    left, right = edges[:, 0].astype(np.intp), edges[:, 1].astype(np.intp)
    degree = np.bincount(left, minlength=len(vertices)) + np.bincount(right, minlength=len(vertices))
    moving = degree == 2
    if not moving.any():
        return vertices, radii

    # Scattered adds rather than a loop over edges: one object's filaments are 1.2 million
    # edges, and ten passes of a Python loop over them is minutes where this is milliseconds.
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

    The first four blocks are exactly the skeleton payload, so a reader that already
    decodes skeletons reads a tube by taking one more array off the end. The radii come
    from kimimaro, which computes them while skeletonising and which this package used to
    throw away - so a tube costs 4 bytes a node more than the skeleton already stored.

    A polyline is the right primitive for a filament rather than a deformed cylinder: it
    can differ in length, curvature and how many branches it has, none of which a fixed
    base mesh can be deformed into.
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
    from label_anatomy.analysis.meshes import quantised_payload

    return quantised_payload(verts_xyz, edges) + radii.tobytes()


def surface_counts(kind: str, payload: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Vertex and element counts for a surface, or nulls where they mean nothing.

    A mesh and a tube are vertex lists and say how big they are in their header. An
    ellipsoid is not: reporting a vertex count for it would describe whatever the viewer
    happened to tessellate, which is a rendering choice and not a property of the data.
    """
    from label_anatomy.analysis.meshes import payload_counts

    if kind == "ellipsoid" or not payload:
        return None, None
    return payload_counts(payload)


def surface_of(
    kind: str,
    stats: Dict[str, object],
    skeleton=None,
) -> bytes:
    """The payload for a parametric kind, from measurements already taken.

    ``mesh`` is not here: it needs the instance's cropped mask and a marching-cubes pass,
    which is farmed out to a pool, so the caller keeps that branch. This is the cheap half
    - nothing in it reads a voxel.
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
