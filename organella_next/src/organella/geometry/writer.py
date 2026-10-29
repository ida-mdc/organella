"""One geometry file per object, beside the report: the 3D views read it for the handful of instances they draw.

Never table columns in the report: meshes would multiply the size of what every stats query loads. Parquet keeps the
payloads queryable where a blob file would not, so opening one structure costs its own few MB.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from organella.app.settings import Settings
from organella.core import Kind, Sample
from organella.geometry.payloads import payload_counts
from organella.geometry.rows import CARRIED, contact_rows, instance_rows, mask_rows
from organella.measure.windows import compute_in_batches

log = logging.getLogger(__name__)

FILENAME = "geometry.parquet"
SETTINGS_FILENAME = "settings.json"

# Payload headers as columns of their own, so how big a surface is can be read without decoding it.
COLUMNS = [
    "object_id", "group_id", "entity_name", "entity_kind", "row_type", "label_id", "spatial_dims",
    "volume_um3", "surface_area_um2", "sphericity", "area_um2", "perimeter_um", "circularity", *CARRIED,
    "entity_a", "label_a", "entity_b", "label_b", "gap_um", "surface_kind",
    "surface_vertices", "surface_elements", "skeleton_vertices", "skeleton_edges", "outline_vertices", "outline_edges",
    "surface", "skeleton", "outline",
]
_TEXT = {"object_id", "group_id", "entity_name", "entity_kind", "row_type", "entity_a", "entity_b", "surface_kind"}
_INTS = {"label_id", "label_a", "label_b", "spatial_dims", "surface_vertices", "surface_elements", "skeleton_vertices",
         "skeleton_edges", "outline_vertices", "outline_edges"}
_BLOBS = {"surface", "skeleton", "outline"}


def _counts(row: dict, field: str, kind_aware: bool = False) -> tuple[int | None, int | None]:
    payload = row.get(field) or b""
    if kind_aware and row.get("surface_kind") == "ellipsoid":
        return None, None   # an ellipsoid's counts would describe how the viewer tessellated it, which is not data
    return payload_counts(payload)


def write_parquet(path: Path, rows: list[dict]) -> Path:
    """An explicit schema, so every object writes the same columns whether or not it has anything in them: an object
    whose instances all failed to mesh would otherwise leave polars a column of nothing to guess a type from."""
    types = {c: pl.Utf8 if c in _TEXT else pl.Int64 if c in _INTS else pl.Binary if c in _BLOBS else pl.Float64 for c in COLUMNS}
    surface = [_counts(r, "surface", kind_aware=True) for r in rows]
    skeleton = [_counts(r, "skeleton") for r in rows]
    outline = [_counts(r, "outline") for r in rows]
    counted = {"surface_vertices": [c[0] for c in surface], "surface_elements": [c[1] for c in surface],
               "skeleton_vertices": [c[0] for c in skeleton], "skeleton_edges": [c[1] for c in skeleton],
               "outline_vertices": [c[0] for c in outline], "outline_edges": [c[1] for c in outline]}
    columns = {}
    for name in COLUMNS:
        if name in counted:
            columns[name] = counted[name]
        elif name in _BLOBS:
            columns[name] = [r.get(name) or None for r in rows]   # NULL, never empty, so "has geometry" is IS NOT NULL
        elif name in _INTS:
            columns[name] = [None if r.get(name) is None or r.get(name) != r.get(name) else int(r[name]) for r in rows]
        else:
            columns[name] = [r.get(name) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(columns, schema=types).write_parquet(path, compression="zstd")
    return path


@dataclass(frozen=True)
class GeometryWriter:
    """Where geometry goes, and whether an earlier run's usable files are kept."""

    folder: Path
    reuse: bool = False

    def write(self, sample: Sample, shared: dict, settings: Settings) -> Path:
        destination = self.folder / sample.id / FILENAME
        fingerprint = _fingerprint(sample, settings)
        if self.reuse and _usable(destination, fingerprint):
            log.info("%s: reusing the geometry already at %s", sample.id, destination)
            return destination.resolve()
        rows, jobs = self._rows(sample, shared["findings"], settings)
        done = compute_in_batches([job for _, job in jobs], settings.surface_batch)
        field = "outline" if len(sample.voxel_size.um) == 2 else "surface"
        for (row, _), payload in zip(jobs, done):
            row[field] = payload
        for row in rows:
            row["object_id"], row["group_id"] = sample.id, sample.group or ""
        path = write_parquet(destination, rows)
        (path.parent / SETTINGS_FILENAME).write_text(json.dumps({"fingerprint": fingerprint}, indent=2) + "\n")
        drawn = sum(1 for r in rows if r.get(field))
        log.info("%s: %d/%d rows drawable -> %s (%.1f MB)", sample.id, drawn, len(rows), path, path.stat().st_size / 1024 ** 2)
        return path.resolve()

    @staticmethod
    def _rows(sample: Sample, findings, settings: Settings):
        rows, jobs = [], []
        entities = {e["entity_name"]: e for e in findings.entities.to_dicts()}
        for structure in sample.structures.values():
            stats = findings.stats[structure.name]
            if structure.kind is Kind.LABEL and structure.name in findings.instances:
                made = instance_rows(structure, sample, stats, findings.instances[structure.name],
                                     findings.skeletons.get(structure.name), settings)
            elif structure.kind is Kind.MASK:
                made = mask_rows(structure, sample, stats, entities[structure.name], settings)
            else:
                continue
            rows += made[0]
            jobs += made[1]
        return rows + contact_rows(findings.contacts), jobs


def _fingerprint(sample: Sample, settings: Settings) -> str:
    """A digest of what the geometry depends on: anything not identical means meshing again."""
    keys = ("mesh_smooth_sigma", "mesh_step_size", "mesh_target_reduction", "mesh_level", "mesh_max_vertices", "geometry_as",
            "skeletons", "max_skeleton_voxels", "skeleton_scale", "skeleton_const_voxels", "skeleton_prune",
            "contacts", "contact_max_um", "clip_to_object_mask", "coarse_voxels")
    plain = {k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in ((k, getattr(settings, k)) for k in keys)}
    blob = json.dumps({**plain, "voxel": sample.voxel_size.um, "mask": sample.object_mask, "shape": sample.shape},
                      sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _usable(path: Path, fingerprint: str) -> bool:
    """An existing file is kept only if it was written under the same settings and holds rows: a run killed mid-write can
    leave a file that opens and holds nothing, and geometry from other settings is worse than none."""
    try:
        recorded = json.loads((path.parent / SETTINGS_FILENAME).read_text())["fingerprint"]
        return recorded == fingerprint and pl.scan_parquet(path).select(pl.len()).collect().item() > 0
    except Exception:
        return False
