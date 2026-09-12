"""Per-entity morphology: one row per structure of an object.

This is measured one entity at a time, so it emits one row per entity per object at
``obs_level=1``, named by the structure it is about. Those rows are what makes
``entity_name`` a groupable column, with scalar metrics the report can plot directly.

Seeing one entity bounds what belongs here: whole-structure morphology for masks, and
counts and totals for labels. Everything that needs the rest of the object - per-instance
measurements, distances, contacts - is measured against the whole stack instead.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

import numpy as np

from organella.analysis.shapes import foreground_centroid
from organella.analysis.distances import polarity_from_offset
from organella.analysis.shapes import size_key, total_size_key
from organella.model import EntityVolume
from organella.analysis.cache import region_metrics_for
from organella.measure.instances import null_if_not_finite

logger = logging.getLogger(__name__)

# Both dimensionalities, so one report can hold objects of either kind; each fills its own
# set and leaves the other null.
_COLUMNS: Dict[str, Any] = {
    "entity_name":              str,
    "entity_kind":              str,
    "entity_colour":            str,
    "instance_count":           np.int64,
    # 3D
    "total_volume_um3":         np.float64,
    "volume_um3":               np.float64,
    "surface_area_um2":         np.float64,
    "sphericity":               np.float64,
    # 2D
    "total_area_um2":           np.float64,
    "area_um2":                 np.float64,
    "perimeter_um":             np.float64,
    "circularity":              np.float64,
    # both
    "aspect_ratio_major_minor": np.float64,
    "polar_dist_um":            np.float64,
    "polar_ny":                 np.float64,
    "polar_nx":                 np.float64,
    # 3D polarity
    "polar_az_deg":             np.float64,
    "polar_el_deg":             np.float64,
    "polar_nz":                 np.float64,
    # 2D polarity
    "polar_angle_deg":          np.float64,
    "file_name":                str,
    "file_size_bytes":          np.int64,
}

_DESCRIPTIONS: Dict[str, str] = {
    "entity_name":              "Name of the structure this row measures, as the segmentation called it.",
    "entity_colour":            "Colour this structure is drawn in, as #rrggbb, from the settings file given to --colours (or `organella colours` afterwards). Null where the file did not name it, and the report falls back to its own palette.",
    "entity_kind":              "'mask' where the structure was segmented as one whole thing, 'label' where it was segmented into separate instances.",
    "instance_count":           "How many separate instances of this structure the object holds. Null for a whole-structure mask, which is one thing rather than many; on the object row, summed over the structures that have instances.",
    "total_volume_um3":         "How much of this structure there is altogether, in µm³: every instance added together, or the whole thing where it was segmented as one rather than into instances.",
    "volume_um3":               "Volume of the whole structure in µm³, where it was segmented as one thing rather than into instances.",
    "surface_area_um2":         "Surface area of the whole structure in µm², by ITK's Crofton estimator: right on a smooth surface, and about 10% short on a flat axis-aligned face.",
    "sphericity":               "Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect sphere and nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does on instances a handful of voxels across. Flat or faceted shapes read well below: a cube 0.92, a thin slab or a square rod about 0.6.",
    "total_area_um2":           "How much of this structure there is altogether, in µm²: every instance added together, or the whole thing where it was segmented as one rather than into instances.",
    "area_um2":                 "Area of the whole structure in µm², where it was segmented as one thing rather than into instances.",
    "perimeter_um":             "Perimeter of the whole structure in µm, by ITK's Crofton estimator: right on a smooth outline, and short on a straight axis-aligned edge.",
    "circularity":              "Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline reads below.",
    "aspect_ratio_major_minor": "Longest principal axis ÷ shortest, for the whole structure. 1 is a sphere, or a disc in a plane; higher is more elongated, flatter, or both.",
    "polar_dist_um":            "Distance in µm from the object centre to this structure's centroid.",
    "polar_az_deg":             "Which way this structure lies from the object centre, around the Z axis, in degrees.",
    "polar_el_deg":             "How far above or below the object centre this structure lies, in degrees.",
    "polar_angle_deg":          "Which way this structure lies from the object centre, in degrees. A plane has one angle where a volume has an azimuth and an elevation.",
    "polar_nz":                 "Z component of the unit vector from the object centre to this structure's centroid.",
    "polar_ny":                 "Y component of the unit vector from the object centre to this structure's centroid.",
    "polar_nx":                 "X component of the unit vector from the object centre to this structure's centroid.",
    "file_name":                "Name of the TIFF this structure was read from.",
    "file_size_bytes":          "Size on disk of that TIFF, in bytes.",
}

# Only the instance count adds up: the structures all sit inside the object mask, so
# summing their extents means nothing.
_SUMMED = {"instance_count"}


def _make_passthrough(col: str) -> Callable[[List[Dict], Dict[str, Any]], Any]:
    """The single row's value, or None when the group spans several entities.

    Per-entity morphology has no meaningful object-level aggregate: an object's "sphericity"
    is not the sphericity of its entities.
    """
    def agg(rows: List[Dict], _g_dims: Dict[str, Any]) -> Any:
        return rows[0].get(col) if len(rows) == 1 else None
    return agg


def _make_sum(col: str) -> Callable[[List[Dict], Dict[str, Any]], Any]:
    def agg(rows: List[Dict], _g_dims: Dict[str, Any]) -> Any:
        vals = [r[col] for r in rows if r.get(col) is not None]
        return sum(vals) if vals else None
    return agg


class MorphologyMeasurer:
    """Whole-structure morphology for a mask entity; counts and totals for a label entity."""

    NAME = "organella-morphology"
    DESCRIPTION = (
        "Computes per-entity morphology of an object: volume, surface area, sphericity and PCA "
        "aspect ratio for whole-structure masks, and instance count and total volume for "
        "instance-segmented label entities."
    )

    # One row per entity. ``roll_up`` below says which of them also add up onto the
    # object row.
    ROW_COLUMNS: Dict[str, Any] = dict(_COLUMNS)
    COLUMN_DESCRIPTIONS: Dict[str, str] = dict(_DESCRIPTIONS)

    def measure(self, entity: EntityVolume) -> Dict[str, Any]:
        """One entity row: what this structure is, and how big and what shape it is."""
        volume = entity.volume
        sample_size = entity.stack.sample_size
        entity_name, entity_kind = entity.name, entity.kind
        sample_extent = float(np.prod(sample_size))

        row: Dict[str, Any] = {"entity_name": entity_name, "entity_kind": entity_kind}
        # Which file this entity came from: an object is a folder, so the object row's own
        # path and size describe the whole of it, not this entity.
        if entity.file_name is not None:
            row["file_name"] = entity.file_name
        if entity.file_bytes is not None:
            row["file_size_bytes"] = entity.file_bytes
        if entity_kind == "mask":
            binary = volume > 0
            metrics = region_metrics_for(
                entity.stack.object_id, entity_name, volume, sample_size)
            centroid = foreground_centroid(binary)
            # instance_count stays null: a mask is one structure, not one instance, which
            # keeps the object-row sum a count of label instances.
            row.update(metrics)
            row[total_size_key(volume.ndim)] = metrics[size_key(volume.ndim)]
            # The object centre travels with the stack, so a measurement of one entity can
            # still say where that entity sits.
            center = entity.stack.center
            if centroid is not None and center is not None:
                centroid_um = centroid * np.array(sample_size)
                row.update(polarity_from_offset(centroid_um - np.array(center, dtype=float)))
        else:
            # One pass for the foreground, used twice: `volume > 0` on a whole entity is
            # tens of megabytes, and this asked for it once to select the ids and again to
            # count them. The int32 cast was a second copy of the volume on top, for a
            # `> 0` and a `unique` that any integer type answers.
            foreground = volume > 0
            row.update({
                "instance_count": int(np.unique(volume[foreground]).size),
                total_size_key(volume.ndim): float(foreground.sum() * sample_extent),
            })
        # NaN would break the very charts these scalars exist for: see null_if_not_finite.
        return {key: null_if_not_finite(value) for key, value in row.items()}

    def roll_up(self, name: str):
        """How an entity column reaches the object row, if it does at all.

        Counts and totals sum; a shape does not - an object's "sphericity" is not the
        sphericity of its entities, so that column is left null on the object row rather
        than filled with the value of whichever entity happened to be alone.
        """
        if name in _SUMMED:
            return _make_sum(name)
        if name in _COLUMNS:
            return _make_passthrough(name)
        return None
