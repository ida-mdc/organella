"""Which instances of one structure touch, and how closely.

Only between instances of the same structure, and only for labelled structures: two mitochondria touching is
a network of mitochondria, where a mitochondrion touching the nucleus is a distance to the nucleus, which the
distance rows carry. The gap is the smallest voxel-to-voxel distance, so instances sharing a face are one voxel
step apart, not zero.
"""
from __future__ import annotations

from itertools import product

import dask
import dask.array as da
import numpy as np
import polars as pl
from dask.delayed import Delayed

from organella.core import VoxelSize
from organella.measure.transform import distance_to_voxels
from organella.measure.windows import box

_PAIRS = {"a": pl.Int64, "b": pl.Int64, "gap": pl.Float64}
# An instance whose box, widened by the contact distance, is past this many voxels is searched a tile at a time: a network
# that runs through the whole cell would otherwise be read and transformed whole.
WHOLE_WINDOW_VOXELS = 50_000_000
TILE_EDGE = 128


def _gaps_around(window: np.ndarray, label: int, spacing_um: tuple[float, ...], max_gap_um: float) -> pl.DataFrame:
    """Gaps from instance ``label`` to every other instance in a window that reaches ``max_gap_um`` past it."""
    others = (window > 0) & (window != label)
    if not others.any():
        return pl.DataFrame(schema=_PAIRS)
    to_this_one = distance_to_voxels(window == label, spacing_um)
    nearest = pl.DataFrame({"b": window[others], "gap": to_this_one[others].astype(np.float64)}).group_by("b").min()
    return nearest.filter(pl.col("gap") <= max_gap_um).select(
        pl.min_horizontal(pl.lit(label), pl.col("b")).cast(pl.Int64).alias("a"),
        pl.max_horizontal(pl.lit(label), pl.col("b")).cast(pl.Int64).alias("b"),
        "gap",
    )


def _gaps_in_tile(window: np.ndarray, label: int, spacing_um: tuple[float, ...], max_gap_um: float) -> pl.DataFrame:
    if not (window == label).any():
        return pl.DataFrame(schema=_PAIRS)
    return _gaps_around(window, label, spacing_um, max_gap_um)


def _joined(frames: list[pl.DataFrame]) -> pl.DataFrame:
    return pl.concat(frames)


def lazy_gaps(data: da.Array, low: np.ndarray, high: np.ndarray, label: int, voxel_size: VoxelSize,
              max_gap_um: float) -> Delayed:
    """The pairs instance ``label`` is in contact with, as a frame of ``a``, ``b`` and ``gap``.

    A big instance is searched in tiles, each widened by the contact distance. A tile gives only distances between real
    voxels, never smaller than the truth, and the tile that holds the nearer voxel of a pair finds its exact gap; the
    closest one is kept when the frames are merged.
    """
    pad = np.array(voxel_size.voxels_within(max_gap_um))
    spacing = tuple(voxel_size.um)
    if np.prod(high - low + 1 + 2 * pad, dtype=np.float64) <= WHOLE_WINDOW_VOXELS:
        return dask.delayed(_gaps_around)(box(data, low, high, pad=pad), label, spacing, max_gap_um)
    tiles = [dask.delayed(_gaps_in_tile)(box(data, np.array(start), np.minimum(np.array(start) + TILE_EDGE - 1, high), pad=pad),
                                         label, spacing, max_gap_um)
             for start in product(*(range(int(lo), int(hi) + 1, TILE_EDGE) for lo, hi in zip(low, high)))]
    return dask.delayed(_joined)(tiles)


def merged(frames: list[pl.DataFrame], entity: str) -> pl.DataFrame:
    """Each pair once, at the closer of the two gaps its instances found, sorted so a report is reproducible."""
    pairs = pl.concat(frames) if frames else pl.DataFrame(schema=_PAIRS)
    return (pairs.group_by("a", "b").agg(pl.col("gap").min()).sort("a", "b")
            .select(pl.lit(entity).alias("contact_entity"), pl.col("a").alias("contact_label_a"),
                    pl.col("b").alias("contact_label_b"), pl.col("gap").alias("contact_gap_um")))
