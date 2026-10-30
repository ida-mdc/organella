"""Reading the box around one instance, for measurements that need the instance whole."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
import polars as pl
from dask.delayed import Delayed


def box(data: da.Array, low: np.ndarray, high: np.ndarray, pad: np.ndarray | int = 0, step: int = 1) -> da.Array:
    """The voxels of ``low``..``high`` (inclusive) widened by ``pad`` voxels per axis and clipped to the array,
    every ``step``-th along each axis."""
    pad = np.broadcast_to(pad, low.shape)
    lo, hi = np.maximum(low - pad, 0), np.minimum(high + pad + 1, data.shape)
    return data[tuple(slice(int(a), int(b), step) for a, b in zip(lo, hi))]


def compute_in_batches(lazies: list[Delayed], batch: int) -> list[pl.DataFrame]:
    """Compute in batches, so the windows a batch reads are not all alive together."""
    return [frame for start in range(0, len(lazies), batch) for frame in dask.compute(*lazies[start:start + batch])]
