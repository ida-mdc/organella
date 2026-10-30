"""A cell's mask at the working scale, rebuilt a chunk at a time from the coarse block it was cut from."""
from __future__ import annotations

from pathlib import Path

import dask.array as da
import numpy as np


class _Upsampled:
    """What dask needs of an array: a shape, a dtype and slicing that returns numpy."""

    dtype = np.dtype(bool)

    def __init__(self, coarse: np.ndarray, picks: tuple[np.ndarray, ...]) -> None:
        self._coarse, self._picks = coarse, picks
        self.shape, self.ndim = tuple(len(p) for p in picks), len(picks)

    def __getitem__(self, index):
        return self._coarse[np.ix_(*(pick[where] for pick, where in zip(self._picks, index)))]


def open_cell_mask(path: Path, chunks: int = 128) -> da.Array:
    with np.load(path) as stored:
        coarse = stored["coarse"]
        picks = tuple(stored[f"pick{axis}"] for axis in range(coarse.ndim))
    return da.from_array(_Upsampled(coarse, picks), chunks=chunks, asarray=False)
