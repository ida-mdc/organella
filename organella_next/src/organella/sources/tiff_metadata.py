from __future__ import annotations

from pathlib import Path

import tifffile

from organella.core import VoxelSize

_TO_UM = {"micron": 1.0, "um": 1.0, "µm": 1.0, "nm": 1e-3, "nanometer": 1e-3, "mm": 1e3}


def voxel_size_from_tiff(path: Path) -> VoxelSize | None:
    """Voxel size from ImageJ or resolution tags, as many axes as the image has, or None when it does not say."""
    with tifffile.TiffFile(path) as tif:
        ndim = len(tif.series[0].shape)
        meta = tif.imagej_metadata or {}
        tags = tif.pages[0].tags
        x, y = _resolution(tags.get("XResolution")), _resolution(tags.get("YResolution"))
        scale = _TO_UM.get(str(meta.get("unit", "")).lower())
        if scale is None or x is None or y is None:
            return None
        if ndim == 2:
            return VoxelSize((y * scale, x * scale))
        if ndim == 3 and meta.get("spacing") is not None:
            return VoxelSize((float(meta["spacing"]) * scale, y * scale, x * scale))
        return None


def _resolution(tag) -> float | None:
    if tag is None:
        return None
    numerator, denominator = tag.value
    return denominator / numerator if numerator else None
