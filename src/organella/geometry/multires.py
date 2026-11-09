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
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from organella.geometry.payloads import decode_quantised, payload_counts, quantised_payload
from organella.geometry.surfaces import DECIMATION_AGGRESSION, _decimated

FILENAME = "meshes.bin"
MANIFEST_VERSION = 1

# A mesh with fewer vertices than this is not worth fragmenting: one payload in the geometry file is as cheap.
MIN_VERTICES = 6_000
# What a fragment aims at, and where the levels stop. Fragments with 8-16k vertices are a few tens of kB deflated.
FRAGMENT_VERTICES = 12_000
COARSEST_VERTICES = 2_000
# Each level keeps about this share of the vertices of the one before.
LEVEL_KEEP = 0.25
MAX_LEVELS = 8
# How much of the way to the next level's edge length counts as "the error" of a level: the 95th percentile of its edge
# lengths, because a few long slivers say nothing about how coarse the surface looks.
ERROR_PERCENTILE = 95


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
                 coarsest_vertices: int = COARSEST_VERTICES) -> tuple[list[Level], tuple[np.ndarray, np.ndarray]]:
    """The levels, finest first, and the mesh of the last, which the geometry file keeps as the instance's surface.

    The grids are nested: one origin, and a cell twice the side of the level below it, so a coarse fragment is the union
    of at most eight finer ones and the viewer can swap one for the others anywhere on the surface without a gap or an
    overlap. A level has a quarter of the vertices of the one before and a cell of four times the area, so fragments stay
    about the same size all the way up.
    """
    xyz, faces = np.asarray(xyz, dtype=np.float32), np.asarray(faces, dtype=np.int64)
    origin, side = _grid(xyz, faces, fragment_vertices)
    levels = [_fragments(xyz, faces, origin, side)]
    while len(xyz) > coarsest_vertices and len(levels) < MAX_LEVELS and len(faces) > 100:
        smaller_xyz, smaller_faces = _decimated(xyz, faces, 1.0 - LEVEL_KEEP, agg=DECIMATION_AGGRESSION)
        if len(smaller_xyz) >= len(xyz) * 0.9 or len(smaller_faces) == 0:
            # Collapsing edges stalls on a surface of many thin or non-manifold pieces (an ER network): merging the vertices
            # that share a grid cell always gets smaller, if blockier.
            smaller_xyz, smaller_faces = _clustered(xyz, faces, int(len(xyz) * LEVEL_KEEP))
        if len(smaller_xyz) >= len(xyz) * 0.9 or len(smaller_faces) == 0:   # it stopped giving: nothing coarser to offer
            break
        xyz, faces = smaller_xyz, smaller_faces.astype(np.int64)
        levels.append(_fragments(xyz, faces, origin, side * 2 ** len(levels)))
    return levels, (xyz, faces)


def _clustered(xyz: np.ndarray, faces: np.ndarray, vertices: int) -> tuple[np.ndarray, np.ndarray]:
    """The mesh with every vertex in one cell of a regular grid merged into their mean, about ``vertices`` of them.

    A cell side from the surface area (a surface crosses about 1.5 A / side^2 cells), then triangles that collapsed to a
    line or a point, and repeats of one triangle, are dropped.
    """
    corners = xyz[faces]
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    side = max(float(np.sqrt(1.5 * area / max(vertices, 1))), 1e-6)
    cell = np.floor((xyz - xyz.min(axis=0)) / side).astype(np.int64)
    _, owner = np.unique(cell, axis=0, return_inverse=True)
    owner = owner.ravel()
    count = np.bincount(owner)
    merged = np.stack([np.bincount(owner, weights=xyz[:, a]) / count for a in range(3)], axis=1).astype(np.float32)
    remapped = owner[faces]
    keep = (remapped[:, 0] != remapped[:, 1]) & (remapped[:, 1] != remapped[:, 2]) & (remapped[:, 0] != remapped[:, 2])
    remapped = np.unique(np.sort(remapped[keep], axis=1), axis=0) if keep.any() else remapped[keep]
    return merged, remapped.astype(np.int64)


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


def give_levels(rows: list[dict], folder: Path, min_vertices: int = MIN_VERTICES, fragment_vertices: int = FRAGMENT_VERTICES,
                coarsest_vertices: int = COARSEST_VERTICES) -> int:
    """Gives every big mesh row levels of detail: its fragments go to ``folder/meshes.bin``, its manifest to ``multires`` and
    its coarsest level to ``surface``. Returns how many rows got them; with none, no ``meshes.bin`` is left behind.

    ``min_vertices`` of 0 turns it off. Rows are changed in place.
    """
    target, done = folder / FILENAME, 0
    if min_vertices > 0:
        with MeshFile(target) as meshes:
            for row in rows:
                payload = row.get("surface")
                if row.get("surface_kind") != "mesh" or not payload:
                    continue
                vertices, _ = payload_counts(payload)
                if vertices is None or vertices < min_vertices:
                    continue
                xyz, faces = decode_quantised(payload)
                levels, (coarse_xyz, coarse_faces) = build_levels(xyz, faces, fragment_vertices, coarsest_vertices)
                if len(levels) < 2:    # decimation gave nothing coarser: there is nothing to choose between
                    continue
                row["multires"] = meshes.add(levels)
                row["surface"] = coarsest_payload(coarse_xyz, coarse_faces)
                done += 1
    if not done:
        target.unlink(missing_ok=True)
    return done
