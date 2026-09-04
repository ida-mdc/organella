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
    if not all(math.isfinite(v) for v in values) or min(values[3:6]) <= 0.0:
        return b""
    return struct.pack("<15f", *values)


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
