"""Geometry for the 3D widgets and the Blender export: meshes, outlines, skeletons.

It never enters the report table, which every stats query loads, but goes beside it as one
``geometry.parquet`` per object. Parquet rather than a blob file keeps it queryable, so
opening one structure costs its own few MB rather than the whole object's.
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import fast_simplification
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from skimage.measure import marching_cubes

from organella.analysis.shapes import (
    METRICS_2D,
    METRICS_3D,
)
from organella.analysis.shapes import foreground_bounds
from organella.analysis.distances import (
    object_center_um,
    polarity_from_offset,
    segmented_center_um,
)
from organella.analysis.parallel import WorkPool, batched, worker_share
from organella.analysis.shapes import skeleton_graph_metrics
from organella.analysis.cache import (
    contacts_for,
    label_metrics_for,
    region_metrics_for,
    regions_for,
    skeletons_for,
)
from organella.config import EntityFilter, RunConfig, forced_surface, wants_skeletons
from organella.analysis.primitives import choose_surface, surface_counts, surface_of

logger = logging.getLogger(__name__)

# Measurements carried onto each geometry row, so a row can be sorted and coloured on its
# own. polar_n* is the unit direction from the object centre to the instance.
CARRIED_METRICS = [
    "aspect_ratio_major_minor", "branches", "length_um", "tortuosity",
    "distance_to_closest_same_type_um",
    "polar_dist_um", "polar_az_deg", "polar_el_deg", "polar_angle_deg",
    "polar_nz", "polar_ny", "polar_nx",
    "polar_spread_deg",
]

# Payload headers as columns of their own, so how big a surface is can be read without
# decoding it.
GEOMETRY_COLUMNS = [
    "object_id", "group_id", "entity_name", "entity_kind", "row_type", "label_id",
    "spatial_dims",
    # 3D size and shape, then the 2D pair; a row carries whichever its object has.
    "volume_um3", "surface_area_um2", "sphericity",
    "area_um2", "perimeter_um", "circularity", *CARRIED_METRICS,
    "entity_a", "label_a", "entity_b", "label_b", "gap_um",
    "surface_kind",
    "surface_vertices", "surface_elements", "skeleton_vertices", "skeleton_edges",
    "outline_vertices", "outline_edges",
    "surface", "skeleton", "outline",
]

_TEXT_FIELDS = {"object_id", "group_id", "entity_name", "entity_kind", "row_type",
                "entity_a", "entity_b", "surface_kind"}
# The carried metrics stay floats, branches included: "not measured" is NaN, which an
# integer column cannot hold.
_INT_FIELDS = {"label_id", "label_a", "label_b", "spatial_dims",
               "surface_vertices", "surface_elements", "skeleton_vertices", "skeleton_edges",
               "outline_vertices", "outline_edges"}
_BLOB_FIELDS = {"surface", "skeleton", "outline"}

GEOMETRY_FILENAME = "geometry.parquet"


@dataclass(frozen=True)
class MeshOptions:
    """The knobs the --mesh-* flags set."""
    smooth_sigma: float = 0.7
    step_size: int = 2
    target_reduction: float = 0.8
    level: Optional[float] = None
    # Structure -> mesh | ellipsoid | tube. Unset means decide from the measured shape.
    # Same mapping the metrics use, so the two share one computation per object.
    geometry_as: Mapping[str, str] = field(default_factory=dict)
    # Structures to skeletonise, from --skeletons. The metrics read the same setting, so a
    # skeleton is computed once per object and shared.
    skeletons: EntityFilter = None
    max_skeleton_voxels: Optional[int] = 500_000
    # Contacts ride along so "Colour by → Contact group" works; None leaves them out. Cheap
    # next to meshing (seconds against minutes).
    contact_max_um: Optional[float] = 0.5
    # Processes to mesh instances with. 0 = work it out from the cores left over.
    mesh_workers: int = 0
    # Most vertices any one surface may keep; 0 lifts the cap. A fixed decimation fraction
    # bounds nothing: at 0.5 one ER sheet was still 2.87 million vertices and 86 MB, two
    # thirds of that object's entire geometry, next to 57 for a vesicle.
    max_vertices: int = 200_000
    # Marching cubes by default, on the measurements: on one ER sheet the two methods came
    # out 0.16% apart on vertex count (5,584,555 against 5,575,347), not the "far fewer" a
    # dual method promises, because a surface has about as many crossing cells as edges.
    # Surface nets buys smoothness - 30% less radius noise on a sphere of known radius - for
    # 66% more time, and couples the axes, which shows on 5x-anisotropic alpha cells.
    surface_method: str = "marching-cubes"

    @classmethod
    def from_config(cls, cfg: "RunConfig") -> "MeshOptions":
        """The --mesh-* settings of a run, in the shape the mesher takes them.

        The one place the mapping is written, and where a new mesh setting belongs: both
        ``process --with-mesh`` and the ``mesh`` command come through here, and two copies
        of it drifted far enough that a flag stopped being read.
        """
        return cls(
            smooth_sigma=cfg.mesh_smooth_sigma,
            step_size=cfg.mesh_step_size,
            target_reduction=cfg.mesh_target_reduction,
            level=cfg.mesh_level,
            geometry_as=cfg.geometry_as,
            skeletons=cfg.skeletons,
            max_skeleton_voxels=cfg.max_skeleton_voxels,
            contact_max_um=cfg.contact_max_um,
            mesh_workers=cfg.mesh_workers,
            max_vertices=cfg.mesh_max_vertices,
            surface_method=cfg.mesh_surface_method,
        )


def sigma_for_shape(sphericity_value: float, fill_ratio: float,
                    sigma_min: float = 0.3, sigma_max: float = 1.5) -> float:
    """Gaussian sigma from shape: blobs get more smoothing, thin structures less.

    From roundness and fill_ratio (samples / bbox samples, which catches curved filaments
    that look compact in PCA but are sparse); the midpoint when either is NaN. generate_mesh
    caps the result by the instance's own thickness, so nothing small is smoothed away.
    """
    _SPHERE_FILL = math.pi / 6.0  # fill ratio of a perfect sphere (~0.524)
    if math.isnan(sphericity_value) or math.isnan(fill_ratio):
        return (sigma_min + sigma_max) / 2.0
    fill_score = min(1.0, fill_ratio / _SPHERE_FILL)
    blob_score = math.sqrt(sphericity_value * fill_score)  # geometric mean of both
    return sigma_min + (sigma_max - sigma_min) * blob_score


# The 12 edges of a cell, as pairs of its 8 corners. A corner is (dz, dy, dx) in {0,1}³ and
# an edge joins two that differ in exactly one of them.
_CELL_EDGES = tuple(
    (a, b)
    for a in ((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
              (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1))
    for b in ((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
              (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1))
    if sum(abs(x - y) for x, y in zip(a, b)) == 1 and a < b
)


def surface_nets(field: np.ndarray, level: float = 0.0):
    """The isosurface as one vertex per cell, joined across the edges that cross it.

    Marching cubes puts a vertex on every crossing *edge*, so a staircase boundary becomes a
    staircase of triangles. This puts one vertex per *cell* at the average of its crossings
    and joins the four cells around each crossing edge into a quad, which comes out smoother
    and better spread for the same field.

    Returns (vertices in ZYX index coordinates, triangles) - the shapes marching_cubes gives,
    so the caller scales and reorders them identically.
    """
    f = np.asarray(field, dtype=np.float32) - float(level)
    if min(f.shape) < 2:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    nz, ny, nx = (n - 1 for n in f.shape)

    corner = {}
    for a in range(2):
        for b in range(2):
            for c in range(2):
                corner[(a, b, c)] = f[a:a + nz, b:b + ny, c:c + nx]

    total = np.zeros((nz, ny, nx, 3), np.float32)
    count = np.zeros((nz, ny, nx), np.float32)
    for a, b in _CELL_EDGES:
        fa, fb = corner[a], corner[b]
        crosses = (fa > 0) != (fb > 0)
        if not crosses.any():
            continue
        denominator = fa - fb
        t = np.where(np.abs(denominator) > 1e-12, fa / np.where(denominator == 0, 1, denominator), 0.5)
        t = np.clip(t, 0.0, 1.0).astype(np.float32)
        for axis in range(3):
            step = b[axis] - a[axis]
            position = a[axis] + (t * step if step else 0.0)
            total[..., axis] += np.where(crosses, position, 0.0)
        count += crosses
    active = count > 0
    if not active.any():
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)

    index = np.full((nz, ny, nx), -1, np.int64)
    index[active] = np.arange(int(active.sum()))
    cells = np.argwhere(active)
    vertices = (cells + total[active] / count[active][:, None]).astype(np.float32)

    # One quad per crossing edge of the grid, from the four cells that share it.
    inside = f > 0
    faces = []
    for axis in range(3):
        lo = [slice(1, n - 1) for n in f.shape]
        hi = [slice(1, n - 1) for n in f.shape]
        lo[axis] = slice(0, f.shape[axis] - 1)
        hi[axis] = slice(1, f.shape[axis])
        a_in, b_in = inside[tuple(lo)], inside[tuple(hi)]
        crossing = np.argwhere(a_in != b_in)
        if not len(crossing):
            continue
        # The four cells around this edge. `crossing` indexes into slices that already start
        # at 1 on the two axes the edge does not run along, so those coordinates are already
        # the lower of the two cells sharing it - stepping back again misses by one cell.
        others = [ax for ax in range(3) if ax != axis]
        base = crossing.copy()
        offsets = [(0, 0), (1, 0), (1, 1), (0, 1)]
        quad = []
        for d0, d1 in offsets:
            here = base.copy()
            here[:, others[0]] += d0
            here[:, others[1]] += d1
            quad.append(index[here[:, 0], here[:, 1], here[:, 2]])
        quad = np.stack(quad, axis=1)
        ok = (quad >= 0).all(axis=1)
        quad = quad[ok]
        if not len(quad):
            continue
        # Wind so the normal points out of the solid, which flips with the edge's direction.
        forward = a_in[tuple(crossing[:, i] for i in range(3))][ok]
        flip = ~forward if axis == 1 else forward
        first = np.where(flip[:, None], quad[:, [0, 1, 2]], quad[:, [0, 2, 1]])
        second = np.where(flip[:, None], quad[:, [0, 2, 3]], quad[:, [0, 3, 2]])
        faces.append(first)
        faces.append(second)
    if not faces:
        return vertices, np.zeros((0, 3), np.int64)
    return vertices, np.vstack(faces).astype(np.int64)


def generate_mesh(
    binary: np.ndarray,
    bbox_origin_zyx: Tuple[int, int, int],
    voxel_size_zyx: Sequence[float],
    step_size: int = 2,
    smooth_sigma: float = 0.7,
    target_reduction: float = 0.8,
    level: Optional[float] = None,
    max_vertices: int = 0,
    surface_method: str = "marching-cubes",
) -> bytes:
    """Mesh a (Z,Y,X) binary mask via marching cubes on a signed distance field.

    The surface is the zero-level set of ``inside_EDT − outside_EDT`` (optionally
    smoothed), which sits at the true voxel boundary. That preserves thin structures and
    avoids the volume inflation of blurring the binary directly.

    Binary payload:
      [uint32 nV][uint32 nF]
      [float32×3 min_xyz][float32×3 scale_xyz]   ← dequantisation params
      [uint16 × nV×3 quantised XYZ vertices]
      [uint32 × nF×3 face indices]

    Returned raw, as it goes into the parquet.
    """
    if binary.sum() < 8:
        return b""
    try:
        spacing = tuple(float(v) for v in voxel_size_zyx)
        finest, coarsest = min(spacing), max(spacing)
        # σ is in the finest sample: the same blur in µm along every axis, which for
        # anisotropic data is a fraction of a sample along the coarse one.
        sigma_um = max(0.0, smooth_sigma) * finest
        # The foreground reaches every face of its own bbox, so pad with background first;
        # with smoothing, ~3σ per axis so the field settles to "outside" before the border.
        pads = tuple(1 if sigma_um <= 0 else int(math.ceil(3.0 * sigma_um / s)) + 1
                     for s in spacing)
        b = np.pad(binary.astype(bool), pad_width=[(p, p) for p in pads])
        # Signed distance field in µm: >0 inside, <0 outside, 0 at the boundary.
        inside = distance_transform_edt(b, sampling=spacing)
        sdf = (inside - distance_transform_edt(~b, sampling=spacing)).astype(np.float32)
        # Neither knob may exceed what the instance can carry: the largest value inside is
        # its inscribed radius, and a kernel wider than half of it flattens the field to
        # nothing while a coarser step walks over it.
        radius_um = float(inside.max())
        sigma_um = min(sigma_um, radius_um / 2.0)
        step = max(1, min(step_size, int(radius_um / coarsest) - 1))
        if sigma_um > 0:
            sdf = gaussian_filter(sdf, sigma=tuple(sigma_um / s for s in spacing))
        if surface_method == "surface-nets":
            # A dual method has no step size: it is one vertex per cell either way, so the
            # coarsening a step would buy comes from the budget instead.
            verts, faces = surface_nets(sdf, level=0.0 if level is None else level)
            if not len(faces):
                return b""
        else:
            verts, faces, _, _ = marching_cubes(
                sdf, level=0.0 if level is None else level, step_size=step
            )
        verts = verts - np.asarray(pads)  # undo padding offset → local voxel coords
        oz, oy, ox = bbox_origin_zyx
        sz, sy, sx = (float(v) for v in voxel_size_zyx)
        # Reorder ZYX → XYZ in µm, the order the payload stores
        verts_xyz = np.column_stack([
            (verts[:, 2] + ox) * sx,
            (verts[:, 1] + oy) * sy,
            (verts[:, 0] + oz) * sz,
        ]).astype(np.float32)
        if target_reduction > 0 and len(faces) > 100:
            verts_xyz, faces_s = fast_simplification.simplify(
                verts_xyz, faces.astype(int), target_reduction=target_reduction, verbose=False,
            )
            verts_xyz = verts_xyz.astype(np.float32)
            faces = faces_s
        # A fixed fraction bounds nothing, so cap the count itself.
        if max_vertices and len(verts_xyz) > max_vertices and len(faces) > 100:
            verts_xyz, faces_s = fast_simplification.simplify(
                verts_xyz, faces.astype(int),
                target_reduction=1.0 - max_vertices / len(verts_xyz), verbose=False,
            )
            verts_xyz = verts_xyz.astype(np.float32)
            faces = faces_s
        return quantised_payload(verts_xyz, faces.astype(np.uint32))
    except Exception as exc:
        logger.debug("organella: meshing failed (%s); emitting no geometry", exc)
        return b""


def generate_outline(
    binary: np.ndarray,
    bbox_origin_yx: Tuple[int, int],
    pixel_size_yx: Sequence[float],
    simplify_tol_px: float = 0.0,
) -> bytes:
    """Trace a (Y,X) binary mask's boundary as closed line loops: generate_mesh in 2D.

    A plane has no surface to triangulate, so an outline is stored instead, in the same
    quantised payload as the skeleton overlay with z fixed at 0, so one decoder reads both.
    Loops are closed so a canvas can fill them; holes come out as loops of their own.

    Binary payload: the quantised_payload layout, indices holding [uint32 × nE×2] pairs.
    """
    if binary.sum() < 1:
        return b""
    try:
        from skimage.measure import approximate_polygon, find_contours

        # Padded so an instance touching its own bbox still traces a closed loop.
        padded = np.pad(binary.astype(float), 1)
        loops = find_contours(padded, level=0.5)
        oy, ox = bbox_origin_yx
        sy, sx = (float(v) for v in pixel_size_yx)
        verts: List[np.ndarray] = []
        edges: List[np.ndarray] = []
        offset = 0
        for loop in loops:
            if simplify_tol_px > 0:
                loop = approximate_polygon(loop, tolerance=simplify_tol_px)
            loop = loop - 1.0
            if len(loop) < 3:
                continue
            # find_contours repeats the first point to close the ring; drop it and close
            # the loop through the index array instead.
            if np.allclose(loop[0], loop[-1]):
                loop = loop[:-1]
            n = len(loop)
            xy = np.column_stack([
                (loop[:, 1] + ox) * sx,
                (loop[:, 0] + oy) * sy,
                np.zeros(n),
            ]).astype(np.float32)
            verts.append(xy)
            ring = np.column_stack([np.arange(n), (np.arange(n) + 1) % n]) + offset
            edges.append(ring.astype(np.uint32))
            offset += n
        if not verts:
            return b""
        return quantised_payload(np.vstack(verts), np.vstack(edges))
    except Exception as exc:
        logger.debug("organella: outlining failed (%s); emitting no geometry", exc)
        return b""


def skeleton_payload(skeleton) -> bytes:
    """Encode a skeleton as a line-segment payload.

    Vertices are µm in the cropped frame - ZYX from kimimaro, YX from the planar thinning -
    both reordered to XYZ so a skeleton overlays exactly on its own instance.

    Binary payload:
      [uint32 nV][uint32 nE]
      [float32×3 min_xyz][float32×3 scale_xyz]   ← dequantisation params
      [uint16 × nV×3 quantised XYZ vertices]
      [uint32 × nE×2 edge index pairs]
    """
    if skeleton is None:
        return b""
    verts = np.asarray(skeleton.vertices, dtype=np.float32)
    edges = np.asarray(skeleton.edges, dtype=np.uint32)
    if len(verts) < 2 or len(edges) == 0:
        return b""
    if verts.shape[1] == 2:
        # A planar medial axis: (Y, X) in µm → XY0, the same frame the outlines use.
        verts_xyz = np.column_stack([
            verts[:, 1], verts[:, 0], np.zeros(len(verts))
        ]).astype(np.float32)
    else:
        verts_xyz = np.column_stack([verts[:, 2], verts[:, 1], verts[:, 0]]).astype(np.float32)
    return quantised_payload(verts_xyz, edges)


def payload_counts(payload: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Vertex and index counts from a payload header, without decoding the arrays."""
    if len(payload) < 8:
        return None, None
    n_verts, n_indices = struct.unpack_from("<II", payload, 0)
    return int(n_verts), int(n_indices)


# Where an index stops fitting in 2 bytes. Worth the branch: indices are three quarters of a
# payload - exactly 30 bytes per vertex, 6 position and 24 indices - so below this the whole
# thing is 40% smaller for nothing, and on one real object 151 of 158 meshes were below it.
NARROW_INDEX_LIMIT = 65536


def index_dtype(n_verts: int) -> np.dtype:
    """The integer type this surface's indices are stored in.

    Derived from the vertex count rather than recorded: a width flag would be a second
    source of truth for something the header already determines.
    """
    return np.dtype(np.uint16 if n_verts < NARROW_INDEX_LIMIT else np.uint32)


def quantised_payload(verts_xyz: np.ndarray, indices: np.ndarray) -> bytes:
    """Vertices quantised to uint16 plus an index array, narrow where it can be."""
    min_xyz = verts_xyz.min(axis=0)
    scale_xyz = verts_xyz.max(axis=0) - min_xyz
    scale_xyz[scale_xyz == 0] = 1.0
    verts_q = np.clip(
        (verts_xyz - min_xyz) / scale_xyz * 65535 + 0.5, 0, 65535
    ).astype(np.uint16)
    header = np.array([len(verts_xyz), len(indices)], dtype=np.uint32)
    quant_params = np.concatenate([min_xyz, scale_xyz]).astype(np.float32)
    narrowed = np.asarray(indices).astype(index_dtype(len(verts_xyz)), copy=False)
    return header.tobytes() + quant_params.tobytes() + verts_q.tobytes() + narrowed.tobytes()


def _bbox_extent(binary: np.ndarray) -> float:
    """Number of samples in the tightest box around the foreground."""
    bounds = foreground_bounds(binary)
    if bounds is None:
        return 0.0
    extent = 1
    for low, high in bounds:
        extent *= high - low
    return float(extent)



# Instances whose geometry is in flight at once. A pool consumes the whole iterable it is
# handed, so feeding it everything would hold every cropped mask in memory at the same time.
_GEOMETRY_BATCH = 256

# Above this an instance is meshed here rather than farmed out. Cost follows the padded
# bounding box, not the voxel count - an organelle threading through the object has a box
# spanning most of it, whose EDT and smoothed copy are gigabytes. One in the parent is
# survivable; one per worker is how a 22-process pool nearly took a machine down.
_INLINE_INSTANCE_SAMPLES = 2_000_000


def _instance_geometry(task: Tuple[Any, ...]) -> Tuple[bytes, bytes]:
    """One instance's (mesh, outline).

    Module level and tuple-argued so a process pool can carry it. Worth farming out: what
    crosses is one cropped mask, and the work on it is an EDT, a blur, marching cubes and a
    decimation.
    """
    (image, origin, sample_size, planar, step_size, sigma, target_reduction, level,
     max_vertices, surface_method) = task
    if planar:
        return b"", generate_outline(image, origin, sample_size)
    return generate_mesh(image, origin, sample_size, step_size=step_size,
                         smooth_sigma=sigma, target_reduction=target_reduction,
                         level=level, max_vertices=max_vertices,
                         surface_method=surface_method), b""


def mesh_rows_for_object(
    volumes: Mapping[str, np.ndarray],
    kinds: Mapping[str, str],
    sample_size: Sequence[float],
    object_id: str,
    group_id: str = "",
    options: MeshOptions = MeshOptions(),
    metrics: Optional[Mapping[tuple, Mapping[str, Any]]] = None,
    object_mask_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """One row per label instance and per whole-structure mask, with its geometry.

    ``row_type='instance'`` for label entities, ``row_type='file'`` for whole-structure
    masks, and every measurement of them alongside, as a sort key or a colour.
    """
    ndim = len(list(sample_size))
    planar = ndim == 2
    # The origin every instance's polarity is measured from. Measured here rather than only
    # carried, so `mesh` on its own writes usable geometry too; a carried value wins and is
    # the same number.
    centre = (object_center_um(volumes[object_mask_name] > 0, sample_size)
              if object_mask_name and object_mask_name in volumes
              else segmented_center_um(volumes.values(), sample_size))

    pool = WorkPool(worker_share(options.mesh_workers), what="meshing")
    try:
        return _rows(volumes, kinds, sample_size, object_id, group_id, options, metrics,
                     ndim, planar, centre, pool)
    finally:
        pool.shutdown()


def _rows(volumes, kinds, sample_size, object_id, group_id, options, metrics,
          ndim, planar, centre, pool):
    """The body of mesh_rows_for_object, with a pool open for the instance geometry."""
    rows: List[Dict[str, Any]] = []
    for name, volume in volumes.items():
        kind = kinds[name]
        if kind != "label":
            binary = volume > 0
            if not binary.any():
                continue
            # Not `metrics`: that parameter holds the per-instance values to carry, and
            # rebinding it here left every label row without them.
            shape = region_metrics_for(object_id, name, volume, sample_size)
            extent = _bbox_extent(binary)
            roundness = shape.get("sphericity", shape.get("circularity", float("nan")))
            rows.append({
                **shape,
                "object_id": object_id, "group_id": group_id, "entity_name": name,
                "entity_kind": kind, "row_type": "file", "label_id": None,
                "spatial_dims": ndim,
                "surface_kind": "mesh",
                "surface": b"" if planar else generate_mesh(
                    binary, (0, 0, 0), sample_size,
                    step_size=options.step_size,
                    smooth_sigma=sigma_for_shape(
                        roundness, float(binary.sum() / extent) if extent else float("nan"),
                        sigma_min=0.3, sigma_max=options.smooth_sigma * 2),
                    target_reduction=options.target_reduction, level=options.level,
                    max_vertices=options.max_vertices,
                    surface_method=options.surface_method,
                ),
                "outline": generate_outline(binary, (0, 0), sample_size) if planar else b"",
                "skeleton": b"",
            })
            continue

        # The view itself, not a contiguous copy of it: label_metrics makes whatever
        # ITK needs, and passing the same array the instance measurer passed is what
        # lets the per-object cache recognise the two calls as the same work.
        labels = volume
        props = regions_for(object_id, name, labels)
        if not props:
            continue
        measured = label_metrics_for(object_id, name, labels, sample_size)
        shape_keys = METRICS_2D if ndim == 2 else METRICS_3D
        skeletons = (
            skeletons_for(object_id, name, labels, sample_size,
                          options.max_skeleton_voxels)
            if wants_skeletons(name, options.geometry_as, options.skeletons)
            else {}
        )
        # What this structure's instances are stored as: forced for the whole entity, or
        # decided per instance from its own measured shape.
        told = forced_surface(name, options.geometry_as)
        # In batches, so the pool always has work but the cropped masks in flight never add
        # up to another copy of the object.
        for batch in batched(props, _GEOMETRY_BATCH):
            pending: List[Dict[str, Any]] = []
            tasks: List[Tuple[Any, ...]] = []
            oversized: List[bool] = []
            # Row index in `pending` per task: a parametric surface needs no task at all,
            # which is most of them and most of the run's meshing.
            task_rows: List[int] = []
            for rp in batch:
                stats = measured.get(int(rp.label), {})
                shape_metrics = {key: stats.get(key, float("nan")) for key in shape_keys}
                origin = tuple(int(v) for v in rp.bbox[:ndim])
                bbox_extent = float(np.prod([hi - lo for lo, hi
                                             in zip(rp.bbox[:ndim], rp.bbox[ndim:])]))
                fill_ratio = rp.area / bbox_extent if bbox_extent > 0 else float("nan")
                roundness = shape_metrics.get("sphericity",
                                              shape_metrics.get("circularity", float("nan")))
                carried = (metrics or {}).get((name, int(rp.label))) or {}
                skeleton = skeletons.get(int(rp.label))
                skeleton_shape = _skeleton_metrics(skeleton)
                # A plane's surface is its outline, and neither primitive is a 2D shape, so
                # the selector only runs on volumes.
                surface_kind = "mesh" if planar else (told or choose_surface(
                    roundness, shape_metrics.get("aspect_ratio_major_minor"),
                    skeleton_shape.get("branches"), has_skeleton=skeleton is not None,
                ))
                surface = surface_of(surface_kind, stats, skeleton)
                # An ellipsoid needs finite moments and a tube a centre line; an instance
                # with neither is still a shape somebody wants to see.
                if surface_kind != "mesh" and not surface:
                    surface_kind = "mesh"
                pending.append({
                    **_polarity(stats.get("centroid_um"), centre),
                    **skeleton_shape,
                    **{k: v for k, v in carried.items() if k in CARRIED_METRICS},
                    **shape_metrics,
                    "object_id": object_id, "group_id": group_id, "entity_name": name,
                    "entity_kind": kind, "row_type": "instance", "label_id": int(rp.label),
                    "spatial_dims": ndim,
                    "surface_kind": surface_kind,
                    "surface": surface,
                    "outline": b"",
                    "skeleton": skeleton_payload(skeleton),
                })
                if surface_kind != "mesh":
                    continue
                image = rp.image.astype(bool)
                task_rows.append(len(pending) - 1)
                tasks.append((
                    image, origin, tuple(sample_size), planar,
                    options.step_size,
                    sigma_for_shape(roundness, fill_ratio, sigma_min=0.3,
                                    sigma_max=options.smooth_sigma * 2),
                    options.target_reduction, options.level, options.max_vertices,
                    options.surface_method,
                ))
                oversized.append(image.size > _INLINE_INSTANCE_SAMPLES)

            # The big ones here, the rest in the pool, and the results put back in order.
            geometry: List[Optional[Tuple[bytes, bytes]]] = [None] * len(tasks)
            farmed = [i for i, big in enumerate(oversized) if not big]
            for i, big in enumerate(oversized):
                if big:
                    geometry[i] = _instance_geometry(tasks[i])
            for i, done in zip(farmed, pool.map(_instance_geometry,
                                                [tasks[i] for i in farmed],
                                                total=len(props))):
                geometry[i] = done
            for task_index, payloads in enumerate(geometry):
                row = pending[task_rows[task_index]]
                row["surface"], row["outline"] = payloads
            rows.extend(pending)

    if options.contact_max_um is not None:
        rows.extend(_contact_rows(volumes, kinds, sample_size, object_id, group_id,
                                  options.contact_max_um))
    return rows


def _polarity(centroid_um: Optional[Sequence[float]],
              centre: Optional[Sequence[float]]) -> Dict[str, Any]:
    """Direction and distance from the object centre to this instance, or nothing."""
    if centroid_um is None or centre is None or len(centroid_um) != len(centre):
        return {}
    return polarity_from_offset([c - o for c, o in zip(centroid_um, centre)])


def _skeleton_metrics(skeleton) -> Dict[str, Any]:
    """Branches, length and tortuosity of a skeleton this run already computed."""
    if skeleton is None:
        return {}
    return skeleton_graph_metrics(skeleton)


def _contact_rows(
    volumes: Mapping[str, np.ndarray],
    kinds: Mapping[str, str],
    sample_size: Sequence[float],
    object_id: str,
    group_id: str,
    max_gap_um: float,
) -> List[Dict[str, Any]]:
    """The touching pairs, in the row shape the 3D viewer splits out on load.

    Both sides of a pair are instances of one structure, so ``entity_b`` repeats
    ``entity_a``; both columns stay, so every contact row has the same shape.
    """
    contacts = contacts_for(object_id, volumes, kinds, sample_size, max_gap_um)
    return [
        {
            "object_id": object_id, "group_id": group_id, "row_type": "contact",
            "entity_a": entity, "label_a": label_a,
            "entity_b": entity, "label_b": label_b,
            "gap_um": gap_um, "surface": b"", "skeleton": b"",
        }
        for entity, label_a, label_b, gap_um in contacts
    ]


def _maybe_int(value: Any) -> Optional[int]:
    """An id as an int, or None for the rows that have none (masks, contacts)."""
    if value is None or value == "":
        return None
    number = float(value)
    return None if math.isnan(number) else int(number)


def write_geometry_parquet(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write geometry.parquet: the payloads as BLOBs, zstd-compressed by parquet itself.

    An explicit schema, so every object writes the same columns whether or not it has
    anything in them - an object whose instances all failed to mesh would otherwise leave
    polars a column of nothing to guess a type from.
    """
    import polars as pl

    types = {
        name: (pl.Utf8 if name in _TEXT_FIELDS else
               pl.Int64 if name in _INT_FIELDS else
               pl.Binary if name in _BLOB_FIELDS else pl.Float64)
        for name in GEOMETRY_COLUMNS
    }
    surface_counted = [surface_counts(str(r.get("surface_kind") or "mesh"),
                                      r.get("surface") or b"") for r in rows]
    skeleton_counts = [payload_counts(r.get("skeleton") or b"") for r in rows]
    outline_counts = [payload_counts(r.get("outline") or b"") for r in rows]
    header_counts = {
        "surface_vertices": [c[0] for c in surface_counted],
        "surface_elements": [c[1] for c in surface_counted],
        "skeleton_vertices": [c[0] for c in skeleton_counts],
        "skeleton_edges": [c[1] for c in skeleton_counts],
        "outline_vertices": [c[0] for c in outline_counts],
        "outline_edges": [c[1] for c in outline_counts],
    }
    columns: Dict[str, List[Any]] = {}
    for name in GEOMETRY_COLUMNS:
        if name in header_counts:
            columns[name] = header_counts[name]
        elif name in _BLOB_FIELDS:
            # NULL rather than an empty blob: "has geometry" is then a plain IS NOT NULL.
            columns[name] = [(r.get(name) or None) for r in rows]
        elif name in _INT_FIELDS:
            columns[name] = [_maybe_int(r.get(name)) for r in rows]
        else:
            columns[name] = [r.get(name) for r in rows]

    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(columns, schema=types).write_parquet(path, compression="zstd")
    return path


def write_geometry(object_dir: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write one object's geometry into its own folder, and say where it went."""
    return write_geometry_parquet(object_dir / GEOMETRY_FILENAME, rows)
