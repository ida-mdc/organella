"""A big mesh as several levels of detail, each cut into spatial fragments the viewer fetches by byte range.

Level 0 is the mesh as meshed; every further level is the one before it decimated to about a quarter of the vertices,
down to a few thousand. A level is cut on a regular grid, each triangle going to the cell holding its centroid, so a
fragment is a small mesh of its own and the fragments of one level together are the whole surface. The grids are nested
(one origin, a cell twice as wide per level), which makes the fragments a tree. Neighbouring fragments of different
levels do not meet exactly: the cracks are the price of loading only what is close.

Fragments are ``quantised_payload`` bytes, raw-deflated one by one so a range read decodes without its neighbours, and
concatenated in one ``meshes.bin`` per object. The manifest that says where each one is stays small enough to live in the
geometry file, in the row of the mesh it describes.
"""
from __future__ import annotations

import json
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from organella.geometry import workers
from organella.geometry.payloads import decode_quantised, payload_counts, quantised_payload
from organella.geometry.surfaces import DECIMATION_AGGRESSION, _decimated

FILENAME = "meshes.bin"
# Where a row carries the coarser meshes the writer made from the distance field, until its levels are built.
COARSER = "_coarser"
MANIFEST_VERSION = 1

# A mesh with fewer vertices than this is not worth fragmenting: one payload in the geometry file is as cheap.
MIN_VERTICES = 6_000
# What a fragment aims at, and where the levels stop. Fragments with 8-16k vertices are a few tens of kB deflated.
FRAGMENT_VERTICES = 12_000
COARSEST_VERTICES = 2_000
# A whole-structure mask is one mesh for the whole cell, so its stored surface (what is drawn before any fragment arrives) is the
# finest level with this many vertices: a few thousand is a handful of triangles across the cell. An instance keeps the coarsest.
MASK_STORED_VERTICES = 200_000
# Each level keeps about this share of the vertices of the one before.
LEVEL_KEEP = 0.25
MAX_LEVELS = 8
# How much of the way to the next level's edge length counts as "the error" of a level: the 95th percentile of its edge
# lengths, because a few long slivers say nothing about how coarse the surface looks.
ERROR_PERCENTILE = 95
# A level is kept only if it still looks like the one before it: it keeps this share of its surface area, and it is not in
# more pieces. Simplifying a thin double-sided sheet (an ER network) past the point where its two sides meet loses both.
MIN_AREA_KEPT = 0.85
MAX_PIECES_GROWTH = 1.1
# A level is worth having only if it is clearly smaller than the one before: a simplifier that stalls leaves 24k, 18k, 16k
# vertices of nearly the same mesh, which are three levels to fetch and none to choose between.
MAX_LEVEL_SIZE = 0.6
# A level sampled from the volume must keep this share of the finest level's surface area (it is taken a little outside the
# true surface, so it can have more).
MIN_COARSE_AREA = 0.3
# And a level is not kept if more than this share of its edges are folded over (see _folded): the finest levels have next to none.
MAX_FOLDED = 0.01
# Under this many faces the share of folded edges is a handful of edges and says nothing, so the gate leaves such a level alone.
MIN_FACES_FOR_FOLD_GATE = 1000


@dataclass
class Fragment:
    cell: tuple[int, int, int]
    payload: bytes            # deflated quantised payload
    vertices: int
    triangles: int


@dataclass
class Level:
    error_um: float
    origin: tuple[float, float, float]
    cell_um: tuple[float, float, float]
    fragments: list[Fragment] = field(default_factory=list)


def _edge_error(xyz: np.ndarray, faces: np.ndarray) -> float:
    corners = xyz[faces]
    edges = np.concatenate([np.linalg.norm(corners[:, a] - corners[:, b], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))])
    return float(np.percentile(edges, ERROR_PERCENTILE)) if len(edges) else 0.0


def _area(xyz: np.ndarray, faces: np.ndarray) -> float:
    corners = xyz[faces].astype(np.float64)
    return float(0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum())


def _pieces(xyz: np.ndarray, faces: np.ndarray) -> int:
    """How many separate connected surfaces the triangles make up."""
    n = len(xyz)
    graph = coo_matrix((np.ones(len(faces) * 3), (np.r_[faces[:, 0], faces[:, 1], faces[:, 2]], np.r_[faces[:, 1], faces[:, 2], faces[:, 0]])),
                       shape=(n, n))
    labels = connected_components(graph, directed=False)[1]
    return int(np.unique(labels[np.unique(faces)]).size)


def _folded(xyz: np.ndarray, faces: np.ndarray) -> float:
    """The share of edges (those with two faces) across which the surface turns back on itself, by more than 140 degrees: a
    smooth surface has none, and a mesh wrecked by collapsing or merging vertices has a great many."""
    faces = np.asarray(faces, dtype=np.int64)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    if len(faces) < 2:
        return 0.0
    corners = xyz[faces].astype(np.float64)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-18)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    owner = np.tile(np.arange(len(faces)), 3)
    key = np.minimum(edges[:, 0], edges[:, 1]).astype(np.int64) * len(xyz) + np.maximum(edges[:, 0], edges[:, 1])
    order = np.argsort(key, kind="stable")
    key, owner = key[order], owner[order]
    pairs = np.flatnonzero(key[1:] == key[:-1])
    if not len(pairs):
        return 0.0
    turn = np.einsum("ij,ij->i", normals[owner[pairs]], normals[owner[pairs + 1]])
    return float((turn < np.cos(np.radians(140.0))).mean())


def _still_the_same_surface(xyz: np.ndarray, faces: np.ndarray, smaller_xyz: np.ndarray, smaller_faces: np.ndarray) -> bool:
    """Whether the coarser mesh keeps the surface: most of its area, and no more pieces than before."""
    if len(smaller_faces) == 0:
        return False
    if _area(smaller_xyz, smaller_faces) < MIN_AREA_KEPT * _area(xyz, faces):
        return False
    if len(smaller_faces) >= MIN_FACES_FOR_FOLD_GATE and _folded(smaller_xyz, smaller_faces) > MAX_FOLDED:
        return False
    return _pieces(smaller_xyz, smaller_faces) <= MAX_PIECES_GROWTH * _pieces(xyz, faces) + 5


def _level_size_limit(vertices: int) -> float:
    """How much of the vertices the next level may still have. A small mesh may stop short of a quarter without it meaning anything."""
    return MAX_LEVEL_SIZE if vertices >= 2 * MIN_FACES_FOR_FOLD_GATE else 0.9


def _deflate(raw: bytes) -> bytes:
    packer = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)   # raw deflate: what DecompressionStream('deflate-raw') reads
    return packer.compress(raw) + packer.flush()


def _grid(xyz: np.ndarray, faces: np.ndarray, fragment_vertices: int) -> tuple[np.ndarray, float]:
    """Where the grid starts, and the side of a cell on the finest level: one that holds about ``fragment_vertices`` of the
    mesh's vertices where the surface passes through it, taking a cell the surface crosses to hold 1.5 side² of it."""
    corners = xyz[faces].astype(np.float64)
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    low, high = xyz.min(axis=0).astype(np.float64), xyz.max(axis=0).astype(np.float64)
    side = float(np.sqrt(fragment_vertices * max(area, 1e-12) / (1.5 * len(xyz))))
    return low, max(side, float((high - low).max()) / 256.0)     # never a grid of millions of cells for a thin, long mesh


def _fragments(xyz: np.ndarray, faces: np.ndarray, origin: np.ndarray, side: float) -> Level:
    """One level cut on the grid that starts at ``origin``; each triangle goes to the cell holding its centroid."""
    centroids = xyz[faces].astype(np.float64).mean(axis=1)
    cells = np.clip(np.floor((centroids - origin) / side), 0, 2 ** 20 - 1).astype(np.int64)
    keys = (cells[:, 0] << 40) | (cells[:, 1] << 20) | cells[:, 2]
    order = np.argsort(keys, kind="stable")
    keys, faces, cells = keys[order], faces[order], cells[order]
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    stops = np.r_[starts[1:], len(keys)]
    level = Level(error_um=_edge_error(xyz, faces), origin=tuple(float(v) for v in origin), cell_um=(side, side, side))
    for start, stop in zip(starts, stops):
        used, local = np.unique(faces[start:stop], return_inverse=True)
        raw = quantised_payload(xyz[used], local.reshape(-1, 3).astype(np.uint32))
        level.fragments.append(Fragment(tuple(int(v) for v in cells[start]), _deflate(raw), len(used), int(stop - start)))
    return level


def build_levels(xyz: np.ndarray, faces: np.ndarray, fragment_vertices: int = FRAGMENT_VERTICES,
                 coarsest_vertices: int = COARSEST_VERTICES,
                 coarser: list[tuple[np.ndarray, np.ndarray]] | None = None,
                 stored_vertices: int = 0) -> tuple[list[Level], tuple[np.ndarray, np.ndarray]]:
    """The levels, finest first, and the mesh the geometry file keeps as the instance's surface: the finest level with at most
    ``stored_vertices`` vertices, or the coarsest if none is that small (and with 0, always the coarsest).

    The grids are nested: one origin, and a cell twice the side of the level below it, so a coarse fragment is the union
    of at most eight finer ones and the viewer can swap one for the others anywhere on the surface without a gap or an
    overlap. A level has a quarter of the vertices of the one before and a cell of four times the area, so fragments stay
    about the same size all the way up.

    ``coarser`` are meshes of the same surface made from the volume (the distance field sampled on wider grids), finest
    first. They are the levels after the first, as far as they go: a sheet or a tube survives being sampled coarsely
    where it does not survive collapsing its edges. Once they run out, or when there are none, the last level is
    decimated further.
    """
    xyz, faces = np.asarray(xyz, dtype=np.float32), np.asarray(faces, dtype=np.int64)
    origin, side = _grid(xyz, faces, fragment_vertices)
    levels = [_fragments(xyz, faces, origin, side)]
    finest_area = _area(xyz, faces)
    stored = (xyz, faces) if len(xyz) <= stored_vertices else None
    for next_xyz, next_faces in coarser or []:
        if len(levels) >= MAX_LEVELS:
            break
        if len(next_xyz) >= len(xyz) * 0.7:
            continue
        # Sampled too coarsely for the structure (an ER network at a step wider than the gaps between its sheets), a field gives
        # a blob with a fraction of the surface: that and every grid wider than it are not levels of this surface.
        # (Not while the last level is still too big to start from: a whole cell's ER has to get small somehow, and a coarse
        # blob of it is better than a root of millions of vertices.)
        if _area(next_xyz, np.asarray(next_faces, dtype=np.int64)) < MIN_COARSE_AREA * finest_area and len(xyz) <= MASK_STORED_VERTICES:
            break
        if len(next_faces) >= MIN_FACES_FOR_FOLD_GATE and _folded(np.asarray(next_xyz, dtype=np.float32), next_faces) > MAX_FOLDED:
            continue
        xyz, faces = np.asarray(next_xyz, dtype=np.float32), np.asarray(next_faces, dtype=np.int64)
        levels.append(_fragments(xyz, faces, origin, side * 2 ** len(levels)))
        stored = stored or ((xyz, faces) if len(xyz) <= stored_vertices else None)
    # With meshes from the volume the ladder is theirs: they run to the widest grid that still holds the surface, and
    # simplifying the last of them would only add the folds that sampling does not have.
    while not coarser and len(xyz) > coarsest_vertices and len(levels) < MAX_LEVELS and len(faces) > 100:
        smaller_xyz, smaller_faces = _decimated(xyz, faces, 1.0 - LEVEL_KEEP, agg=DECIMATION_AGGRESSION)
        if len(smaller_xyz) >= len(xyz) * _level_size_limit(len(xyz)) or not _still_the_same_surface(xyz, faces, smaller_xyz, smaller_faces):
            break      # simplifying stalls, or wrecks the surface (folds, lost area, pieces): this is the coarsest level it can have
        xyz, faces = smaller_xyz, smaller_faces.astype(np.int64)
        levels.append(_fragments(xyz, faces, origin, side * 2 ** len(levels)))
        stored = stored or ((xyz, faces) if len(xyz) <= stored_vertices else None)
    return levels, stored or (xyz, faces)


def coarsest_payload(xyz: np.ndarray, faces: np.ndarray) -> bytes:
    """The surface the geometry file keeps for a multires instance: the coarsest level, whole and not deflated."""
    return quantised_payload(np.asarray(xyz, dtype=np.float32), np.asarray(faces, dtype=np.uint32))


class MeshFile:
    """Appends the fragments of many meshes to one ``meshes.bin`` and hands back each mesh's manifest."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self._out, self._offset = path, path.open("wb"), 0

    def add(self, levels: list[Level]) -> bytes:
        described = []
        for level in levels:
            fragments = []
            for fragment in level.fragments:
                fragments.append({"ijk": list(fragment.cell), "off": self._offset, "size": len(fragment.payload),
                                  "nv": fragment.vertices, "nt": fragment.triangles})
                self._out.write(fragment.payload)
                self._offset += len(fragment.payload)
            described.append({"error": level.error_um, "origin": list(level.origin), "cell": list(level.cell_um),
                              "fragments": fragments})
        return json.dumps({"v": MANIFEST_VERSION, "file": FILENAME, "levels": described}, separators=(",", ":")).encode()

    def close(self) -> None:
        self._out.close()

    def __enter__(self) -> "MeshFile":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def read_fragment(path: Path, entry: dict) -> tuple[np.ndarray, np.ndarray]:
    """One fragment back out of ``meshes.bin``: what the viewer does with a byte range, for tests and checks."""
    with path.open("rb") as handle:
        handle.seek(entry["off"])
        raw = zlib.decompress(handle.read(entry["size"]), -zlib.MAX_WBITS)
    return decode_quantised(raw)


def _is_big(row: dict, min_vertices: int) -> bool:
    payload = row.get("surface")
    if row.get("surface_kind") != "mesh" or not payload:
        return False
    vertices, _ = payload_counts(payload)
    return vertices is not None and vertices >= min_vertices


def give_levels(rows: list[dict], folder: Path, min_vertices: int = MIN_VERTICES, fragment_vertices: int = FRAGMENT_VERTICES,
                coarsest_vertices: int = COARSEST_VERTICES, mask_vertices: int = MASK_STORED_VERTICES) -> int:
    """Gives every big mesh row levels of detail: its fragments go to ``folder/meshes.bin``, its manifest to ``multires`` and
    its coarsest level to ``surface``. Returns how many rows got them; with none, no ``meshes.bin`` is left behind.

    ``min_vertices`` of 0 turns it off. Rows are changed in place.
    """
    target, done = folder / FILENAME, 0
    if min_vertices > 0:
        with MeshFile(target) as meshes:
            wanted = [row for row in rows if _is_big(row, min_vertices)]
            # Several meshes at a time: each one's decimation runs in a worker process, so the threads only wait on them.
            # A window of them, not all, so decoded meshes are not all alive together.
            width = max(1, workers.pool_size())
            with ThreadPoolExecutor(width) as threads:
                for start in range(0, len(wanted), 2 * width):
                    window = wanted[start:start + 2 * width]
                    built = threads.map(lambda row: build_levels(*decode_quantised(row["surface"]), fragment_vertices, coarsest_vertices,
                                                                 row.pop(COARSER, None),
                                                                 mask_vertices if row.get("entity_kind") == "mask" else 0), window)
                    for row, (levels, (coarse_xyz, coarse_faces)) in zip(window, built):    # in row order, so offsets are stable
                        if len(levels) < 2:    # decimation gave nothing coarser: there is nothing to choose between
                            continue
                        row["multires"] = meshes.add(levels)
                        row["surface"] = coarsest_payload(coarse_xyz, coarse_faces)
                        done += 1
    for row in rows:
        row.pop(COARSER, None)     # the meshes of rows that were not big enough, or when levels are off, are not kept
    if not done:
        target.unlink(missing_ok=True)
    return done
