"""Voxel size from an image file, whatever its format."""
from __future__ import annotations

from pathlib import Path

import SimpleITK as sitk

from organella.core import VoxelSize
from organella.sources.arrays import TIFF_SUFFIXES, image_suffix
from organella.sources.tiff_metadata import voxel_size_from_tiff

# NIfTI spacing is millimetres unless it says otherwise, and its unit field is very often 0 ("unknown"), as in
# TotalSegmentator. Assuming mm is what every reader of these does; --voxel-size-um overrides it.
_MM_TO_UM = 1000.0


def voxel_size_from_file(path: Path) -> VoxelSize | None:
    if image_suffix(path) in TIFF_SUFFIXES:
        return voxel_size_from_tiff(path)
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.ReadImageInformation()
    spacing = tuple(s * _MM_TO_UM for s in reversed(reader.GetSpacing()))     # (x, y, z) to (z, y, x)
    return VoxelSize(spacing[1:] if tuple(reader.GetSize()[2:]) == (1,) else spacing)
