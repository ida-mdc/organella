"""Direction and distance from the object centre to points, and how elongated an instance is."""
from __future__ import annotations

import dask
import dask.array as da
import numpy as np
from dask.delayed import Delayed

from organella.measure.windows import box

# Below this share of the longest axis' variance an axis is rounding noise, not a shape.
DEGENERATE = 1e-9


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
