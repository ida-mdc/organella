"""Direction and distance from the object centre to points, and how elongated an instance is."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
import polars as pl
from dask.delayed import Delayed

from organella.app.settings import Settings
from organella.core import Sample
from organella.measure.windows import box, over_instances

# Below this share of the longest axis' variance an axis is rounding noise, not a shape.
DEGENERATE = 1e-9

# The grid directions are counted on: a plane has this many angles; a volume has this many bins in z (an equal share of the
# sphere each) by this many in azimuth, so every bin spans the same solid angle.
PLANE_BINS = 360
VOLUME_BINS = 32


def direction_counts(offsets_um: np.ndarray) -> np.ndarray:
    """How many of the offsets ``(n, ndim)`` point into each bin of an equal-share grid of directions: ``(360,)`` for a
    plane, ``(32, 32)`` (z, azimuth) for a volume. An offset of 0 has no direction and is not counted."""
    dist = np.linalg.norm(offsets_um, axis=1)
    units = offsets_um[dist > 0] / dist[dist > 0, None]
    if offsets_um.shape[1] == 2:
        angle = np.arctan2(units[:, 0], units[:, 1])
        bins = np.clip(((angle + np.pi) / (2 * np.pi) * PLANE_BINS).astype(int), 0, PLANE_BINS - 1)
        return np.bincount(bins, minlength=PLANE_BINS)
    z = np.clip(((units[:, 0] + 1) / 2 * VOLUME_BINS).astype(int), 0, VOLUME_BINS - 1)
    azimuth = np.clip(((np.arctan2(units[:, 1], units[:, 2]) + np.pi) / (2 * np.pi) * VOLUME_BINS).astype(int), 0, VOLUME_BINS - 1)
    return np.bincount(z * VOLUME_BINS + azimuth, minlength=VOLUME_BINS ** 2).reshape(VOLUME_BINS, VOLUME_BINS)


def _block_directions(block: np.ndarray, origin: np.ndarray, voxel_um: np.ndarray, centre_um: np.ndarray) -> np.ndarray:
    return direction_counts((np.argwhere(block) + origin) * voxel_um - centre_um)


def direction_profile(region: da.Array, voxel_um: np.ndarray, centre_um: np.ndarray) -> Delayed:
    """Which directions the object's own voxels lie in as seen from its centre: what a structure scattered anywhere in the
    object would be spread over, so an elongated object has more of it along its long axis than across."""
    starts = [np.concatenate([[0], np.cumsum(c)[:-1]]) for c in region.chunks]
    parts = [dask.delayed(_block_directions)(block, np.array([starts[axis][i] for axis, i in enumerate(index)]), voxel_um, centre_um)
             for index, block in np.ndenumerate(region.to_delayed())]
    return dask.delayed(sum)(parts)


def polarity(offsets_um: np.ndarray) -> dict[str, np.ndarray]:
    """From offsets ``(n, ndim)`` in array order: distance, unit vector, and azimuth and elevation (a plane: one angle).

    A point at the centre has a distance of 0 and no direction, so its direction columns are NaN, not 0°.
    """
    dist = np.linalg.norm(offsets_um, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        units = np.where(dist[:, None] > 0, offsets_um / dist[:, None], np.nan)
    if offsets_um.shape[1] == 2:
        ny, nx = units.T
        return {"polar_dist_um": dist, "polar_ny": ny, "polar_nx": nx,
                "polar_angle_deg": np.degrees(np.arctan2(ny, nx))}
    nz, ny, nx = units.T
    return {"polar_dist_um": dist, "polar_nz": nz, "polar_ny": ny, "polar_nx": nx,
            "polar_az_deg": np.degrees(np.arctan2(ny, nx)),
            "polar_el_deg": np.degrees(np.arcsin(np.clip(nz, -1.0, 1.0)))}


def spread_deg(coords_voxels: np.ndarray, voxel_um: np.ndarray, centre_um: np.ndarray) -> float:
    """How wide a range of directions an instance covers as seen from the object centre, in degrees: a compact granule
    reads near zero, a strand wrapping the object reads large. NaN for fewer than three voxels at distinct directions."""
    if len(coords_voxels) < 3:
        return float("nan")
    offsets = coords_voxels * voxel_um - centre_um
    radius = np.linalg.norm(offsets, axis=1)
    keep = radius > 0
    if keep.sum() < 3:
        return float("nan")
    unit = offsets[keep] / radius[keep, None]
    mean = unit.mean(axis=0)
    length = float(np.linalg.norm(mean))
    mean = mean / length if length > 0 else mean
    return float(np.degrees(np.arccos(np.clip(unit @ mean, -1.0, 1.0))).std())


def aspect_ratio(covariance_um: np.ndarray) -> np.ndarray:
    """Longest principal axis over shortest, from covariances ``(n, ndim, ndim)``; NaN for a degenerate shape."""
    eigenvalues = np.linalg.eigvalsh(covariance_um)
    shortest, longest = eigenvalues[:, 0], eigenvalues[:, -1]
    flat = shortest <= longest * DEGENERATE  # a point, a line or a plane has no shortest axis to divide by
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(flat, np.nan, np.sqrt(longest / shortest))


def _spread_of(window: np.ndarray, label: int, low: np.ndarray, voxel_um: np.ndarray, centre_um: np.ndarray) -> float:
    return spread_deg(np.argwhere(window == label) + low, voxel_um, centre_um)


def lazy_spread(data: da.Array, low: np.ndarray, high: np.ndarray, label: int, voxel_um: np.ndarray, centre_um: np.ndarray) -> Delayed:
    return dask.delayed(_spread_of)(box(data, low, high), label, low, voxel_um, centre_um)


def spreads(sample: Sample, per_structure: dict[str, pl.DataFrame], settings: Settings, centre: np.ndarray | None) -> dict[str, np.ndarray]:
    """The angular spread of each instance's voxels around the object centre, for the labelled structures; none unless asked."""
    if not (settings.polarity_spread and settings.instances and centre is not None):
        return {}
    voxel = np.array(sample.voxel_size.um)
    return {s.name: np.array(over_instances(s.data, per_structure[s.name],
                                            lambda data, low, high, label: lazy_spread(data, low, high, label, voxel, centre),
                                            settings.surface_batch), dtype=np.float64)
            for s in sample.labelled() if per_structure[s.name].height}
