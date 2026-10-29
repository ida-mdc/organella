from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import dask.array as da

from organella.core.structure import Kind, Structure
from organella.core.voxel import VoxelSize


@dataclass(frozen=True)
class Sample:
    """The structures of one measured object, aligned on one voxel grid."""

    id: str
    group: str | None
    voxel_size: VoxelSize
    structures: Mapping[str, Structure]
    object_mask: str | None = None

    def __post_init__(self) -> None:
        shapes = {s.data.shape for s in self.structures.values()}
        if len(shapes) > 1:
            raise ValueError(f"{self.id}: structures differ in shape: {sorted(shapes)}")
        if self.voxel_size.ndim != len(self.shape):
            raise ValueError(f"{self.id}: voxel size has {self.voxel_size.ndim} values for {len(self.shape)}D data")
        if 0 in self.shape:
            raise ValueError(f"{self.id}: sample is empty, shape {self.shape}")
        if self.object_mask is not None and self.object_mask not in self.structures:
            raise ValueError(f"{self.id}: object mask '{self.object_mask}' is not a structure")

    @property
    def _any(self) -> Structure:
        return next(iter(self.structures.values()))

    @property
    def shape(self) -> tuple[int, ...]:
        return self._any.data.shape

    @property
    def region(self) -> da.Array:
        """The voxels that belong to the object: its mask, or everything without one."""
        if self.object_mask is None:
            return da.ones_like(self._any.data, dtype=bool)
        return self.structures[self.object_mask].foreground

    def distance_target(self, name: str) -> da.Array:
        """The voxels "distance to ``name``" is measured to. For the object mask that is everything outside it,
        so the distance reads as depth inside the object."""
        structure = self.structures[name]
        return ~structure.foreground if name == self.object_mask else structure.foreground

    def labelled(self) -> list[Structure]:
        return [s for s in self.structures.values() if s.kind is Kind.LABEL]

