"""An object measured a window at a time, for one too big to hold.

``measure_object`` reads an object whole, which sets the memory a run asks for: a cell 8 times as fine in
each direction is 512 times the voxels. Here the object is never read whole. What a measurement needs of it
decides how it is read:

- **An instance** (its shape, where it lies, which others it touches, how near it gets to another structure)
  needs a box around itself and a margin. These are measured at full resolution from windows, and are the
  numbers the whole-object path gives.
- **The object as a whole** (the chance distribution of a distance, how deep a structure's centre sits, the
  shape of a whole-structure mask and its surface) needs all of it. These are measured by the same code the
  whole-object path uses, on a *coarse view*: every nth voxel along each axis, with as few n as fit the
  budget ``--coarse-voxels``. ``coarse_factor`` on the object row says which n.

A distance from an instance to another structure is exact up to ``--window-reach-um`` and is read off the
coarse view beyond that, as far as the window does not see it.

With a coarse factor of 1 the view is the whole object and the rows are the whole-object path's, which is how
the tests hold the two together.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import polars as pl

from organella.analysis.meshes import MeshOptions
from organella.analysis.parallel import worker_share
from organella.config import RunConfig, wants_skeletons
from organella.measure import ContactMeasurer, GeometryWriter, InstanceMeasurer, MorphologyMeasurer
from organella.measure.contacts import contact_measurement
from organella.measure.instance_windows import (
    InstanceBox, Measured, instance_boxes, instance_tables, measure_instances, mesh_rows_for_windows,
)
from organella.measure.instances import metrics_by_instance, nulled, null_if_not_finite
from organella.measure.source import ObjectSource
from organella.model import BASELINE_ROW, DISTANCE_ROW, INSTANCE_ROW, ObjectMeasurement
from organella.pipeline import table
from organella.pipeline.objects import (
    add_to_entity_rows, entity_rows, object_row, rolled_up, what_the_object_is,
)

logger = logging.getLogger(__name__)

Boxes = Dict[str, Dict[int, InstanceBox]]


def measure_windowed(folder, group: str, excluded: Sequence[str], cfg: RunConfig):
    """An object measured a window at a time, as (its id, its row, its entity rows, its deep rows)."""
    source = ObjectSource(folder, cfg)
    _refuse_what_cannot_be_windowed(source, cfg)
    skip = set(excluded or ())

    boxes: Boxes = {}
    for name in source.names:
        if source.kinds[name] == "label":
            boxes[name] = instance_boxes(source, name)
            logger.info("organella: %s: %d instances of %s", source.object_id, len(boxes[name]), name)
    _refuse_binary_labels(source, boxes)
    factor = source.coarse_factor(cfg.coarse_voxels)
    logger.info("organella: %s: %d instances measured a window at a time; the whole-object measurements run on "
                "%s", source.object_id, sum(map(len, boxes.values())),
                f"every {factor}th voxel" if factor > 1 else "the object at full resolution")
    coarse = source.coarse_stack(factor)
    logger.info("organella: %s: coarse view of %s voxels, %.1f GB", source.object_id,
                "×".join(map(str, coarse.data.shape[1:])), coarse.data.nbytes / 1024**3)

    about = what_the_object_is(folder, source.meta, coarse.dim_order, (len(source.names), *source.shape))
    morphology = MorphologyMeasurer()
    rows = entity_rows(coarse, morphology, about, group)
    _exact_sizes(rows, boxes, source)

    columns: Dict[str, Any] = {"coarse_factor": factor}
    per_entity: Dict[str, Dict[str, Any]] = {}
    frames: List[pl.DataFrame] = []

    def keep(found: ObjectMeasurement, schemas: Mapping[str, Mapping[str, Any]]) -> None:
        columns.update(found.columns)
        for name, values in found.entity_columns.items():
            per_entity.setdefault(name, {}).update(values)
        frames.extend(table.deep_row_frames(found, schemas, source.object_id))

    # One pass over the object finds all that windows give: the shape of every instance, how near it gets to each
    # other structure, and which instances of a structure touch. What is not wanted is not measured.
    with_instances = "organella-instances" not in skip
    with_contacts = "organella-contacts" not in skip
    with_geometry = "organella-mesh" not in skip and bool(cfg.mesh_dir)
    measured = measure_instances(
        source, boxes, cfg.window_reach_um if with_instances else None,
        cfg.contact_max_um if (with_contacts or with_geometry) else None, worker_share(cfg.mesh_workers))

    metrics: Mapping[tuple, Mapping[str, Any]] = {}
    if with_instances:
        instances, metrics = _instances(source, coarse, boxes, measured, cfg)
        keep(instances, InstanceMeasurer.ROW_SCHEMAS)
    if with_contacts:
        keep(contact_measurement(measured.contacts), ContactMeasurer.ROW_SCHEMAS)
    if "organella-mesh" not in skip:
        keep(GeometryWriter(cfg).write(
            source.object_id, 3,
            lambda: mesh_rows_for_windows(source, coarse, boxes, measured.stats, MeshOptions.from_config(cfg),
                                          metrics, measured.contacts)), GeometryWriter.ROW_SCHEMAS)

    add_to_entity_rows(rows, per_entity)
    return source.object_id, object_row(about, group, rolled_up(morphology, rows), columns), rows, frames


def _refuse_what_cannot_be_windowed(source: ObjectSource, cfg: RunConfig) -> None:
    """Say, before anything is read, what this run asks for that a window cannot give."""
    if source.center is None:
        raise ValueError(f"{source.object_id}: measuring a window at a time needs the mask that bounds the "
                         "object (--object-mask): polarity is measured from its centre")
    skeletonised = [n for n in source.names
                    if source.kinds[n] == "label" and wants_skeletons(n, cfg.geometry_as, cfg.skeletons)]
    if skeletonised:
        raise ValueError(f"{source.object_id}: curve skeletons ({', '.join(skeletonised)}) are not measured a "
                         "window at a time; leave out --skeletons and tube or skeleton geometry")
    if cfg.distance_histograms:
        raise ValueError(f"{source.object_id}: --distance-histograms are not measured a window at a time")


def _refuse_binary_labels(source: ObjectSource, boxes: Boxes) -> None:
    """A binary array declared as labels is split into its connected pieces when an object is read whole, and
    that split is of the whole array. One id is what that array looks like."""
    for name in source.declared_labels():
        if len(boxes.get(name, ())) == 1:
            raise ValueError(f"{source.object_id}: '{name}' is declared as labels but holds a single id, which is "
                             "what a binary array looks like. Windows cannot split it into its connected pieces: "
                             "declare it a mask, or give it an id per instance")


def _exact_sizes(rows: Sequence[Dict[str, Any]], boxes: Boxes, source: ObjectSource) -> None:
    """Put the full-resolution size of every structure on its entity row.

    The rows were measured on the coarse view. A label structure's count and total volume come from its boxes, which
    have every instance; a mask's volume from the voxels counted as the view was built. The shape of a mask
    (surface, sphericity, axes) stays the view's.
    """
    voxel = float(np.prod(source.sample_size))
    for row in rows:
        name = row["entity_name"]
        if name in boxes:
            row["instance_count"] = len(boxes[name])
            row["total_volume_um3"] = null_if_not_finite(float(sum(i.voxels for i in boxes[name].values()) * voxel))
        elif source.foreground_voxels[name]:
            row["volume_um3"] = row["total_volume_um3"] = float(source.foreground_voxels[name] * voxel)


def _instances(source: ObjectSource, coarse, boxes: Boxes, measured: Measured,
               cfg: RunConfig) -> Tuple[ObjectMeasurement, Mapping]:
    """The instance and distance rows from windows, and the chance distributions and depths from the coarse view."""
    inst, dist = instance_tables(source, boxes, measured, cfg.polarity_spread)
    from_coarse = InstanceMeasurer(cfg).measure(coarse)
    dist["distance_um"] = _read_past_the_reach(dist, from_coarse.tables[DISTANCE_ROW], cfg.window_reach_um)
    enclosed = float(source.enclosed_voxels * np.prod(source.sample_size))
    rows = ObjectMeasurement(
        columns={"object_volume_um3": enclosed},
        entity_columns=from_coarse.entity_columns,
        tables={INSTANCE_ROW: nulled(inst), DISTANCE_ROW: nulled(dist),
                BASELINE_ROW: from_coarse.tables[BASELINE_ROW]})
    return rows, metrics_by_instance(inst)


def _read_past_the_reach(exact: Mapping[str, List[Any]], coarse: Mapping[str, List[Any]], reach_um: float) -> List[float]:
    """The distances of ``exact``, where one that its window did not reach is the coarse view's reading.

    Not under the reach, which is all that is known of it: the view's reading of a distance that its window could
    not see may fall a voxel short of it.
    """
    keys = ("distance_entity", "distance_label", "distance_target")
    seen = {(e, l, t): d for e, l, t, d in zip(*(coarse[c] for c in (*keys, "distance_um")))}
    out = []
    for entity, label, target, distance in zip(*(exact[c] for c in (*keys, "distance_um"))):
        if math.isinf(distance):
            reading = seen.get((entity, label, target))
            distance = float("nan") if reading is None else max(float(reading), reach_um)
        out.append(distance)
    return out
