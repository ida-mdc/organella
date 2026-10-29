from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class VoxelSize:
    """Edge of one voxel in µm along each array axis."""

    um: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.um or any(edge <= 0 for edge in self.um):
            raise ValueError(f"voxel size must be positive along every axis, got {self.um}")

    @property
    def ndim(self) -> int:
        return len(self.um)

    @property
    def axes(self) -> str:
        return "zyx"[-self.ndim:]

    @property
    def volume(self) -> float:
        return math.prod(self.um)

    def voxels_within(self, distance_um: float) -> tuple[int, ...]:
        """Whole voxels along each axis that cover ``distance_um``."""
        return tuple(math.ceil(distance_um / edge) for edge in self.um)
