"""How deep inside the object a point sits, in µm."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
from dask.delayed import Delayed
from scipy import ndimage
from scipy.spatial import cKDTree

from organella.measure.per_label import chunks_with_origin, tree_merged


def _outside_and_touching(chunk: np.ndarray) -> np.ndarray:
    return ndimage.binary_dilation(chunk, structure=np.ones((3,) * chunk.ndim, bool)) & ~chunk


def _coordinates(chunk: np.ndarray, origin: np.ndarray) -> np.ndarray:
    return np.argwhere(chunk) + origin


def _joined(parts: list[np.ndarray]) -> np.ndarray:
    return np.concatenate(parts)


def boundary_voxels(mask: da.Array) -> Delayed:
    """Voxel coordinates ``(n, ndim)`` of every voxel outside the mask that touches it.

    The nearest voxel outside the object is always one of these, so they are all a depth needs. Read off chunk by
    chunk: dask's own argwhere builds an index array of the whole volume.
    """
    touching = mask.map_overlap(_outside_and_touching, depth=1, boundary=0, dtype=bool)
    return tree_merged([dask.delayed(_coordinates)(chunk, origin) for origin, (chunk,) in chunks_with_origin(touching)], _joined)


def depth_um(boundary: np.ndarray, voxel_um: np.ndarray, at_voxel: np.ndarray) -> np.ndarray:
    """Distance from each voxel in ``at_voxel`` to the nearest boundary voxel; NaN when nothing is outside."""
    if not len(boundary):
        return np.full(len(at_voxel), np.nan)
    return cKDTree(boundary * voxel_um).query(at_voxel * voxel_um)[0]
