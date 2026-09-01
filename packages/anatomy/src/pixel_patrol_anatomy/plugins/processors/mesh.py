"""Mesh processor: geometry as a side file, never as table columns.

Meshes would multiply the size of the report that every stats query loads, so this
processor contributes exactly one column - where it put the geometry. The payloads
themselves go to one ``geometry.parquet`` per object, which the 3D widgets and
``geometry_to_blender.py`` read.

That one column is what makes the geometry reachable from the viewer: the 3D widgets read
it off the object row, then query the sidecar for the handful of instances they are about to
draw. Without it a widget would have to guess where the meshes went.

It only runs when a destination is configured (``PP_ANATOMY_MESH_DIR``, set by
``pixel-patrol-anatomy process --with-mesh``), so an ordinary run pays nothing for it:
meshing every instance, and skeletonising it again for the overlay, is the most expensive
thing here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec

import numpy as np

from pixel_patrol_anatomy.config import AnatomyConfig
from pixel_patrol_anatomy.mesh import (
    GEOMETRY_FILENAME,
    MeshOptions,
    mesh_rows_for_object,
    write_geometry,
)
from pixel_patrol_anatomy.plugins.loaders.object_loader import OBJECT_KIND
from pixel_patrol_anatomy.spatial import voxel_size
from pixel_patrol_anatomy.plugins.processors.instances import channel_view
from pixel_patrol_anatomy.skeletons import CACHE

logger = logging.getLogger(__name__)


SETTINGS_FILENAME = "settings.json"


def _usable_geometry(path: Path, fingerprint: str) -> Optional[int]:
    """How many rows an existing geometry file has, or None if it should be written again.

    Checked rather than assumed, in two ways. A run killed mid-write - exactly the situation
    --reuse-geometry exists for - can leave a file that opens and holds nothing. And geometry
    written under other settings is worse than none: change the voxel size or --no-clip and
    the old meshes are of something else, with nothing in the output to say so. Geometry from
    before this check has no settings recorded, so it is written again rather than trusted.
    """
    if not path.is_file():
        return None
    recorded = _recorded_settings(path.parent)
    if recorded != fingerprint:
        logger.info("anatomy: geometry at %s was written under other settings; meshing again",
                    path.parent)
        return None
    try:
        import pyarrow.parquet as pq

        rows = int(pq.read_metadata(path).num_rows)
    except Exception as exc:  # noqa: BLE001 - unreadable is a reason to rewrite, not to stop
        logger.warning("anatomy: %s is not readable geometry (%s); writing it again",
                       path, type(exc).__name__)
        return None
    if rows == 0:
        logger.warning("anatomy: %s holds no rows; writing it again", path)
        return None
    return rows


def _recorded_settings(folder: Path) -> Optional[str]:
    """The fingerprint the geometry in this folder was written under, if it recorded one."""
    import json

    try:
        return str(json.loads((folder / SETTINGS_FILENAME).read_text())["fingerprint"])
    except Exception:  # noqa: BLE001 - absent or unreadable both mean "do not trust it"
        return None


def _record_settings(folder: Path, fingerprint: str) -> None:
    import json

    try:
        (folder / SETTINGS_FILENAME).write_text(
            json.dumps({"fingerprint": fingerprint}, indent=2) + "\n")
    except OSError as exc:
        logger.warning("anatomy: could not record the settings beside %s (%s)", folder, exc)


class MeshProcessor:
    """Write per-instance meshes and skeletons for one object to its own CSV."""

    NAME = "anatomy-mesh"
    DESCRIPTION = (
        "Writes one geometry file per object - per-instance marching-cubes meshes and curve "
        "skeletons for the 3D widgets and the Blender export. Adds one "
        "column to the table, the path it wrote to: the payloads belong beside the report, "
        "not inside it. Enabled by PP_ANATOMY_MESH_DIR (anatomy process --with-mesh)."
    )

    CHUNK_KIND = ChunkKind.MEMORY
    # Y and X, not Z: a 2D object is a CYX record.
    INPUT = RecordSpec(axes={"C", "Y", "X"}, kinds={OBJECT_KIND})
    OUTPUT = "features"

    # One column, and only a path: the geometry itself never enters the parquet.
    OUTPUT_SCHEMA: Dict[str, Any] = {"mesh_geometry_file": str}
    OUTPUT_SCHEMA_DESCRIPTIONS: Dict[str, str] = {
        "mesh_geometry_file": (
            "Where this object's meshes and skeletons were written. The 3D widgets query this "
            "file for the instances they draw; null when the object produced no geometry."
        ),
    }

    def __init__(self) -> None:
        self._config = AnatomyConfig.from_env()

    def run_chunk(self, record: Record) -> Dict[str, Any]:
        cfg = self._config
        if not cfg.mesh_dir:
            return {"mesh_geometry_file": None}

        meta = record.meta
        arr = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        c_axis = record.dim_order.index("C")
        names = list(meta.get("channel_names") or [])
        kinds = list(meta.get("entity_kinds") or [])
        expected_zyx = [int(v) for v in (meta.get("object_shape") or [])]
        spatial = [s for i, s in enumerate(arr.shape) if i != c_axis]
        if arr.shape[c_axis] != len(names) or (expected_zyx and spatial != expected_zyx):
            raise ValueError(
                f"object arrived as a {arr.shape} fragment of {len(names)}×{tuple(expected_zyx)}: "
                "an object is measured whole, so a fragment means the caller split it"
            )

        object_id = str(meta.get("object_id") or "object")
        destination = Path(cfg.mesh_dir) / object_id / GEOMETRY_FILENAME
        fingerprint = cfg.fingerprint()
        if cfg.reuse_geometry:
            rows_already = _usable_geometry(destination, fingerprint)
            if rows_already is not None:
                logger.info("anatomy: %s: reusing %d geometry rows already at %s",
                            object_id, rows_already, destination)
                return {"mesh_geometry_file": str(destination.resolve())}
        # What anatomy-instances already measured, so the geometry carries the same metrics
        # without measuring them twice. Empty when that processor is off.
        metrics = CACHE.get_or_compute(object_id, ("instance_metrics",), arr, dict)
        rows = mesh_rows_for_object(
            {name: channel_view(arr, c_axis, i) for i, name in enumerate(names)},
            dict(zip(names, kinds)),
            voxel_size(meta, record.dim_order),
            object_id=object_id,
            object_mask_name=meta.get("object_mask_name"),
            options=MeshOptions(
                smooth_sigma=cfg.mesh_smooth_sigma,
                step_size=cfg.mesh_step_size,
                target_reduction=cfg.mesh_target_reduction,
                level=cfg.mesh_level,
                skeleton_entities=cfg.skeleton_entities,
                max_skeleton_voxels=cfg.max_skeleton_voxels,
                num_threads=cfg.num_threads,
                contact_max_um=cfg.contact_max_um,
                mesh_workers=cfg.mesh_workers,
            ),
            metrics=metrics,
        )
        path = write_geometry(Path(cfg.mesh_dir) / object_id, rows)
        _record_settings(path.parent, fingerprint)
        # A plane is outlined, not meshed, so count and name whichever it produced.
        drawable = "outline" if len(record.dim_order) - 1 == 2 else "mesh"
        with_geometry = sum(1 for row in rows if row.get(drawable))
        logger.info(
            "anatomy: %s: %d/%d rows %s → %s (%.1f MB)",
            object_id, with_geometry, len(rows),
            "outlined" if drawable == "outline" else "meshed",
            path, path.stat().st_size / 1024**2,
        )
        return {"mesh_geometry_file": str(path.resolve())}

    def get_aggregation(self, name: str) -> Optional[Any]:
        if name != "mesh_geometry_file":
            return None

        def agg(rows: List[Dict[str, Any]], _g_dims: Dict[str, Any]) -> Optional[str]:
            # One chunk per object (run_chunk refuses fragments), so one path to report.
            return rows[0].get(name) if len(rows) == 1 else None

        return agg
