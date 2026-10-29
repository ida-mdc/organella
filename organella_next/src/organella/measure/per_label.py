"""Per-instance statistics of chunked label arrays.

Each chunk reports only the instances it holds, with scipy.ndimage, and the reports are merged a few at a
time in a tree. An instance that spans chunks gets the in-memory answer, and no task holds every chunk's report.
"""
from __future__ import annotations

from itertools import product

import dask
import dask.array as da
import numpy as np
import polars as pl
from dask.delayed import Delayed
from scipy import ndimage

REPORTS_PER_MERGE = 8


def chunks_with_origin(labels: da.Array, *fields: da.Array):
    """Each chunk of ``labels`` with its origin in voxels and the matching chunks of ``fields``."""
    starts = [np.cumsum((0, *sizes[:-1])) for sizes in labels.chunks]
    delayed = [labels.to_delayed()] + [f.to_delayed() for f in fields]
    for index in product(*(range(n) for n in labels.numblocks)):
        origin = np.array([starts[axis][i] for axis, i in enumerate(index)])
        yield origin, [d[index] for d in delayed]


def tree_merged(reports: list[Delayed], merge) -> Delayed:
    while len(reports) > 1:
        reports = [dask.delayed(merge)(reports[i:i + REPORTS_PER_MERGE])
                   for i in range(0, len(reports), REPORTS_PER_MERGE)]
    return reports[0]


def _ids_as_int64(ids: np.ndarray) -> np.ndarray:
    if ids.size and ids.max() > np.iinfo(np.int64).max:
        raise ValueError(f"label id {ids.max()} does not fit int64, which the report stores")
    return ids.astype(np.int64)


def _pairs(ndim: int) -> list[tuple[int, int]]:
    return [(i, j) for i in range(ndim) for j in range(i, ndim)]


def _count_and_sums(chunk: np.ndarray, origin: np.ndarray) -> pl.DataFrame:
    """Per instance in this chunk: voxel count, sums of each coordinate, sums of each coordinate product."""
    where = np.nonzero(chunk)
    present, inverse = np.unique(chunk[where], return_inverse=True)
    coords = [c.astype(np.float64) + o for c, o in zip(where, origin)]
    sums = {f"s{i}": np.bincount(inverse, weights=c, minlength=present.size).astype(np.float64)
            for i, c in enumerate(coords)}
    cross = {f"m{i}{j}": np.bincount(inverse, weights=coords[i] * coords[j], minlength=present.size).astype(np.float64)
             for i, j in _pairs(chunk.ndim)}
    corners = pl.DataFrame({"id": inverse, **{f"c{i}": c for i, c in enumerate(coords)}}).group_by("id").agg(
        *(pl.col(f"c{i}").min().alias(f"lo{i}") for i in range(chunk.ndim)),
        *(pl.col(f"c{i}").max().alias(f"hi{i}") for i in range(chunk.ndim)),
    ).sort("id").drop("id")
    return pl.DataFrame({"id": present, "count": np.bincount(inverse, minlength=present.size).astype(np.int64),
                         **sums, **cross}).hstack(corners)


def summed(reports: list[pl.DataFrame]) -> pl.DataFrame:
    """Counts and moments add; a bounding box takes the smallest low corner and the largest high corner."""
    columns = [c for c in reports[0].columns if c != "id"]
    return pl.concat(reports).group_by("id").agg(
        *(pl.col(c).min() if c.startswith("lo") else pl.col(c).max() if c.startswith("hi") else pl.col(c).sum()
          for c in columns))


def _counted(total: pl.DataFrame) -> pl.DataFrame:
    total = total.sort("id")
    n = np.maximum(total["count"].to_numpy(), 1)
    ndim = sum(c.startswith("s") for c in total.columns)
    centre = [total[f"s{i}"].to_numpy() / n for i in range(ndim)]
    return pl.DataFrame({
        "id": _ids_as_int64(total["id"].to_numpy()),
        "count": total["count"],
        **{f"c{i}": c for i, c in enumerate(centre)},
        **{f"v{i}{j}": total[f"m{i}{j}"].to_numpy() / n - centre[i] * centre[j] for i, j in _pairs(ndim)},
        **{f"{side}{i}": total[f"{side}{i}"].to_numpy().astype(np.int64) for side in ("lo", "hi") for i in range(ndim)},
    })


def counts_and_centroids(labels: da.Array) -> Delayed:
    """A frame of ``id``, ``count``, the centroid ``c0``, ``c1``..., the covariance ``v00``, ``v01``... of each
    instance's voxel coordinates, and its bounding box ``lo0``..``hi0``.. (inclusive), ids ascending."""
    reports = [dask.delayed(_count_and_sums)(chunk, origin) for origin, (chunk,) in chunks_with_origin(labels)]
    return dask.delayed(_counted)(tree_merged(reports, summed))


def minimum_in_chunk(chunk: np.ndarray, field: np.ndarray) -> pl.DataFrame:
    present = np.unique(chunk)
    present = present[present > 0]
    found = ndimage.minimum(field, chunk, present).astype(field.dtype) if present.size else np.empty(0, field.dtype)
    return pl.DataFrame({"id": present, "min": found})


def smallest(reports: list[pl.DataFrame]) -> pl.DataFrame:
    return pl.concat(reports).group_by("id").min()


def minimised(total: pl.DataFrame) -> pl.DataFrame:
    total = total.sort("id")
    return pl.DataFrame({"id": _ids_as_int64(total["id"].to_numpy()), "min": total["min"].to_numpy()})


def ends_in_chunk(field: np.ndarray, origin: np.ndarray, points: np.ndarray, owners: np.ndarray) -> pl.DataFrame:
    inside = np.all((points >= origin) & (points < origin + field.shape), axis=1)
    local = (points[inside] - origin).T
    return pl.DataFrame({"id": owners[inside], "end_min": field[tuple(local)].astype(np.float64)}).with_columns(
        end_max=pl.col("end_min"))


def extremes(reports: list[pl.DataFrame]) -> pl.DataFrame:
    return pl.concat(reports).group_by("id").agg(pl.col("end_min").min(), pl.col("end_max").max())


def histogram_in_chunk(chunk: np.ndarray, field: np.ndarray, bins: int, upper: float) -> pl.DataFrame:
    foreground = chunk > 0
    present, inverse = np.unique(chunk[foreground], return_inverse=True)
    values = field[foreground].astype(np.float64)
    which = np.minimum((values / upper * bins).astype(np.int64), bins - 1)   # past the range reads as the last bin
    counts = np.bincount(inverse * bins + which, minlength=present.size * bins).reshape(-1, bins)
    return pl.DataFrame({"id": present, "total": np.bincount(inverse, weights=values, minlength=present.size).astype(np.float64),
                         **{f"h{b}": counts[:, b].astype(np.int64) for b in range(bins)}})


def histograms_of(total: pl.DataFrame) -> pl.DataFrame:
    total = total.sort("id")
    return pl.DataFrame({"id": _ids_as_int64(total["id"].to_numpy()), "total": total["total"],
                         "counts": total.select(pl.col(c) for c in total.columns if c.startswith("h")).to_numpy().tolist()})
