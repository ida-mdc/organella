"""Exact Euclidean distance to a set of voxels."""
from __future__ import annotations

from typing import Sequence

import numpy as np
import SimpleITK as sitk


def distance_to_voxels(target: np.ndarray, spacing_um: Sequence[float]) -> np.ndarray:
    """µm from every voxel to the nearest True voxel of ``target``, float32; 0 on and inside the target.

    ITK's Maurer transform: exact, spacing-aware, and multi-threaded inside, where ``edt`` holds the GIL so that threads
    around it add nothing. The edge of the array is not a target. The array must have a target voxel in it.
    """
    image = sitk.GetImageFromArray(np.ascontiguousarray(target, dtype=np.uint8))
    image.SetSpacing(tuple(float(s) for s in reversed(spacing_um)))  # ITK orders x, y, z
    signed = sitk.SignedMaurerDistanceMap(image, insideIsPositive=False, squaredDistance=False, useImageSpacing=True)
    return np.maximum(sitk.GetArrayFromImage(signed), 0.0)
