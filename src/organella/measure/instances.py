"""One row per instance, and one per instance-target distance.

Per-instance measurements need the whole object, not one entity: a distance is *to*
another entity, and polarity is relative to the object mask. So this measurer sees every
entity at once, and what it measures becomes rows of its own, two kinds of them
(``row_type='instance'`` and ``row_type='distance'``, see
:mod:`organella.model.rows`):

    SELECT object_id, instance_entity, instance_label, instance_volume_um3
    FROM pp_all WHERE row_type = 'instance'

Distances are a second, longer kind - one row per instance × target - because a distance
belongs to a pair, and target names come from the data:

    SELECT object_id, distance_entity, distance_label, distance_target, distance_um
    FROM pp_all WHERE row_type = 'distance'

One more, short one per target: what the same distance looks like from everywhere in the
object (``row_type='baseline'``), which is what says whether a measured distance is close or
merely as close as anything would be:

    SELECT object_id, baseline_target, baseline_median_um, baseline_hist_counts
    FROM pp_all WHERE row_type = 'baseline'

Rows rather than list columns on the object row: the viewer materialises object rows in
memory, so lists meant loading an object's whole instance table whatever was asked.

Instances come from label entities. A whole-structure mask has none; its morphology is on
its own entity row.

Memory is the constraint - a real object is ~550 megavoxels across five entities - so both
passes keep at most one whole-volume float32 array alive: morphology walks instances one at
a time, distances walk *targets* one at a time.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree

from organella.config import RunConfig, normalize_name, wants_skeletons
from organella.analysis.distances import (
    POLARITY_2D,
    POLARITY_3D,
    distance_target,
    distance_transform_um,
    object_center_um,
    polarity_from_offset,
    segmented_center_um,
)
from organella.analysis.shapes import (
    METRICS_2D,
    METRICS_3D,
    foreground_centroid,
    skeleton_endpoints_um,
    skeleton_graph_metrics,
)
from organella.model import (
    BASELINE_ROW,
    DISTANCE_ROW,
    INSTANCE_ROW,
    ObjectMeasurement,
    ObjectStack,
)
from organella.analysis.cache import (
    CACHE, label_metrics_for, regions_for, skeletons_for,
)

logger = logging.getLogger(__name__)

DISTANCE_HISTOGRAM_BINS = 20

# The chance distribution is one row per target rather than one per instance, so it can
# afford a finer grid than the per-instance histograms: 128 bins over the object is ~1% of
# the range per bin, which is what makes it drawable as a curve rather than a staircase.
BASELINE_HISTOGRAM_BINS = 128
# Binned this finely first, so the median comes off the counts rather than off a sort of
# every voxel in the object - 300 million of them on a real cell - and is still exact to a
# few nanometres. Summed down to the stored bins afterwards.
_BASELINE_FINE_BINS = 4096
# How much of the object a chance pass holds at once. The transform is already the largest
# array in the process, so this walks it rather than gathering it: 8 million voxels is
# 32 MB of float32 per slab whatever the object's size.
_BASELINE_SLAB_VOXELS = 8_000_000

# Both dimensionalities are declared; each object fills its own set and leaves the other
# null.
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

# The same pair, read at the instance's skeleton tips rather than over all of it. Only
# filled for a structure that was skeletonised, so the columns are added to the rows only
# when something in the object has tips at all.
_END_COLUMNS: Dict[str, Any] = {
    "distance_end_min_um": np.float64,
    "distance_end_max_um": np.float64,
}

# One row per target: the distance to it from everywhere in the object.
_BASELINE_COLUMNS: Dict[str, Any] = {
    "baseline_target": str,
    "baseline_excluded": str,
    "baseline_voxels": np.int64,
    "baseline_mean_um": np.float64,
    "baseline_median_um": np.float64,
    "baseline_hist_min_um": np.float64,
    "baseline_hist_max_um": np.float64,
    "baseline_hist_counts": str,
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
    "instance_sphericity": "Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect sphere and nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does on instances a handful of voxels across. Flat or faceted shapes read well below: a cube 0.92, a thin slab or a square rod about 0.6.",
    "instance_area_um2": "Area of this instance in µm².",
    "instance_perimeter_um": "Perimeter of this instance in µm, by ITK's Crofton estimator: right on a smooth outline, and short on a straight axis-aligned edge.",
    "instance_circularity": "Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline reads below.",
    "instance_aspect_ratio_major_minor": "Longest principal axis ÷ shortest. 1 is a sphere, or a disc in a plane; higher is more elongated, flatter, or both.",
    "instance_branches": "Branches in this instance's curve skeleton. Only measured for the structures a run named with --skeletons.",
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
    "distance_end_min_um": "Smallest distance in µm from one of this instance's skeleton tips to the target structure: how close its *end* gets, where distance_um is how close any part of it gets. A filament can run past a structure along its whole length and end nowhere near it, which is the difference these two columns are for. Null for a structure with no curve skeleton (--skeletons), and for an instance whose skeleton is a closed loop and so has no tips.",
    "distance_end_max_um": "Largest distance in µm from one of this instance's skeleton tips to the target structure: the tip that sits furthest from it. With the usual two tips, the pair says whether one end is against the structure while the other is not.",
    "baseline_target": "Structure the chance distribution on this row is measured to. One row per structure measured to, for the object as a whole.",
    "baseline_excluded": "Structures left out of the region, from --baseline-exclude, comma separated. The target itself is always out of it, since its distance to itself is zero. Null where nothing else was excluded.",
    "baseline_voxels": "How many samples of the object the chance distribution was taken over.",
    "baseline_mean_um": "Mean distance in µm to this structure over every sample of the region: how far from it a point of the object sits on average.",
    "baseline_median_um": "Median distance in µm to this structure over the region - the distance half the object is closer than. This is the number a measured distance is read against: a structure whose instances sit at 0.3 µm from the membrane where half the object is within 0.9 µm is closer to it than the object's shape alone would put them; one at 0.9 µm is exactly as close as anything would be. Taken from a 4096-bin histogram rather than a sort of every sample, so it is exact to about a thousandth of the range.",
    "baseline_hist_min_um": "Lower bound of the chance histogram's range.",
    "baseline_hist_max_um": "Upper bound of the chance histogram's range.",
    "baseline_hist_counts": "Sample counts over that range, as a JSON array of fixed-width bins: the shape of the chance distribution, to draw a measured distribution against.",
    "polar_depth_um": "How deep inside the object mask this structure's centre sits, in µm: the distance from its centroid to the boundary. The comparable way to say where a structure sits, since objects differ in size - where polar_dist_um says how far from the centre it is, which is only readable against that object's own extent. 0 where the centroid falls outside the mask, which a horseshoe-shaped structure's can; null without a bounding mask.",
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

    Left as NaN, an aggregate over the column raises rather than skipping it, and
    min/max/quantiles come back nan.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metrics_by_instance(inst: Dict[str, List[Any]]) -> Dict[tuple, Dict[str, Any]]:
    """The instance lists as {(entity, label): {metric: value}}, for joining elsewhere.

    Keys drop the "instance_" prefix, so the same values can be written under the shorter
    names (volume_um3, polar_nx, ...).
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

    Every column keeps its length, so the rows stay aligned, and its declared type, so a
    column no instance could fill is still typed rather than untyped nulls.
    """
    return {name: [null_if_not_finite(v) for v in values] for name, values in table.items()}


class InstanceMeasurer:
    """Per-instance morphology, distances and polarity for a whole object."""

    NAME = "organella-instances"
    DESCRIPTION = (
        "Measures every labelled instance in an object: volume, surface area, sphericity, PCA aspect "
        "ratio, curve-skeleton metrics, distance to each other entity (from all of it and from its "
        "skeleton tips), distance to the closest instance of its own entity, and its direction from "
        "the object centre. Also the chance distribution of each distance - the same distance from "
        "everywhere in the object - which is what a measured one is read against."
    )

    # The object row's own columns, then the columns of the rows below it.
    OBJECT_COLUMNS: Dict[str, Any] = dict(_OBJECT_COLUMNS)
    ROW_SCHEMAS: Dict[str, Dict[str, Any]] = {
        INSTANCE_ROW: dict(_INSTANCE_COLUMNS),
        DISTANCE_ROW: {**_DISTANCE_COLUMNS, **_END_COLUMNS},
        BASELINE_ROW: dict(_BASELINE_COLUMNS),
    }
    COLUMN_DESCRIPTIONS: Dict[str, str] = dict(_DESCRIPTIONS)

    def __init__(self, config: Optional[RunConfig] = None) -> None:
        self._config = config if config is not None else RunConfig()

    def measure(self, stack: ObjectStack) -> ObjectMeasurement:
        volumes = stack.volumes()
        sample_size = stack.sample_size
        object_mask_name = stack.object_mask_name

        out: Dict[str, Any] = {}
        if object_mask_name in volumes:
            sample_extent = float(np.prod(sample_size))
            enclosed = float((volumes[object_mask_name] > 0).sum() * sample_extent)
            out["object_area_um2" if stack.spatial_dims == 2 else "object_volume_um3"] = enclosed

        # One origin for every row of this object, recomputed by the same two rules only
        # when the stack arrived without one: the bounding mask's centroid, or the centre of
        # everything segmented.
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

        dist, baseline, depths = self._measure_distances(
            volumes, stack.kinds, label_names, ids_by_entity, sample_size,
            object_mask_name, object_id=stack.object_id,
        )

        logger.info(
            "organella: %s: %d instances, %d instance-target distances, "
            "%d chance distributions",
            stack.object_id, len(inst["instance_label"]), len(dist["distance_um"]),
            len(baseline["baseline_target"]),
        )
        # Kept on the cache, so these metrics are measured once per object however many
        # readers want them.
        CACHE.get_or_compute(
            stack.object_id, ("instance_metrics",), stack.data,
            lambda: _metrics_by_instance(inst),
        )
        return ObjectMeasurement(
            columns=out,
            entity_columns={name: {"polar_depth_um": null_if_not_finite(depth)}
                            for name, depth in depths.items()},
            tables={INSTANCE_ROW: _nulled(inst), DISTANCE_ROW: _nulled(dist),
                    BASELINE_ROW: _nulled(baseline)},
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
            if wants_skeletons(entity, cfg.geometry_as, cfg.skeletons) else {}
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
        # A tree, not an all-pairs matrix: at 6340 instances that is 8.9 ms and 0.3 MB
        # against 238 ms and 322 MB for the same answers. k=2 because the closest point to
        # a point is itself.
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
        object_id: str = "object",
    ) -> Tuple[Dict[str, List[Any]], Dict[str, List[Any]], Dict[str, float]]:
        """One row per (instance, target), one per target, and one depth per structure.

        Targets are the outer loop so only one distance transform exists at a time. Each
        entity's foreground is indexed once, after which measuring it against a transform
        is a gather plus a reduceat over the foreground alone: 0.01 s where scipy's
        ndimage.minimum, whose cost follows the volume and label count instead, took 54 s
        on a 197-megavoxel object with 10k instances.

        The transform is already built, so more readings come almost free off it: the
        distance at each instance's skeleton tips, the distance from everywhere in the
        object - the chance distribution a measured distance has to be read against - and,
        off the bounding mask's own transform, how deep each structure's centre sits inside
        it. That last one is the only thing here that lands on a *structure's* row: a mask
        was measured as one whole thing, so it has no instance to carry a distance, and
        without this there is nothing comparable to say about where it sits.
        """
        cfg = self._config
        # Only the columns that get filled: the histogram ones are dropped from the report
        # rather than written as a column of nulls per distance row.
        columns = list(_DISTANCE_COLUMNS) if cfg.distance_histograms else [
            "distance_entity", "distance_label", "distance_target", "distance_um",
        ]
        ends = self._endpoint_indexes(views, label_names, ids_by_entity, sample_size,
                                      object_id)
        if ends:
            columns += list(_END_COLUMNS)
        dist: Dict[str, List[Any]] = {col: [] for col in columns}
        baseline: Dict[str, List[Any]] = {col: [] for col in _BASELINE_COLUMNS}
        depths: Dict[str, float] = {}
        region = _ReferenceRegion.of(views, object_mask_name, cfg.baseline_exclude)
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
            if target == object_mask_name:
                # This transform is the depth inside the object, measured from the
                # boundary: every structure's centre can be read straight off it.
                depths = _centre_depths(views, transform)
            chance = region.distribution(transform)
            if chance is not None:
                baseline["baseline_target"].append(target)
                baseline["baseline_excluded"].append(region.excluded_label)
                baseline["baseline_voxels"].append(chance["voxels"])
                baseline["baseline_mean_um"].append(chance["mean"])
                baseline["baseline_median_um"].append(chance["median"])
                baseline["baseline_hist_min_um"].append(chance["lo"])
                baseline["baseline_hist_max_um"].append(chance["hi"])
                baseline["baseline_hist_counts"].append(chance["counts"])
            for name in measured:
                index = indexes[name]
                values = flat[index.positions]
                mins = np.minimum.reduceat(values, index.starts)
                stats = _distance_stats(values, index) if cfg.distance_histograms else None
                at_ends = _end_distances(flat, ends.get(name)) if ends else None
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
                    if ends:
                        near, far = (at_ends or {}).get(int(label_id),
                                                        (float("nan"), float("nan")))
                        dist["distance_end_min_um"].append(near)
                        dist["distance_end_max_um"].append(far)
            del transform, flat
        return dist, baseline, depths

    def _endpoint_indexes(
        self,
        views: Dict[str, np.ndarray],
        label_names: List[str],
        ids_by_entity: Dict[str, List[int]],
        sample_size: Sequence[float],
        object_id: str,
    ) -> Dict[str, "_ForegroundIndex"]:
        """Each skeletonised structure's tips, indexed like its foreground.

        Only the structures that were skeletonised have tips, and the skeletons are the
        ones the morphology pass already computed: the cache is keyed on the object and the
        array, so this is a lookup rather than a second TEASAR run.
        """
        cfg = self._config
        out: Dict[str, "_ForegroundIndex"] = {}
        for name in label_names:
            if not ids_by_entity.get(name):
                continue
            if not wants_skeletons(name, cfg.geometry_as, cfg.skeletons):
                continue
            skeletons = skeletons_for(object_id, name, views[name], sample_size,
                                      cfg.max_skeleton_voxels, cfg.num_threads)
            index = _endpoint_index(views[name], skeletons, sample_size)
            if index is not None:
                out[name] = index
        return out


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
    # int32 halves the second largest allocation after the distance transform itself.
    if labels.size <= np.iinfo(np.int32).max:
        positions = positions.astype(np.int32, copy=False)
    ids_at = labels.reshape(-1)[positions]
    order = np.argsort(ids_at, kind="stable")
    positions = positions[order]
    ids, starts = np.unique(ids_at[order], return_index=True)
    return _ForegroundIndex(positions=positions, starts=starts, ids=ids)


def _endpoint_index(
    labels: np.ndarray,
    skeletons: Dict[int, Any],
    sample_size: Sequence[float],
) -> Optional[_ForegroundIndex]:
    """One structure's skeleton tips as voxels, grouped by instance, or None if it has none.

    The same shape as a foreground index, so a tip distance is read off a transform the
    same way a whole-instance one is: gather, then reduce per instance. Vertices come back
    in µm, so they are rounded to the voxel they sit in and clipped to the volume - a
    skeleton is built inside it, and a rounded border vertex must not index outside it.
    """
    spacing = np.asarray(sample_size, dtype=float)
    shape = np.asarray(labels.shape)
    groups: List[Tuple[int, np.ndarray]] = []
    for label_id, skeleton in skeletons.items():
        tips = skeleton_endpoints_um(skeleton)
        if not len(tips):
            continue
        voxels = np.clip(np.rint(tips / spacing).astype(np.int64), 0, shape - 1)
        flat = np.ravel_multi_index(tuple(voxels.T), labels.shape)
        groups.append((int(label_id), np.unique(flat)))
    if not groups:
        return None
    groups.sort()
    positions = np.concatenate([flat for _, flat in groups])
    counts = np.array([len(flat) for _, flat in groups])
    return _ForegroundIndex(
        positions=positions,
        starts=np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64),
        ids=np.array([label_id for label_id, _ in groups], dtype=np.int64),
    )


def _centre_depths(
    views: Dict[str, np.ndarray], depth: np.ndarray,
) -> Dict[str, float]:
    """How deep inside the object each structure's centre sits, off the boundary transform.

    The centroid of the structure, rounded to the sample it falls in. A structure whose
    centroid falls outside itself - a horseshoe, a shell - still has a centre, and the depth
    of that centre is what this is; where it falls outside the *object* the transform reads
    zero, which is the honest answer rather than an extrapolation.
    """
    out: Dict[str, float] = {}
    shape = np.asarray(depth.shape)
    for name, volume in views.items():
        centroid = foreground_centroid(volume > 0)
        if centroid is None:
            continue
        at = np.clip(np.rint(centroid).astype(np.int64), 0, shape - 1)
        out[name] = float(depth[tuple(at)])
    return out


def _end_distances(
    flat: np.ndarray, index: Optional[_ForegroundIndex],
) -> Optional[Dict[int, Tuple[float, float]]]:
    """``{label: (nearest tip, furthest tip)}`` for one structure against one transform."""
    if index is None or not len(index.positions):
        return None
    values = flat[index.positions]
    nearest = np.minimum.reduceat(values, index.starts)
    furthest = np.maximum.reduceat(values, index.starts)
    return {int(label_id): (float(near), float(far))
            for label_id, near, far in zip(index.ids, nearest, furthest)}


@dataclass(frozen=True)
class _ReferenceRegion:
    """Everywhere in the object a distance is read against.

    The chance distribution: the same distance transform, over the object itself rather
    than over one structure's instances. Without it a distance has no scale - granules
    100 nm from the membrane are against it in a cell where half the volume is 1 µm away,
    and unremarkable in one where half of it is 80 nm away - and the difference is the
    object's shape, not anything about the granules.

    ``mask`` bounds it; with no bounding mask the whole analysed region is the reference.
    ``excluded`` are the structures --baseline-exclude named: ground an instance could
    never have occupied, which would otherwise pad the reference with distances no instance
    could ever have had. The target itself needs no excluding - its own distance to itself
    is zero, and zero is what identifies it (every other sample is at least one voxel
    away).
    """

    mask: Optional[np.ndarray]
    excluded: Tuple[np.ndarray, ...]
    excluded_names: Tuple[str, ...]
    slab_rows: int

    @classmethod
    def of(cls, views: Dict[str, np.ndarray], object_mask_name: Optional[str],
           exclude: Any) -> "_ReferenceRegion":
        """The region for one object: what bounds it, and what is left out of it."""
        wanted = {normalize_name(name) for name in (exclude or ())}
        names = tuple(sorted(
            name for name in views
            if normalize_name(name) in wanted and name != object_mask_name
        ))
        shape = next(iter(views.values())).shape if views else (1,)
        per_row = int(np.prod(shape[1:])) or 1
        return cls(
            mask=views.get(object_mask_name) if object_mask_name else None,
            excluded=tuple(views[name] for name in names),
            excluded_names=names,
            slab_rows=max(1, _BASELINE_SLAB_VOXELS // per_row),
        )

    @property
    def excluded_label(self) -> Optional[str]:
        return ", ".join(self.excluded_names) or None

    def _slabs(self, transform: np.ndarray):
        """The region's distances, a slab of the object at a time.

        Walked rather than gathered: ``transform[region]`` on a whole cell is another
        gigabyte beside the transform itself, for a histogram that needs one pass.
        """
        for start in range(0, transform.shape[0], self.slab_rows):
            piece = slice(start, min(transform.shape[0], start + self.slab_rows))
            keep = (self.mask[piece] > 0 if self.mask is not None
                    else np.ones(transform[piece].shape, dtype=bool))
            for other in self.excluded:
                keep &= other[piece] == 0
            values = transform[piece][keep]
            # Zero is the target itself: every sample outside it is at least one voxel
            # away, so this is how the structure is kept out of its own reference.
            yield values[values > 0]

    def distribution(self, transform: np.ndarray) -> Optional[Dict[str, Any]]:
        """Mean, median and a binned distribution of the region's distances, in two passes.

        One pass to find the range and the mean, a second to bin it - the alternative is
        holding every value to bin it afterwards, which is the allocation this exists to
        avoid.
        """
        voxels, total = 0, 0.0
        lo, hi = float("inf"), float("-inf")
        for values in self._slabs(transform):
            if not values.size:
                continue
            voxels += int(values.size)
            total += float(values.sum(dtype=np.float64))
            lo = min(lo, float(values.min()))
            hi = max(hi, float(values.max()))
        if not voxels or not math.isfinite(lo) or not math.isfinite(hi):
            return None
        if hi <= lo:
            hi = lo + 1.0
        counts = np.zeros(_BASELINE_FINE_BINS, dtype=np.int64)
        for values in self._slabs(transform):
            if not values.size:
                continue
            bins = np.clip(
                ((values - lo) / (hi - lo) * _BASELINE_FINE_BINS).astype(np.int64),
                0, _BASELINE_FINE_BINS - 1,
            )
            counts += np.bincount(bins, minlength=_BASELINE_FINE_BINS)
        fine_width = (hi - lo) / _BASELINE_FINE_BINS
        stored = counts.reshape(BASELINE_HISTOGRAM_BINS, -1).sum(axis=1)
        return {
            "voxels": voxels,
            "mean": total / voxels,
            "median": _median_from_counts(counts, lo, fine_width),
            "lo": lo,
            "hi": hi,
            "counts": json.dumps([int(c) for c in stored]),
        }


def _median_from_counts(counts: np.ndarray, lo: float, width: float) -> float:
    """The median of a binned population, interpolated inside the bin that holds it."""
    total = int(counts.sum())
    if total <= 0:
        return float("nan")
    cumulative = np.cumsum(counts)
    half = total / 2.0
    at = min(int(np.searchsorted(cumulative, half, side="left")), len(counts) - 1)
    before = float(cumulative[at - 1]) if at else 0.0
    within = float(counts[at])
    share = (half - before) / within if within > 0 else 0.0
    return lo + (at + share) * width


def _distance_stats(values: np.ndarray, index: _ForegroundIndex) -> Dict[str, Any]:
    """Mean and a binned distribution per instance, from the gathered values.

    The histogram range is shared across the entity/target pair rather than fitted per
    instance: shared bins are what makes two distributions comparable.
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
