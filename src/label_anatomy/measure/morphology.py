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

from label_anatomy.analysis.shapes import foreground_centroid
from label_anatomy.analysis.distances import polarity_from_offset
from label_anatomy.analysis.shapes import size_key, total_size_key
from label_anatomy.model import EntityVolume
from label_anatomy.analysis.cache import region_metrics_for
from label_anatomy.measure.instances import null_if_not_finite

logger = logging.getLogger(__name__)

# Both dimensionalities are declared so one report can hold objects of either kind; each
# object fills its own set and leaves the other null.
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
    "entity_name":              "Name of the entity (organelle / structure) this row measures.",
    "entity_colour":            "Colour this structure is drawn in, as #rrggbb, from the settings file given to --colours (or `anatomy colours` afterwards). Null where the file did not name it, and the report falls back to its own palette.",
    "entity_kind":              "'mask' for a single whole structure, 'label' for an instance-segmented entity.",
    "instance_count":           "Number of labelled instances in this entity; null for whole-structure masks, and summed across the object's label entities on the object row.",
    "total_volume_um3":         "Total segmented volume of this entity in µm³ (3D objects).",
    "volume_um3":               "Volume of the structure in µm³ (3D mask entities).",
    "surface_area_um2":         "Surface area of the whole structure in µm², by ITK's Crofton estimator: right on a smooth surface, and about 10% short on a flat axis-aligned face (3D mask entities).",
    "sphericity":               "Surface area of the equal-volume sphere divided by the measured surface area. 1 is a perfect ball and nothing is rounder than one; a few percent above 1 is the boundary estimator coming in short, which it does on instances a handful of voxels across. Flat or faceted shapes read well below: a cube 0.92, a thin slab or a square rod about 0.6.",
    "total_area_um2":           "Total segmented area of this entity in µm² (2D objects).",
    "area_um2":                 "Area of the structure in µm² (2D mask entities).",
    "perimeter_um":             "Perimeter of the whole structure in µm, by ITK's Crofton estimator: right on a smooth outline, and short on a straight axis-aligned edge (2D mask entities).",
    "circularity":              "Perimeter of the equal-area disc divided by the measured perimeter. 1 is a perfect disc; a few percent above it is the boundary estimator coming in short on a very small instance, and an elongated or ragged outline reads below.",
    "aspect_ratio_major_minor": "Ratio of largest to smallest PCA axis length (mask entities).",
    "polar_dist_um":            "Distance in µm from the object centre to this structure's centroid (mask entities).",
    "polar_az_deg":             "Azimuth in degrees of this structure's centroid as seen from the object centre (3D mask entities).",
    "polar_el_deg":             "Elevation in degrees of this structure's centroid as seen from the object centre (3D mask entities).",
    "polar_angle_deg":          "Angle in degrees of this structure's centroid as seen from the object centre (2D mask entities); a plane has one angle, not an azimuth and an elevation.",
    "polar_nz":                 "Z component of the unit vector from the object centre to this structure's centroid (3D).",
    "polar_ny":                 "Y component of the unit vector from the object centre to this structure's centroid.",
    "polar_nx":                 "X component of the unit vector from the object centre to this structure's centroid.",
    "file_name":                "Name of the TIFF this entity was read from.",
    "file_size_bytes":          "Size on disk of that TIFF, in bytes.",
}

# Only the instance count adds up across entities. Extents do not: an object's structures
# all sit inside the object mask, so summing them means nothing.
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

    NAME = "anatomy-morphology"
    DESCRIPTION = (
        "Computes per-entity morphology of an object: volume, surface area, sphericity and PCA "
        "aspect ratio for whole-structure masks, and instance count and total volume for "
        "instance-segmented label entities."
    )

    # One row per entity, so every column of it is an entity-row column; the pipeline rolls
    # some of them up onto the object row (see roll_up).
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
            # The loader recorded the object mask's centroid, so a measurement that sees
            # one entity can still say where it sits.
            center = entity.stack.center
            if centroid is not None and center is not None:
                centroid_um = centroid * np.array(sample_size)
                row.update(polarity_from_offset(centroid_um - np.array(center, dtype=float)))
        else:
            labels = volume.astype(np.int32, copy=False)
            row.update({
                "instance_count": int(np.unique(labels[labels > 0]).size),
                total_size_key(volume.ndim): float((labels > 0).sum() * sample_extent),
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
