"""What the size and shape columns are called: a volume and its surface, or a plane and its outline."""
from __future__ import annotations


def size(ndim: int) -> str:
    return "area_um2" if ndim == 2 else "volume_um3"


def total_size(ndim: int) -> str:
    return "total_area_um2" if ndim == 2 else "total_volume_um3"


def object_size(ndim: int) -> str:
    return "object_area_um2" if ndim == 2 else "object_volume_um3"


def segmented_size(ndim: int) -> str:
    """The extent covered by at least one structure: the union, where the structures' totals count an overlap once each."""
    return "segmented_area_um2" if ndim == 2 else "segmented_volume_um3"


def boundary(ndim: int) -> str:
    return "perimeter_um" if ndim == 2 else "surface_area_um2"


def roundness(ndim: int) -> str:
    return "circularity" if ndim == 2 else "sphericity"
