"""Surface area and sphericity of instances (perimeter and circularity of a plane), by ITK's Crofton estimate."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
import polars as pl
import SimpleITK as sitk
from dask.delayed import Delayed

from organella.measure.windows import box


def _crofton(window: np.ndarray, label: int | None, spacing_um: tuple[float, ...]) -> pl.DataFrame:
    """The estimate for one region, padded with background so a region at the edge is bounded like any other."""
    region = np.pad(window == label if label is not None else window > 0, 1)
    if not region.any():
        return pl.DataFrame({"boundary": [np.nan], "roundness": [np.nan]})
    image = sitk.GetImageFromArray(region.astype(np.uint8))
    image.SetSpacing(tuple(float(s) for s in reversed(spacing_um)))  # ITK orders x, y, z
    statistics = sitk.LabelShapeStatisticsImageFilter()
    statistics.SetComputePerimeter(True)
    statistics.Execute(image)
    return pl.DataFrame({"boundary": [statistics.GetPerimeter(1)], "roundness": [statistics.GetRoundness(1)]})


def lazy_surface(data: da.Array, low: np.ndarray, high: np.ndarray, label: int | None,
                 spacing_um: tuple[float, ...], max_voxels: int) -> Delayed:
    """The estimate for the region in the box ``low``..``high`` (inclusive), as a one-row frame.

    A box over ``max_voxels`` is read every n-th voxel along each axis, so a whole-cell mask never has to
    fit in memory; the spacing grows to match and the estimate is of the coarser region.
    """
    step = max(1, int(np.ceil((np.prod(high - low + 1) / max_voxels) ** (1 / len(low)))))
    return dask.delayed(_crofton)(box(data, low, high, step=step), label, tuple(s * step for s in spacing_um))
