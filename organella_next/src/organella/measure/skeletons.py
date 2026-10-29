"""Curve skeletons of instances: how many branches, how long, how straight, and where they end.

Traced one instance at a time in its own box: kimimaro's TEASAR in a volume, a thinning in a plane. Every number
is relative to the data, so a thick mitochondrion and a thin ER tube each get a reach in proportion to their own
width.
"""
from __future__ import annotations

from dataclasses import dataclass

import dask
import dask.array as da
import kimimaro
import numpy as np
from dask.delayed import Delayed
from skimage.morphology import skeletonize

from organella.measure.windows import box

# Below this many voxels there is nothing to trace.
DUST_VOXELS = 2
# 8-connected neighbour offsets, each pair counted once: the edges of a 2D skeleton.
_PLANAR_NEIGHBOURS = ((0, 1), (1, 0), (1, 1), (1, -1))


@dataclass(frozen=True)
class SkeletonParams:
    """TEASAR traces a path, rules out everything within ``scale`` radii plus ``const_voxels`` of it as covered,
    and traces again from what is left. Too small a reach and one tube is traced again and again from its own
    wall. ``prune`` cuts terminal branches shorter than that many radii at the junction they leave."""

    scale: float = 3.0
    const_voxels: float = 6.0
    prune: float = 2.0


class Traced:
    """What one instance's skeleton comes to: its metrics and its tips (µm, array order). Opaque to dask."""

    __slots__ = ("branches", "length_um", "tortuosity", "tips", "vertices", "edges", "radii")

    def __init__(self, branches: float, length_um: float, tortuosity: float, tips: np.ndarray,
                 vertices=None, edges=None, radii=None) -> None:
        self.branches, self.length_um, self.tortuosity, self.tips = branches, length_um, tortuosity, tips
        self.vertices, self.edges, self.radii = vertices, edges, radii  # the whole skeleton, kept only when drawn


def _unmeasured(ndim: int) -> Traced:
    return Traced(np.nan, np.nan, np.nan, np.zeros((0, ndim)))


class _Polyline:
    """A 2D skeleton read the way kimimaro's is: vertices, edges, length."""

    __slots__ = ("vertices", "edges")

    def __init__(self, vertices: np.ndarray, edges: np.ndarray) -> None:
        self.vertices, self.edges = vertices, edges

    def cable_length(self) -> float:
        if len(self.edges) == 0:
            return 0.0
        return float(np.linalg.norm(self.vertices[self.edges[:, 0]] - self.vertices[self.edges[:, 1]], axis=1).sum())


def prune_spurs(skeleton, factor: float):
    """Cut the terminal branches shorter than ``factor`` radii, in place; the skeleton back.

    A terminal branch runs from a tip to the junction it leaves, and its length is judged against the radius at that
    junction. Short ones go first, and a junction that is down to two branches is not a junction any more, so the
    longest of the rest are never cut. A skeleton that is one unbranched path has no junction and is left alone.
    """
    vertices, edges = np.asarray(skeleton.vertices), np.asarray(skeleton.edges)
    radii = getattr(skeleton, "radii", None)
    if factor <= 0 or len(edges) == 0 or radii is None or len(radii) != len(vertices):
        return skeleton
    pos, radius = vertices.astype(np.float64), np.asarray(radii, dtype=np.float64)
    neighbours: list[set[int]] = [set() for _ in range(len(vertices))]
    for a, b in edges:
        neighbours[int(a)].add(int(b))
        neighbours[int(b)].add(int(a))

    removed: set[int] = set()
    while True:
        spurs = []
        for tip in range(len(neighbours)):
            if len(neighbours[tip]) != 1 or tip in removed:
                continue
            path, previous, here, length = [tip], None, tip, 0.0
            while len(neighbours[here]) <= 2:
                step = [n for n in neighbours[here] if n != previous]
                if not step:
                    break
                previous, here = here, step[0]
                length += float(np.linalg.norm(pos[here] - pos[previous]))
                path.append(here)
            if len(neighbours[here]) >= 3 and length < factor * radius[here]:
                spurs.append((length, path))
        cut = False
        for _, path in sorted(spurs, key=lambda spur: spur[0]):
            junction = path[-1]
            if len(neighbours[junction]) < 3:
                continue
            for vertex in path[:-1]:
                for n in neighbours[vertex]:
                    neighbours[n].discard(vertex)
                neighbours[vertex] = set()
                removed.add(vertex)
            cut = True
        if not cut:
            break

    if not removed:
        return skeleton
    keep = np.array([v for v in range(len(vertices)) if v not in removed])
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    kept = np.array([(remap[a], remap[b]) for a, b in edges if a not in removed and b not in removed], dtype=edges.dtype)
    skeleton.vertices, skeleton.edges, skeleton.radii = vertices[keep], kept.reshape(-1, 2), np.asarray(radii)[keep]
    if getattr(skeleton, "vertex_types", None) is not None:
        skeleton.vertex_types = np.asarray(skeleton.vertex_types)[keep]
    return skeleton


def _branch_segments(adjacent: list[list[int]], degree: np.ndarray) -> list[list[int]]:
    """Split a skeleton graph into maximal chains between nodes of degree other than 2."""
    def key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    seen: set[tuple[int, int]] = set()
    segments: list[list[int]] = []
    breakpoints = [v for v in range(len(adjacent)) if adjacent[v] and degree[v] != 2]
    if not breakpoints:  # a closed loop has none; seed from any vertex so it still yields one segment
        breakpoints = [v for v in range(len(adjacent)) if adjacent[v]][:1]
    for start in breakpoints:
        for neighbour in adjacent[start]:
            if key(start, neighbour) in seen:
                continue
            seen.add(key(start, neighbour))
            path, previous, current = [start, neighbour], start, neighbour
            while degree[current] == 2:
                following = [x for x in adjacent[current] if x != previous]
                if not following or key(current, following[0]) in seen:
                    break
                seen.add(key(current, following[0]))
                path.append(following[0])
                previous, current = current, following[0]
            segments.append(path)
    return segments


def _smoothed(points: np.ndarray, window: int = 2, passes: int = 2) -> np.ndarray:
    """A moving average along a polyline, ends fixed: it removes the voxel staircase that would inflate tortuosity."""
    if len(points) <= 2:
        return points
    p = points.astype(np.float64, copy=True)
    for _ in range(passes):
        q = p.copy()
        for i in range(1, len(p) - 1):
            q[i] = p[max(0, i - window):min(len(p), i + window + 1)].mean(axis=0)
        p = q
    return p


def graph_metrics(skeleton) -> tuple[float, float, float]:
    """``(branches, cable length in µm, tortuosity)`` of a skeleton.

    Branches are chains between tips and junctions. Tortuosity is the length-weighted mean of arc over chord per
    branch on the smoothed path (1 is straight).
    """
    if skeleton is None or len(skeleton.vertices) == 0 or len(skeleton.edges) == 0:
        return 0, 0.0, np.nan
    vertices, edges = np.asarray(skeleton.vertices, dtype=np.float64), np.asarray(skeleton.edges)
    degree = np.bincount(edges.reshape(-1), minlength=len(vertices))
    adjacent: list[list[int]] = [[] for _ in range(len(vertices))]
    for a, b in edges:
        adjacent[int(a)].append(int(b))
        adjacent[int(b)].append(int(a))
    segments = _branch_segments(adjacent, degree)
    total_arc = weighted = 0.0
    for path in segments:
        points = _smoothed(vertices[path])
        arc = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
        chord = float(np.linalg.norm(points[-1] - points[0]))
        if arc > 0 and chord > 0:
            weighted += arc * (arc / chord)
            total_arc += arc
    return len(segments), float(skeleton.cable_length()), weighted / total_arc if total_arc > 0 else np.nan


def tips_um(skeleton) -> np.ndarray:
    """Where a skeleton stops: its degree-1 vertices. A closed loop, or a lone vertex, has none."""
    vertices = np.asarray(getattr(skeleton, "vertices", np.zeros((0, 3))), dtype=np.float64)
    edges = np.asarray(getattr(skeleton, "edges", np.zeros((0, 2), dtype=int)))
    if not len(vertices) or not edges.size:
        return vertices[:0]
    return vertices[np.bincount(edges.reshape(-1).astype(np.int64), minlength=len(vertices)) == 1]


def _teasar(params: SkeletonParams, voxel_um: tuple[float, ...]) -> dict:
    """kimimaro's parameters for this resolution; its soma handling targets neurons, not organelles."""
    return {"scale": params.scale, "const": params.const_voxels * max(voxel_um), "pdrf_scale": 100000,
            "pdrf_exponent": 4, "soma_detection_threshold": 1e9, "soma_acceptance_threshold": 1e9}


def _trace_volume(window: np.ndarray, label: int, origin: np.ndarray, voxel_um: tuple[float, ...],
                  params: SkeletonParams, max_voxels: int | None):
    """One instance's centre line, in the volume's frame. The window is grown by a voxel wherever the volume has one
    to spare, so only an instance that really reaches the volume's face is anchored there."""
    crop = np.asfortranarray(window)
    crop[crop != label] = 0  # the box holds whatever else passes through it, and kimimaro traces every id
    if max_voxels is not None and int(np.count_nonzero(crop)) > max_voxels:
        return "oversize"
    skeleton = kimimaro.skeletonize(
        crop, teasar_params=_teasar(params, voxel_um), anisotropy=voxel_um, dust_threshold=DUST_VOXELS,
        fix_branching=True, fix_borders=True, progress=False, parallel=1, in_place=True,
    ).get(label)
    if skeleton is None:
        return None
    # Into the volume's frame in voxels first: a vertex is a voxel centre, so the micrometres that come back are the
    # ones a trace of the whole volume would have given.
    anisotropy = np.asarray(voxel_um, dtype=np.float32)
    voxels = np.rint(np.asarray(skeleton.vertices, dtype=np.float64) / anisotropy) + origin
    skeleton.vertices = voxels.astype(np.float32) * anisotropy
    return prune_spurs(skeleton, params.prune)


def _trace_plane(window: np.ndarray, label: int, origin: np.ndarray, voxel_um: tuple[float, ...],
                 max_voxels: int | None):
    """One instance's medial axis in a plane: TEASAR is 3D only, so it is a thinning, with an edge between every
    8-connected pair so a diagonal step costs the square root of two pixels."""
    region = window == label
    count = int(region.sum())
    if (max_voxels is not None and count > max_voxels):
        return "oversize"
    if count < DUST_VOXELS:
        return None
    coords = np.argwhere(skeletonize(region))
    if not len(coords):
        return _Polyline(np.zeros((0, 2)), np.zeros((0, 2), dtype=int))
    index = {(int(y), int(x)): i for i, (y, x) in enumerate(coords)}
    edges = [(i, index[(int(y) + dy, int(x) + dx)]) for i, (y, x) in enumerate(coords)
             for dy, dx in _PLANAR_NEIGHBOURS if (int(y) + dy, int(x) + dx) in index]
    vertices = (coords + origin).astype(np.float64) * np.asarray(voxel_um, dtype=float)
    return _Polyline(vertices, np.asarray(edges, dtype=int).reshape(-1, 2))


def _summarise(window: np.ndarray, label: int, origin: np.ndarray, voxel_um: tuple[float, ...],
               params: SkeletonParams, max_voxels: int | None, keep: bool) -> Traced:
    ndim = len(voxel_um)
    traced = (_trace_volume(window, label, origin, voxel_um, params, max_voxels) if ndim == 3
              else _trace_plane(window, label, origin, voxel_um, max_voxels))
    if isinstance(traced, str):  # over the size cap: not measured, which is not the same as no branches
        return _unmeasured(ndim)
    branches, length, tortuosity = graph_metrics(traced)
    if traced is None:
        return Traced(branches, length, tortuosity, np.zeros((0, ndim)))
    whole = (np.asarray(traced.vertices), np.asarray(traced.edges), getattr(traced, "radii", None)) if keep else (None,) * 3
    return Traced(branches, length, tortuosity, tips_um(traced), *whole)


def lazy_skeleton(data: da.Array, low: np.ndarray, high: np.ndarray, label: int, voxel_um: tuple[float, ...],
                  params: SkeletonParams, max_voxels: int | None, keep: bool = False) -> Delayed:
    pad = 1 if len(low) == 3 else 0
    origin = np.maximum(low - pad, 0)
    return dask.delayed(_summarise)(box(data, low, high, pad=pad), label, origin, voxel_um, params, max_voxels, keep)
