"""How an instance is drawn: a marching-cubes mesh, an outline in a plane, or a far smaller primitive.

    ellipsoid   round and compact          15 floats, drawn from one shared sphere
    tube        elongated, one branch      a centre line with a radius per node
    mesh        everything else            marching cubes on a signed distance field

The kind follows from measurements the report already carries. Nothing measured depends on any of it: the surface is
only ever drawn.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Sequence

import edt
import fast_simplification
import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.measure import approximate_polygon, find_contours, marching_cubes

from organella.geometry.payloads import quantised_payload

log = logging.getLogger(__name__)

# Rounder than this is within a few percent of its own ellipsoid, unless it is also elongated: roundness alone cannot
# tell a sphere from a smooth short rod, which an ellipsoid of the same moments draws too fat in the middle.
ELLIPSOID_MIN_SPHERICITY = 0.75
ELLIPSOID_MAX_ASPECT = 3.0
# Only small instances: its coarse sphere and its mismatch with the real outline show on a big one, a nucleus above all.
ELLIPSOID_MAX_VOLUME_UM3 = 0.5
# A blob's skeleton is one branch the length of its own diameter, which a tube would draw as a stubby cylinder.
TUBE_MIN_ASPECT = 3.0
# Below this an aspect ratio is a collapsed measurement, not a flat shape.
ASPECT_FLOOR = 1.0
# How hard fast_simplification may collapse an edge (0 to 10; its own default is 7). On thin convoluted networks, ER
# sheets one to three voxels thick, 7 cracked the sheets into shards at the 80% reduction every mesh gets.
DECIMATION_AGGRESSION = 3
# Mesh decimation is not safe to run in two threads at once: some meshes came back as the wrong surface. It is the last and
# cheapest stage of a mesh, so the rest still runs in parallel.
_DECIMATION_LOCK = threading.Lock()
SURFACE_KINDS = ("mesh", "ellipsoid", "tube")


def choose_surface(sphericity: float | None, aspect: float | None, branches: float | None, has_skeleton: bool,
                   volume_um3: float | None) -> str:
    """Which kind of surface to store for one instance. A missing metric falls through to a mesh, which draws the
    voxels it has where a primitive chosen on a NaN would be wrong about the shape."""
    def known(value) -> bool:
        return value is not None and not (isinstance(value, float) and math.isnan(value))

    if known(aspect) and aspect < ASPECT_FLOOR:
        aspect = None
    if has_skeleton and known(aspect) and aspect >= TUBE_MIN_ASPECT and known(branches) and branches >= 1:
        return "tube"
    if (known(sphericity) and sphericity >= ELLIPSOID_MIN_SPHERICITY and known(aspect) and aspect <= ELLIPSOID_MAX_ASPECT
            and known(volume_um3) and volume_um3 <= ELLIPSOID_MAX_VOLUME_UM3):
        return "ellipsoid"
    return "mesh"


def sigma_for_shape(sphericity: float, fill_ratio: float, sigma_min: float = 0.3, sigma_max: float = 1.5) -> float:
    """Gaussian sigma from shape: blobs get more smoothing, thin structures less. From roundness and the fill ratio
    (voxels over box voxels, which catches curved filaments that look compact in PCA but are sparse)."""
    if math.isnan(sphericity) or math.isnan(fill_ratio):
        return (sigma_min + sigma_max) / 2.0
    blob = math.sqrt(sphericity * min(1.0, fill_ratio / (math.pi / 6.0)))
    return sigma_min + (sigma_max - sigma_min) * blob


def instance_sigma(option: float | Sequence[float], sphericity: float, fill_ratio: float):
    """The option's smoothing for one instance, shape-scaled: a number, or one per axis. 0 is no smoothing at all."""
    def one(value: float) -> float:
        return 0.0 if value <= 0 else sigma_for_shape(sphericity, fill_ratio, sigma_min=0.3, sigma_max=value * 2)

    return one(float(option)) if isinstance(option, (int, float)) else tuple(one(float(v)) for v in option)


def _distance_to_background(mask: np.ndarray, spacing: Sequence[float]) -> np.ndarray:
    """µm from every True voxel of ``mask`` to the nearest False one, float32."""
    return edt.edt(np.ascontiguousarray(mask), anisotropy=tuple(float(v) for v in spacing), black_border=False)


def _decimated(xyz: np.ndarray, faces: np.ndarray, reduction: float, **options) -> tuple[np.ndarray, np.ndarray]:
    with _DECIMATION_LOCK:
        xyz, faces = fast_simplification.simplify(xyz, faces.astype(int), target_reduction=reduction, verbose=False, **options)
    return xyz.astype(np.float32), faces


def generate_mesh(binary: np.ndarray, origin_zyx: Sequence[int], voxel_um: Sequence[float], step_size: int = 1,
                  smooth_sigma: float | Sequence[float] = 0.7, target_reduction: float = 0.8,
                  level: float | None = None, max_vertices: int = 0) -> bytes:
    """Mesh a (Z, Y, X) mask by marching cubes on a signed distance field, as a quantised payload; empty if it fails.

    The surface is the zero level of inside minus outside distance, optionally smoothed, which sits at the true voxel
    boundary: thin structures survive, and blurring the binary would inflate the volume.
    """
    if binary.sum() < 8:
        return b""
    try:
        spacing = tuple(float(v) for v in voxel_um)
        finest, coarsest = min(spacing), max(spacing)
        # One sigma is in the finest voxel: the same blur in µm along every axis. Three are each in voxels of their axis.
        if isinstance(smooth_sigma, (int, float)):
            sigma_um = (max(0.0, float(smooth_sigma)) * finest,) * len(spacing)
        else:
            sigma_um = tuple(max(0.0, float(v)) * s for v, s in zip(smooth_sigma, spacing))
        # Padded with background so the field settles to "outside" before the border.
        pads = tuple(1 if su <= 0 else int(math.ceil(3.0 * su / s)) + 1 for su, s in zip(sigma_um, spacing))
        padded = np.pad(binary.astype(bool), [(p, p) for p in pads])
        inside = _distance_to_background(padded, spacing)
        field = inside - _distance_to_background(~padded, spacing)
        # Neither knob may exceed what the instance can carry: a kernel wider than half its inscribed radius flattens
        # the field, and a coarser step walks over it.
        radius_um = float(inside.max())
        sigma_um = tuple(min(su, radius_um / 2.0) for su in sigma_um)
        step = max(1, min(step_size, int(radius_um / coarsest) - 1))
        if any(su > 0 for su in sigma_um):
            field = gaussian_filter(field, sigma=tuple(su / s for su, s in zip(sigma_um, spacing)))
        vertices, faces, _, _ = marching_cubes(field, level=0.0 if level is None else level, step_size=step)
        vertices = vertices - np.asarray(pads)
        oz, oy, ox = origin_zyx
        sz, sy, sx = spacing
        xyz = np.column_stack([(vertices[:, 2] + ox) * sx, (vertices[:, 1] + oy) * sy,
                               (vertices[:, 0] + oz) * sz]).astype(np.float32)
        if target_reduction > 0 and len(faces) > 100:
            xyz, faces = _decimated(xyz, faces, target_reduction, agg=DECIMATION_AGGRESSION)
        if max_vertices and len(xyz) > max_vertices and len(faces) > 100:  # a fixed fraction bounds nothing, so cap the count
            xyz, faces = _decimated(xyz, faces, 1.0 - max_vertices / len(xyz))
        return quantised_payload(xyz, faces.astype(np.uint32))
    except Exception as error:  # a speck too small to enclose a surface is ordinary
        log.debug("meshing failed (%s); emitting no geometry", error)
        return b""


def generate_outline(binary: np.ndarray, origin_yx: Sequence[int], voxel_um: Sequence[float]) -> bytes:
    """Trace a (Y, X) mask's boundary as closed line loops, in the same payload a skeleton overlay uses with z at 0.
    Holes come out as loops of their own."""
    if binary.sum() < 1:
        return b""
    try:
        loops = find_contours(np.pad(binary.astype(float), 1), level=0.5)  # padded so a box-filling instance still closes
        oy, ox = origin_yx
        sy, sx = (float(v) for v in voxel_um)
        vertices, edges, offset = [], [], 0
        for loop in loops:
            loop = loop - 1.0
            if len(loop) < 3:
                continue
            if np.allclose(loop[0], loop[-1]):  # find_contours repeats the first point to close a ring
                loop = loop[:-1]
            n = len(loop)
            vertices.append(np.column_stack([(loop[:, 1] + ox) * sx, (loop[:, 0] + oy) * sy, np.zeros(n)]).astype(np.float32))
            edges.append((np.column_stack([np.arange(n), (np.arange(n) + 1) % n]) + offset).astype(np.uint32))
            offset += n
        return quantised_payload(np.vstack(vertices), np.vstack(edges)) if vertices else b""
    except Exception as error:
        log.debug("outlining failed (%s); emitting no geometry", error)
        return b""
