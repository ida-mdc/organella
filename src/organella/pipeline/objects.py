"""Measuring one object folder into the rows a report holds.

One object is read once and measured at three depths, which is what the four steps below
produce:

    the object row     obs_level 0: what the object is, where it came from, and the
                       whole-object values the measurers hand back
    the entity rows    obs_level 1: one per structure, from the per-entity measurer
    the deep rows      obs_level 2: one per instance, instance-target distance and contact

Nothing here raises. One object failing is ordinary - a missing mask, a volume that will
not read - and must cost that object and nothing else, so it comes back as
:attr:`ObjectResult.error` for the batch to report by name.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import polars as pl

from organella.analysis.cache import CACHE
from organella.config import RunConfig
from organella.measure import (
    ContactMeasurer,
    GeometryWriter,
    InstanceMeasurer,
    MorphologyMeasurer,
    load_object,
)
from organella.model import ENTITY_ROW, OBJECT_ROW, ROW_TYPE, ObjectStack
from organella.pipeline import table

logger = logging.getLogger(__name__)

# On the object row and no other: an entity row carrying the object's extent would read as
# if that were the entity's.
_OBJECT_ONLY = ("object_shape", "object_center_z_um", "object_center_y_um",
                "object_center_x_um")


@dataclass
class ObjectResult:
    """One object's rows, or the error that stopped it.

    The object and entity rows are dicts - there are a handful of them, and they are built
    from what the object knows a key at a time. The instance, distance and contact rows are
    frames, because there are tens of thousands per object.
    """

    object_id: str
    object_row: Optional[Dict[str, Any]] = None
    entity_rows: List[Dict[str, Any]] = field(default_factory=list)
    deep_rows: List[pl.DataFrame] = field(default_factory=list)
    error: Optional[str] = None
    seconds: float = 0.0

    def frame(self) -> pl.DataFrame:
        """Every row this object produced, in one frame."""
        shallow = table.rows_as_frame([self.object_row, *self.entity_rows])
        return table.stacked([f for f in (shallow, *self.deep_rows) if f.height])


def measure_object(folder: Path, group: str, excluded: Sequence[str] = (),
                   config: Optional[RunConfig] = None) -> ObjectResult:
    """Read one object folder and measure it, whole. Never raises.

    ``config`` is what the run was asked for. It arrives as an argument because this
    function is what a worker process is handed: the settings travel with the work rather
    than being picked up out of the worker's surroundings.
    """
    started = time.perf_counter()
    cfg = config if config is not None else RunConfig.from_env()
    try:
        stack = load_object(folder, cfg)
        about_the_object = _what_the_object_is(folder, stack)

        morphology = MorphologyMeasurer()
        entity_rows = _entity_rows(stack, morphology, about_the_object, group)
        whole_object_columns, deep_rows, per_entity = _measure_the_whole_object(
            stack, excluded, cfg)
        _add_to_entity_rows(entity_rows, per_entity)
        object_row = _object_row(about_the_object, group,
                                 _rolled_up(morphology, entity_rows), whole_object_columns)

        return ObjectResult(stack.object_id, object_row, entity_rows, deep_rows,
                            seconds=time.perf_counter() - started)
    except Exception as exc:  # noqa: BLE001 - one bad object must not stop the batch
        logger.error("organella: %s failed: %s: %s", folder.name, type(exc).__name__, exc)
        return ObjectResult(folder.name, error=f"{type(exc).__name__}: {exc}",
                            seconds=time.perf_counter() - started)
    finally:
        # The next object in this worker is a different object; nothing measured for this
        # one may be answered from the cache again.
        CACHE.clear()


# ── the rows ──────────────────────────────────────────────────────────────────

def _entity_rows(stack: ObjectStack, morphology: MorphologyMeasurer,
                 about_the_object: Mapping[str, Any], group: str) -> List[Dict[str, Any]]:
    """One row per structure of the object, named by the structure it is about.

    Not keyed by the channel it was stacked as: a ``dim_c`` column would read an object's
    structures as if they were imaging channels, a five-structure cell as "5 channels".
    ``entity_name`` says which structure a row is about.
    """
    shared = {k: v for k, v in about_the_object.items() if k not in _OBJECT_ONLY}
    return [
        {**shared, **morphology.measure(entity),
         "obs_level": 1, ROW_TYPE: ENTITY_ROW, "imported_path_short": group}
        for entity in stack.entities()
    ]


def _measure_the_whole_object(
    stack: ObjectStack, excluded: Sequence[str], config: RunConfig,
) -> Tuple[Dict[str, Any], List[pl.DataFrame], Dict[str, Dict[str, Any]]]:
    """What the measurers that see every entity at once produce.

    Three parts of it: the values that belong on the object row, the frames of rows that sit
    below the object, and the few values that belong on a *structure's* row but need the
    whole object to measure. A measurer named in ``excluded`` is not run at all - that is
    what ``--no-instances`` / ``--no-contacts`` and a run without ``--with-mesh`` come down
    to.
    """
    columns: Dict[str, Any] = {}
    frames: List[pl.DataFrame] = []
    per_entity: Dict[str, Dict[str, Any]] = {}
    for measurer in _measurers_of_whole_objects(excluded, config):
        measured = measurer.measure(stack)
        columns.update(measured.columns)
        for name, values in measured.entity_columns.items():
            per_entity.setdefault(name, {}).update(values)
        frames.extend(table.deep_row_frames(measured, measurer.ROW_SCHEMAS, stack.object_id))
    return columns, frames, per_entity


def _add_to_entity_rows(rows: List[Dict[str, Any]],
                        per_entity: Mapping[str, Mapping[str, Any]]) -> None:
    """Put what was measured against the whole object onto the structure it is about."""
    for row in rows:
        extra = per_entity.get(row.get("entity_name"))
        if extra:
            row.update(extra)


def _object_row(about_the_object: Mapping[str, Any], group: str,
                rolled_up: Mapping[str, Any],
                whole_object_columns: Mapping[str, Any]) -> Dict[str, Any]:
    """The one row that stands for the object: what it is, and what was measured of it whole."""
    return {**about_the_object, **rolled_up, **whole_object_columns,
            "obs_level": 0, ROW_TYPE: OBJECT_ROW, "imported_path_short": group}


def _rolled_up(morphology: MorphologyMeasurer,
               entity_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The per-entity values that mean something for the object as a whole.

    Counts and totals sum; a shape does not, and comes back None so the column is left
    unfilled on the object row. The measurer decides which is which (see its ``roll_up``).
    """
    rolled: Dict[str, Any] = {}
    for column in morphology.ROW_COLUMNS:
        aggregate = morphology.roll_up(column)
        if aggregate is not None:
            rolled[column] = aggregate([dict(row) for row in entity_rows], {})
    return rolled


def _measurers_of_whole_objects(excluded: Iterable[str], config: RunConfig) -> List[Any]:
    """The measurers that need every entity at once, minus any the run asked to skip."""
    skip = set(excluded or ())
    return [m for m in (InstanceMeasurer(config), ContactMeasurer(config),
                        GeometryWriter(config))
            if m.NAME not in skip]


# ── what the object is ────────────────────────────────────────────────────────

def _what_the_object_is(folder: Path, stack: ObjectStack) -> Dict[str, Any]:
    """Everything true of the object rather than of one measurement of it.

    Both halves of that: what it holds and how big it is, and where it came from. Every row
    of this object repeats these, minus the few that describe the object as a whole (see
    ``_OBJECT_ONLY``), so a row can be read on its own.
    """
    return {**_extent(stack), **_provenance(folder, stack)}


def _extent(stack: ObjectStack) -> Dict[str, Any]:
    """What the object knows about itself, plus how big the region it was read from is.

    The spatial axes only: C is the structure count, which ``n_entities`` already says and
    which as ``size_C`` reads as an image with five channels. Axis order is not a column
    either - it never varies, and ``spatial_dims`` says which axes an object has.
    """
    columns = dict(stack.meta)
    for axis, size in zip(stack.dim_order, stack.data.shape):
        if axis != "C":
            columns[f"size_{axis}"] = int(size)
    columns["num_pixels"] = int(np.prod(stack.data.shape))
    return columns


def _provenance(folder: Path, stack: ObjectStack) -> Dict[str, Any]:
    """Where the object came from and how much was read.

    An object is a folder, so the usual per-file columns (name, parent, depth, type)
    describe nothing and are left out. ``file_extension`` stays: the volumes share one, and
    it tells a TIFF batch from any other.
    """
    files = [folder / name for name in (stack.meta.get("entity_files") or [])]
    stats = [p.stat() for p in files if p.exists()]
    suffixes = {p.suffix.lower().lstrip(".") for p in files}
    return {
        "path": str(folder.resolve()),
        "file_extension": suffixes.pop() if len(suffixes) == 1 else None,
        "size_bytes": sum(s.st_size for s in stats) or None,
        "modification_date": (datetime.fromtimestamp(max(s.st_mtime for s in stats))
                              if stats else None),
    }
