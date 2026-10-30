"""Boundary (surface area, or perimeter of a plane) and roundness (sphericity, or circularity) of instances, by ITK's Crofton estimate."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
import polars as pl
import SimpleITK as sitk
from dask.delayed import Delayed

from organella.app.settings import Settings
from organella.core import Kind, Sample
from organella.measure.windows import box, over_instances


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


def lazy_boundary(data: da.Array, low: np.ndarray, high: np.ndarray, label: int | None,
                 spacing_um: tuple[float, ...], max_voxels: int) -> Delayed:
    """The estimate for the region in the box ``low``..``high`` (inclusive), as a one-row frame.

    A box over ``max_voxels`` is read every n-th voxel along each axis, so a whole-cell mask never has to
    fit in memory; the spacing grows to match and the estimate is of the coarser region.
    """
    step = max(1, int(np.ceil((np.prod(high - low + 1) / max_voxels) ** (1 / len(low)))))
    return dask.delayed(_crofton)(box(data, low, high, step=step), label, tuple(s * step for s in spacing_um))


def boundaries(sample: Sample, per_structure: dict[str, pl.DataFrame], settings: Settings) -> dict[str, pl.DataFrame]:
    """Boundary and roundness of each instance of a labelled structure, and of each mask as a whole."""
    spacing, found = tuple(sample.voxel_size.um), {}
    for s in sample.structures.values():
        stats = per_structure[s.name]
        if not stats.height or (s.kind is Kind.LABEL and not settings.instances):
            continue
        data, labelled = (s.data, True) if s.kind is Kind.LABEL else (s.foreground, False)
        found[s.name] = pl.concat(over_instances(
            data, stats, lambda d, low, high, label: lazy_boundary(d, low, high, label if labelled else None, spacing, settings.coarse_voxels),
            settings.surface_batch))
    return found
