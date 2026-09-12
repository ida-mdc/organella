"""What one object is, once it has been read: the stack, and one entity of it.

An object is a folder of volumes that have to be measured together - a distance is *to*
another entity, a contact is *between* two of them - so it is read once into a single array
with the entities stacked along ``C``, and every measurement works off that one array.

The only type the measuring code speaks, and a named thing rather than a generic record
with dimension inference and a metadata dict, which would hide what an object has.
Everything it knows is a named accessor, and the fragment rule - an object is measured
whole - is checked once on construction rather than at the top of every measurer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

import numpy as np

LABEL = "label"


# ── the spatial shape of an object ────────────────────────────────────────────
#
# Read off the stack's own dim_order ("CZYX" for a volume, "CYX" for a plane), so a
# coordinate array and a sample size are always in the same order.

_SPATIAL = ("Z", "Y", "X")


def spatial_axes(dim_order: str) -> str:
    """The spatial axes in array order: ``"ZYX"`` or ``"YX"``."""
    return "".join(ax for ax in dim_order if ax in _SPATIAL)


def _sample_size(meta: Mapping[str, Any], dim_order: str) -> Tuple[float, ...]:
    """Size of one sample along each spatial axis, µm, in array order.

    ``pixel_size_Z`` is absent for a plane, never 1.0: a made-up depth would turn an area
    into a volume.
    """
    return tuple(float(meta[f"pixel_size_{ax}"]) for ax in spatial_axes(dim_order))


def _recorded_center(meta: Mapping[str, Any], dim_order: str) -> Optional[Tuple[float, ...]]:
    """The object mask's centroid in µm, in array order, or None if it was not recorded."""
    center = [meta.get(f"object_center_{ax.lower()}_um") for ax in spatial_axes(dim_order)]
    if any(value is None for value in center):
        return None
    return tuple(float(value) for value in center)


@dataclass(frozen=True)
class ObjectStack:
    """Every entity volume of one object, stacked along ``C``.

    ``meta`` is what was found out while reading the object, in the shape the report's
    object row takes: entity names and kinds, the files, the voxel size, the object mask's
    centroid. Passed straight through rather than re-derived, so it stays a mapping.
    """

    data: np.ndarray
    dim_order: str                      # "CZYX" for a volume, "CYX" for a plane
    meta: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Refuse a fragment of an object, wherever it came from.

        Measurements are cross-entity and whole-region, so two of five entities or a slab
        of the region answers wrongly rather than partially. Checked here so the rule has
        one home instead of a spelling per measurer.
        """
        c_axis = self.dim_order.index("C")
        names = list(self.meta.get("channel_names") or [])
        expected = [int(v) for v in (self.meta.get("object_shape") or [])]
        spatial = [s for i, s in enumerate(self.data.shape) if i != c_axis]
        if self.data.shape[c_axis] != len(names) or (expected and spatial != expected):
            raise ValueError(
                f"object arrived as a {self.data.shape} fragment of "
                f"{len(names)}×{tuple(expected)}: an object is measured whole, so a fragment "
                "means the caller split it"
            )

    # ── what the object is ────────────────────────────────────────────────────

    @property
    def object_id(self) -> str:
        return str(self.meta.get("object_id") or "object")

    @property
    def entity_names(self) -> List[str]:
        return list(self.meta.get("channel_names") or [])

    @property
    def kinds(self) -> Dict[str, str]:
        """Entity name -> 'label' or 'mask'."""
        return dict(zip(self.entity_names, self.meta.get("entity_kinds") or []))

    @property
    def label_names(self) -> List[str]:
        """The entities that carry instances; a mask is one structure, not one instance."""
        kinds = self.kinds
        return [name for name in self.entity_names if kinds[name] == LABEL]

    @property
    def object_mask_name(self) -> Optional[str]:
        """The mask that bounds the object, or None when nothing does."""
        name = self.meta.get("object_mask_name")
        return str(name) if name else None

    @property
    def sample_size(self) -> Tuple[float, ...]:
        """Size of one voxel (or pixel) along each spatial axis, in µm, in array order."""
        return _sample_size(self.meta, self.dim_order)

    @property
    def spatial_dims(self) -> int:
        """3 for a volume, 2 for a plane."""
        return len(spatial_axes(self.dim_order))

    @property
    def center(self) -> Optional[Tuple[float, ...]]:
        """The object mask's centroid in µm, the origin of every polarity metric."""
        return _recorded_center(self.meta, self.dim_order)

    # ── how the volumes are reached ───────────────────────────────────────────

    def volume(self, index: int) -> np.ndarray:
        """One entity's volume as a *view* - never a copy.

        np.take would copy, which on a 550-megavoxel channel is a gigabyte per entity.
        """
        key: List[Any] = [slice(None)] * self.data.ndim
        key[self.dim_order.index("C")] = index
        return self.data[tuple(key)]

    def volumes(self) -> Dict[str, np.ndarray]:
        """Every entity by name, as views."""
        return {name: self.volume(i) for i, name in enumerate(self.entity_names)}

    def entity(self, index: int) -> "EntityVolume":
        return EntityVolume(self, index)

    def entities(self) -> Iterator["EntityVolume"]:
        """One entity at a time, in channel order - the order ``dim_c`` indexes."""
        for index in range(len(self.entity_names)):
            yield EntityVolume(self, index)


@dataclass(frozen=True)
class EntityVolume:
    """One entity of one object: its own volume, and what the object knows about it.

    A per-entity measurement sees only this, and still needs the voxel size and the object
    centre it is placed against, so it carries the stack rather than copies.
    """

    stack: ObjectStack
    index: int

    @property
    def name(self) -> str:
        return self.stack.entity_names[self.index]

    @property
    def kind(self) -> str:
        return list(self.stack.meta.get("entity_kinds") or [])[self.index]

    @property
    def volume(self) -> np.ndarray:
        return self.stack.volume(self.index)

    @property
    def file_name(self) -> Optional[str]:
        """The file this entity was read from; the object itself is a folder."""
        files = list(self.stack.meta.get("entity_files") or [])
        return files[self.index] if self.index < len(files) else None

    @property
    def file_bytes(self) -> Optional[int]:
        sizes = list(self.stack.meta.get("entity_file_bytes") or [])
        return int(sizes[self.index]) if self.index < len(sizes) else None
