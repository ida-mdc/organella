"""One row per instance, and one per instance-target distance.

Per-instance measurements need the whole object, not one entity: a distance is *to*
another entity, and polarity is relative to the object mask. So this measurer sees every
entity at once, and what it measures becomes rows of its own, two kinds of them
(``row_type='instance'`` and ``row_type='distance'``, see
:mod:`label_anatomy.model.rows`):

    SELECT object_id, instance_entity, instance_label, instance_volume_um3
    FROM pp_all WHERE row_type = 'instance'

Distances are a second, longer kind - one row per instance × target - because a distance
belongs to a pair, and target names come from the data:

    SELECT object_id, distance_entity, distance_label, distance_target, distance_um
    FROM pp_all WHERE row_type = 'distance'

Both used to be parallel list columns on the object row, unnested in SQL. The viewer
materialises the object rows in memory, so an object's whole instance table was loaded
whatever question was being asked; as rows they are read when a widget asks for them.

Instances come from label entities. A whole-structure mask has no instances; its
morphology is on its own entity row, from anatomy-morphology.

Memory is the constraint, since a real object is ~550 megavoxels across five entities. Both
passes keep at most one whole-volume float32 array alive: morphology walks instances one at
a time, distances walk *targets* one at a time, reducing each transform before freeing it.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree

from label_anatomy.config import AnatomyConfig, wants_skeletons
from label_anatomy.analysis.distances import (
    POLARITY_2D,
    POLARITY_3D,
    distance_target,
    distance_transform_um,
    object_center_um,
    polarity_from_offset,
    segmented_center_um,
)
from label_anatomy.analysis.shapes import (
    METRICS_2D,
    METRICS_3D,
    skeleton_graph_metrics,
)
from label_anatomy.model import (
    DISTANCE_ROW,
    INSTANCE_ROW,
    ObjectMeasurement,
    ObjectStack,
)
from label_anatomy.analysis.cache import (
    CACHE, label_metrics_for, regions_for, skeletons_for,
)

logger = logging.getLogger(__name__)

DISTANCE_HISTOGRAM_BINS = 20

# One row per instance. Both dimensionalities are declared, since a column the writer
# was not told about is dropped, and each object fills its own set.
_INSTANCE_COLUMNS: Dict[str, Any] = {
    "instance_entity": str,
    "instance_label": np.int64,
    # 3D
    "instance_volume_um3": np.float64,
    "instance_surface_area_um2": np.float64,
    "instance_sphericity": np.float64,
    # 2D
    "instance_area_um2": np.float64,
    "instance_perimeter_um": np.float64,
    "instance_circularity": np.float64,
    # both
    "instance_aspect_ratio_major_minor": np.float64,
    "instance_branches": np.float64,
    "instance_length_um": np.float64,
    "instance_tortuosity": np.float64,
    "instance_distance_to_closest_same_type_um": np.float64,
    "instance_polar_dist_um": np.float64,
    "instance_polar_ny": np.float64,
    "instance_polar_nx": np.float64,
    "instance_polar_spread_deg": np.float64,
    # 3D polarity
    "instance_polar_az_deg": np.float64,
    "instance_polar_el_deg": np.float64,
    "instance_polar_nz": np.float64,
    # 2D polarity
    "instance_polar_angle_deg": np.float64,
}

# One row per instance x target entity.
_DISTANCE_COLUMNS: Dict[str, Any] = {
    "distance_entity": str,
    "distance_label": np.int64,
    "distance_target": str,
    "distance_um": np.float64,
    "distance_mean_um": np.float64,
    "distance_hist_min_um": np.float64,
    "distance_hist_max_um": np.float64,
    "distance_hist_counts": str,
}

_OBJECT_COLUMNS: Dict[str, Any] = {
    "object_volume_um3": np.float64,
    "object_area_um2": np.float64,
}

_DESCRIPTIONS: Dict[str, str] = {
    "instance_entity": "Structure the instance on this row belongs to.",
    "instance_label": "Label id of this instance within its structure.",
    "instance_volume_um3": "Volume of this instance in µm³.",
    "instance_surface_area_um2": "Surface area of this instance in µm², by ITK's Crofton estimator: right on a smooth surface, and about 10% short on a flat axis-aligned face.",
    "instance_sphericity": "Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect ball and nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does on instances a handful of voxels across. Flat or faceted shapes read well below: a cube 0.92, a thin slab or a square rod about 0.6.",
    "instance_area_um2": "Area of this instance in µm².",
    "instance_perimeter_um": "Perimeter of this instance in µm, by ITK's Crofton estimator: right on a smooth outline, and short on a straight axis-aligned edge.",
    "instance_circularity": "Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline reads below.",
    "instance_aspect_ratio_major_minor": "Longest principal axis ÷ shortest. 1 is a ball; higher is more elongated, flatter, or both.",
    "instance_branches": "Branches in this instance's curve skeleton. Only measured for the structures a run named with --skeleton-entities.",
    "instance_length_um": "Total length of this instance's curve skeleton in µm - the length along it, not end to end.",
    "instance_tortuosity": "How far from straight the skeleton runs: length along it ÷ the straight line between its ends, weighted by branch length. 1 is straight.",
    "instance_distance_to_closest_same_type_um": "Centre-to-centre distance in µm to the nearest other instance of the same structure - how crowded this one's neighbourhood is.",
    "instance_polar_dist_um": "Distance in µm from the object centre to the instance centroid.",
    "instance_polar_az_deg": "Which way this instance lies from the object centre, around the Z axis, in degrees.",
    "instance_polar_el_deg": "How far above or below the object centre this instance lies, in degrees.",
    "instance_polar_angle_deg": "Which way this instance lies from the object centre, in degrees.",
    "instance_polar_nz": "Z component of the unit vector from the object centre to this instance's centroid.",
    "instance_polar_ny": "Y component of the unit vector from the object centre to the instance centroid.",
    "instance_polar_nx": "X component of the unit vector from the object centre to the instance centroid.",
    "instance_polar_spread_deg": "Angular spread in degrees of the instance's voxels as seen from the object centre: how much of a direction range it covers. Null unless polarity spread is enabled.",
    "distance_entity": "Structure of the instance this distance row is about.",
    "distance_label": "Label id of the instance being measured.",
    "distance_target": "Structure measured to; for the object mask this is the distance to the object boundary.",
    "distance_um": "Smallest distance in µm from this instance's voxels to the nearest voxel of the target structure. 0 means they overlap.",
    "distance_mean_um": "Mean distance in µm over the instance's voxels. Null unless distance histograms are enabled.",
    "distance_hist_min_um": "Lower bound of the histogram range, shared by every instance of this structure measured to this target.",
    "distance_hist_max_um": "Upper bound of the histogram range, shared by every instance of this structure measured to this target.",
    "distance_hist_counts": "Per-instance voxel counts over the histogram range, as a JSON array of fixed-width bins.",
    "object_volume_um3": "Volume in µm³ enclosed by the object mask.",
    "object_area_um2": "Area in µm² enclosed by the object mask.",
}


# The other dimensionality's shape columns, filled with NaN (→ null) so every instance row
# has the same columns and a metric this dimensionality does not define reads as not measured.
_UNMEASURED_SHAPE = {
    3: tuple(name for name in METRICS_2D if name not in METRICS_3D),
    2: tuple(name for name in METRICS_3D if name not in METRICS_2D),
}

# Every polarity column either dimensionality defines; the rest are left null.
_POLARITY_COLUMNS = tuple(dict.fromkeys(POLARITY_3D + POLARITY_2D))


def null_if_not_finite(value: Any) -> Any:
    """NaN and infinity become NULL, because that is how a table says "not measured".

    Left as NaN they are not merely untidy: DuckDB's STDDEV raises "out of range" on a
    column that holds one, so a single unmeasured instance took a whole widget down, and
    min/max/quantiles came back nan.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metrics_by_instance(inst: Dict[str, List[Any]]) -> Dict[tuple, Dict[str, Any]]:
    """The instance lists as {(entity, label): {metric: value}}, for joining elsewhere.

    Keys drop the "instance_" prefix, so the geometry file can carry the shorter column names
    the 3D widgets read (volume_um3, polar_nx, ...).
    """
    keys = [c for c in inst if c not in ("instance_entity", "instance_label")]
    return {
        (entity, int(label)): {
            col.removeprefix("instance_"): null_if_not_finite(inst[col][i])
            for col in keys if i < len(inst[col])
        }
        for i, (entity, label) in enumerate(zip(inst["instance_entity"], inst["instance_label"]))
    }


def _nulled(table: Dict[str, List[Any]]) -> Dict[str, List[Any]]:
    """One columnar table with every non-finite value turned into a NULL.

    Every column keeps its length, so the rows built from them stay aligned; the type comes
    from the measurer's declaration, so a column no instance of this object could fill is
    still the number it is rather than a column of untyped nulls.
    """
    return {name: [null_if_not_finite(v) for v in values] for name, values in table.items()}


class InstanceMeasurer:
    """Per-instance morphology, distances and polarity for a whole object."""

    NAME = "anatomy-instances"
    DESCRIPTION = (
        "Measures every labelled instance in an object: volume, surface area, sphericity, PCA aspect "
        "ratio, curve-skeleton metrics, distance to each other entity, distance to the closest "
        "instance of its own entity, and its direction from the object centre."
    )

    # What lands on the object row, and what lands on rows of its own. The pipeline reads
    # both: the first to fill the object row, the second to type the instance and distance
    # rows.
    OBJECT_COLUMNS: Dict[str, Any] = dict(_OBJECT_COLUMNS)
    ROW_SCHEMAS: Dict[str, Dict[str, Any]] = {
        INSTANCE_ROW: dict(_INSTANCE_COLUMNS),
        DISTANCE_ROW: dict(_DISTANCE_COLUMNS),
    }
    COLUMN_DESCRIPTIONS: Dict[str, str] = dict(_DESCRIPTIONS)

    def __init__(self) -> None:
        self._config = AnatomyConfig.from_env()

    def measure(self, stack: ObjectStack) -> ObjectMeasurement:
        volumes = stack.volumes()
        sample_size = stack.sample_size
        object_mask_name = stack.object_mask_name

        out: Dict[str, Any] = {}
        if object_mask_name in volumes:
            sample_extent = float(np.prod(sample_size))
            enclosed = float((volumes[object_mask_name] > 0).sum() * sample_extent)
            out["object_area_um2" if stack.spatial_dims == 2 else "object_volume_um3"] = enclosed

        # From the loader, so these rows and the entity rows share one origin. Recomputed
        # here only for a stack that arrived without one - the same two rules the loader
        # uses: the bounding mask's centroid, or the centre of everything segmented.
        center = stack.center
        if center is None:
            center = (object_center_um(volumes[object_mask_name], sample_size)
                      if object_mask_name in volumes
                      else segmented_center_um(volumes.values(), sample_size))

        label_names = stack.label_names
        inst: Dict[str, List[Any]] = {col: [] for col in _INSTANCE_COLUMNS}
        ids_by_entity: Dict[str, List[int]] = {}
        for name in label_names:
            ids_by_entity[name] = self._measure_morphology(
                name, volumes[name], sample_size, center, inst, object_id=stack.object_id,
            )

        dist = self._measure_distances(volumes, stack.kinds, label_names, ids_by_entity,
                                       sample_size, object_mask_name)

        logger.info(
            "anatomy: %s: %d instances, %d instance-target distances",
            stack.object_id, len(inst["instance_label"]), len(dist["distance_um"]),
        )
        # Published for the geometry writer: geometry.parquet carries the same per-instance
        # metrics, and recomputing them there would be waste.
        CACHE.get_or_compute(
            stack.object_id, ("instance_metrics",), stack.data,
            lambda: _metrics_by_instance(inst),
        )
        return ObjectMeasurement(
            columns=out,
            tables={INSTANCE_ROW: _nulled(inst), DISTANCE_ROW: _nulled(dist)},
        )

    # ── morphology, one instance at a time ────────────────────────────────────

    def _measure_morphology(
        self,
        entity: str,
        labels: np.ndarray,
        sample_size: Sequence[float],
        center: tuple[float, float, float] | None,
        inst: Dict[str, List[Any]],
        object_id: str = "object",
    ) -> List[int]:
        """Append one element per instance to every instance_* list; return the label ids."""
        cfg = self._config
        props = regions_for(object_id, entity, labels)
        if not props:
            return []
        ndim = labels.ndim
        # Every instance measured in one pass, then looked up per instance.
        measured = label_metrics_for(object_id, entity, labels, sample_size)
        shape_keys = METRICS_2D if ndim == 2 else METRICS_3D

        # One pass over the whole entity yields a skeleton per instance, shared with the
        # geometry writer through the cache, and skipped for entities that want none.
        skels = (
            skeletons_for(object_id, entity, labels, sample_size,
                          cfg.max_skeleton_voxels, cfg.num_threads)
            if wants_skeletons(entity, cfg.skeleton_entities) else {}
        )
        unmeasured = {"branches": float("nan"), "length_um": float("nan"), "tortuosity": float("nan")}

        ids: List[int] = []
        centroids = []
        for rp in props:
            stats = measured.get(int(rp.label), {})
            metrics = {key: stats.get(key, float("nan")) for key in shape_keys}
            if not skels or (cfg.max_skeleton_voxels is not None and rp.area > cfg.max_skeleton_voxels):
                # Not asked for, or over the size cap: NaN, not a misleading zero.
                skel = unmeasured
            else:
                skel = skeleton_graph_metrics(skels.get(int(rp.label)))

            ids.append(int(rp.label))
            inst["instance_entity"].append(entity)
            inst["instance_label"].append(int(rp.label))
            for name, value in metrics.items():
                inst[f"instance_{name}"].append(value)
            for name in _UNMEASURED_SHAPE[ndim]:
                inst[f"instance_{name}"].append(float("nan"))
            inst["instance_branches"].append(float(skel["branches"]))
            inst["instance_length_um"].append(float(skel["length_um"]))
            inst["instance_tortuosity"].append(float(skel["tortuosity"]))

            centroid_um = np.array(stats.get("centroid_um")
                                   or np.array(rp.centroid) * np.array(sample_size))
            centroids.append(centroid_um)
            polar = (
                polarity_from_offset(centroid_um - np.array(center)) if center is not None
                else {}
            )
            for name in _POLARITY_COLUMNS:
                inst[f"instance_{name}"].append(polar.get(name, float("nan")))
            inst["instance_polar_spread_deg"].append(
                _polar_spread_deg(rp.coords, sample_size, center)
                if (cfg.polarity_spread and center is not None) else float("nan")
            )

        # Nearest neighbour of the same entity, centroid to centroid, in instance order.
        # A tree, not an all-pairs matrix: one entity can hold thousands of instances, and
        # n x n float64 is 321 MB at 6340 of them for the sake of one value each. k=2
        # because the closest point to a point is itself, so the neighbour is the second.
        # Measured at 6340: 8.9 ms and 0.3 MB against 238 ms and 322 MB, same answers.
        if len(centroids) > 1:
            points = np.vstack(centroids)
            nearest = cKDTree(points).query(points, k=2)[0][:, 1].tolist()
        else:
            nearest = [float("nan")] * len(props)
        inst["instance_distance_to_closest_same_type_um"].extend(nearest)
        return ids

    # ── distances, one target at a time ──────────────────────────────────────

    def _measure_distances(
        self,
        views: Dict[str, np.ndarray],
        kinds_by_name: Dict[str, str],
        label_names: List[str],
        ids_by_entity: Dict[str, List[int]],
        sample_size: Sequence[float],
        object_mask_name: str | None = None,
    ) -> Dict[str, List[Any]]:
        """One row per (instance, target), reducing each transform over every entity.

        Targets are the outer loop so only one distance transform exists at a time.

        Each entity's foreground is indexed once (voxel positions sorted by label id),
        after which measuring it against a transform is a gather plus a reduceat over the
        foreground alone. scipy's ndimage.minimum does the obvious thing instead - one
        labelled pass over the whole volume - and is pathologically slow at it: 54 s per
        call on a 197-megavoxel object with 10k instances, against 0.01 s here, because its
        cost follows the volume and the label count rather than the foreground.
        """
        cfg = self._config
        # Only the columns that get filled: the histogram ones are dropped from the report
        # rather than written as a column of nulls per distance row.
        columns = list(_DISTANCE_COLUMNS) if cfg.distance_histograms else [
            "distance_entity", "distance_label", "distance_target", "distance_um",
        ]
        dist: Dict[str, List[Any]] = {col: [] for col in columns}
        indexes = {
            name: _foreground_index(views[name])
            for name in label_names if ids_by_entity.get(name)
        }
        for target, target_view in views.items():
            measured = [name for name in indexes if name != target]
            if not measured:
                continue
            transform = distance_transform_um(
                distance_target(target_view, target, kinds_by_name[target],
                                object_mask_name),
                sample_size, cfg.edt_threads,
            )
            flat = transform.reshape(-1)
            for name in measured:
                index = indexes[name]
                values = flat[index.positions]
                mins = np.minimum.reduceat(values, index.starts)
                stats = _distance_stats(values, index) if cfg.distance_histograms else None
                for position, label_id in enumerate(index.ids):
                    dist["distance_entity"].append(name)
                    dist["distance_label"].append(int(label_id))
                    dist["distance_target"].append(target)
                    dist["distance_um"].append(float(mins[position]))
                    if stats is not None:
                        dist["distance_mean_um"].append(stats["mean"][position])
                        dist["distance_hist_min_um"].append(stats["lo"])
                        dist["distance_hist_max_um"].append(stats["hi"])
                        dist["distance_hist_counts"].append(stats["counts"][position])
            del transform, flat
        return dist


@dataclass(frozen=True)
class _ForegroundIndex:
    """An entity's labelled voxels, grouped by instance.

    positions: flat voxel indices, sorted by label id
    starts:    where each instance's run begins in positions
    ids:       the label ids, in the same order as starts
    """
    positions: np.ndarray
    starts: np.ndarray
    ids: np.ndarray


def _foreground_index(labels: np.ndarray) -> _ForegroundIndex:
    """Index an entity's foreground once, so each later measurement is a gather."""
    positions = np.flatnonzero(labels)
    # int32 halves this array where the volume allows, and it is the second largest
    # allocation after the distance transform itself.
    if labels.size <= np.iinfo(np.int32).max:
        positions = positions.astype(np.int32, copy=False)
    ids_at = labels.reshape(-1)[positions]
    order = np.argsort(ids_at, kind="stable")
    positions = positions[order]
    ids, starts = np.unique(ids_at[order], return_index=True)
    return _ForegroundIndex(positions=positions, starts=starts, ids=ids)


def _distance_stats(values: np.ndarray, index: _ForegroundIndex) -> Dict[str, Any]:
    """Mean and a binned distribution per instance, from the gathered values.

    The histogram range is shared by every instance of the entity/target pair, rather than
    fitted per instance: shared bins are what makes two instances' distributions
    comparable, and one pass computes them all.
    """
    counts_per_instance = np.diff(np.append(index.starts, len(values)))
    sums = np.add.reduceat(values.astype(np.float64), index.starts)
    means = sums / counts_per_instance
    lo, hi = float(values.min()), float(values.max())
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    # One bincount over (instance, bin) pairs rather than a histogram per instance.
    bins = np.clip(
        ((values - lo) / (hi - lo) * DISTANCE_HISTOGRAM_BINS).astype(np.int64),
        0, DISTANCE_HISTOGRAM_BINS - 1,
    )
    instance_of = np.repeat(np.arange(len(index.ids)), counts_per_instance)
    flat_counts = np.bincount(
        instance_of * DISTANCE_HISTOGRAM_BINS + bins,
        minlength=len(index.ids) * DISTANCE_HISTOGRAM_BINS,
    ).reshape(len(index.ids), DISTANCE_HISTOGRAM_BINS)
    return {
        "mean": [float(m) for m in means],
        "lo": lo,
        "hi": hi,
        "counts": [json.dumps([int(c) for c in row]) for row in flat_counts],
    }


def _polar_spread_deg(
    coords: np.ndarray,
    sample_size: Sequence[float],
    center: Tuple[float, float, float],
) -> float:
    """Angular spread of an instance's voxels on the polarity sphere, in degrees.

    How wide a range of directions the instance covers as seen from the object centre: a
    compact granule reads near zero, a strand wrapping the object reads large.
    """
    if coords.shape[0] < 3:
        return float("nan")
    offsets = coords * np.array(sample_size) - np.array(center)
    radius = np.linalg.norm(offsets, axis=1)
    valid = radius > 0
    if valid.sum() < 3:
        return float("nan")
    unit = offsets[valid] / radius[valid, None]
    mean_dir = unit.mean(axis=0)
    length = float(np.linalg.norm(mean_dir))
    if length > 0:
        mean_dir = mean_dir / length
    dots = np.clip(unit @ mean_dir, -1.0, 1.0)
    return float(np.degrees(np.arccos(dots)).std())
