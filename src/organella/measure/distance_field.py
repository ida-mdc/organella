"""Lazy arrays derived from a structure, shared by the measurements."""
from __future__ import annotations

import dask.array as da
import numpy as np

from organella.core import VoxelSize
from organella.measure.transform import distance_to_voxels


def reach_cap(reach_um: float) -> np.float32:
    """The reach as the distance field stores it. Every comparison with the field uses this one number."""
    return np.float32(reach_um)


def distance_to(target: da.Array, voxel_size: VoxelSize, reach_um: float) -> da.Array:
    """µm from every voxel to the nearest ``target`` voxel, capped at the reach, in float32.

    Each chunk is read with a halo covering the reach, so a distance below the cap is exact
    and anything farther reads as the cap.
    """
    cap = reach_cap(reach_um)

    def per_chunk(chunk: np.ndarray) -> np.ndarray:
        if not chunk.any():
            return np.full(chunk.shape, cap, dtype=np.float32)
        return np.minimum(distance_to_voxels(chunk, voxel_size.um), cap)

    # A halo longer than the volume holds nothing past its edge, so it is cut to the volume.
    halo = tuple(min(h, n) for h, n in zip(voxel_size.voxels_within(reach_um), target.shape))
    field = target.map_overlap(per_chunk, depth=halo, boundary=0, dtype=np.float32)
    return field.rechunk(target.chunks)  # overlap rechunks a trailing chunk shorter than the halo
