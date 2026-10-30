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


def over_instances(data: da.Array, stats: pl.DataFrame, task, batch: int) -> list:
    """``task(data, low, high, label)``, a lazy result, for every instance in ``stats`` (``id`` and a bounding box
    ``lo0``..``hi0``..), computed ``batch`` at a time. Results come back in row order."""
    ndim = sum(c.startswith("lo") for c in stats.columns)
    low = stats.select(f"lo{i}" for i in range(ndim)).to_numpy()
    high = stats.select(f"hi{i}" for i in range(ndim)).to_numpy()
    ids = stats["id"].to_list()
    return compute_in_batches([task(data, low[r], high[r], int(ids[r])) for r in range(stats.height)], batch)
