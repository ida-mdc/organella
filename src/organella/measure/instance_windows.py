"""Instances measured in a window of the object each, instead of in the object whole.

The dense measurers hold every structure at once: a cell's distance maps, its foreground index and the ITK pass
over all its labels are each as large as the cell. An instance needs a box around itself. Two steps:

- :func:`instance_boxes` finds, in one pass over a structure in blocks, the box and the voxel count of every
  instance. Nothing larger than a block is in memory.
- :func:`instance_shape` measures one instance in its own box and a margin, with the same ITK filter the dense
  path uses, so the numbers are the dense ones.

A measurement of an instance depends only on that instance's voxels and the margin around them, which is why a
window gives the dense answer: the tests hold the two equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, Sequence, Tuple

import numpy as np
from scipy import ndimage

from organella.analysis.distances import distance_target, distance_transform_um
from organella.analysis.shapes import label_metrics
from organella.measure.source import ObjectSource

Box = Tuple[slice, ...]

# Past this a label id is not an index into a list: ndimage.find_objects makes one entry per id up to the largest.
_DENSE_IDS = 1 << 20


@dataclass(frozen=True)
class InstanceBox:
    """One instance: its id, the box it fills in the object, and how many voxels it has."""

    label: int
    box: Box
    voxels: int


def instance_boxes(source: ObjectSource, name: str, block: int = 256) -> Dict[int, InstanceBox]:
    """The box and voxel count of every instance of a structure, found a block at a time.

    ``block`` is the edge of the cube read at once. An instance that crosses blocks is the union of its parts.
    """
    shape = source.shape
    lo: Dict[int, list] = {}
    hi: Dict[int, list] = {}
    count: Dict[int, int] = {}
    for corner in product(*(range(0, n, block) for n in shape)):
        window = tuple(slice(c, min(c + block, n)) for c, n in zip(corner, shape))
        part = source.read(name, window)
        for label, found, voxels in _objects_in(part):
            starts = [s.start + c for s, c in zip(found, corner)]
            stops = [s.stop + c for s, c in zip(found, corner)]
            if label in count:
                lo[label] = [min(a, b) for a, b in zip(lo[label], starts)]
                hi[label] = [max(a, b) for a, b in zip(hi[label], stops)]
                count[label] += voxels
            else:
                lo[label], hi[label], count[label] = starts, stops, voxels
    return {label: InstanceBox(label, tuple(slice(a, b) for a, b in zip(lo[label], hi[label])), count[label])
            for label in sorted(count)}


def _objects_in(part: np.ndarray):
    """(label, tight box, voxel count) of every nonzero id in an array."""
    if not part.any():
        return
    if int(part.max()) <= _DENSE_IDS:
        dense, ids = part.astype(np.int32, copy=False), None
    else:
        ids, inverse = np.unique(part, return_inverse=True)
        dense = inverse.reshape(part.shape).astype(np.int32)
        if ids[0] != 0:                        # no background in this block: shift so that index 0 is background
            dense = dense + 1
            ids = np.concatenate([[0], ids])
    counts = np.bincount(dense.ravel())
    for index, found in enumerate(ndimage.find_objects(dense), start=1):
        if found is None:
            continue
        label = int(ids[index]) if ids is not None else index
        yield label, found, int(counts[index])


def expanded(box: Box, margin: "int | Sequence[int]", shape: Sequence[int]) -> Box:
    """``box`` grown by ``margin`` voxels on every side, kept inside an object of ``shape``.

    One margin, or one per axis: a distance in µm is a different number of voxels along each.
    """
    margins = (margin,) * len(box) if isinstance(margin, int) else tuple(margin)
    return tuple(slice(max(0, s.start - m), min(n, s.stop + m)) for s, m, n in zip(box, margins, shape))


def instance_shape(source: ObjectSource, name: str, instance: InstanceBox, margin: int = 1) -> Dict[str, Any]:
    """Size and shape of one instance, as ``label_metrics`` has it for the whole object.

    The centroid and the ellipsoid come back in the object's own frame, not the window's.
    """
    window = expanded(instance.box, margin, source.shape)
    part = source.read(name, window) == instance.label
    measured = label_metrics(part.astype(np.uint8), source.sample_size).get(1)
    if measured is None:
        return {}
    offset = np.array([s.start for s in window], dtype=float) * np.array(source.sample_size)
    out = dict(measured)
    out["centroid_um"] = tuple(float(c + o) for c, o in zip(measured["centroid_um"], offset))
    out["centroid_xyz_um"] = tuple(float(c) for c in reversed(out["centroid_um"]))
    return out


def margin_voxels(reach_um: float, sample_size: Sequence[float]) -> Tuple[int, ...]:
    """How many voxels along each axis make ``reach_um``, and one more so the nearest voxel is inside the window."""
    return tuple(int(np.ceil(reach_um / float(size))) + 1 for size in sample_size)


def contact_area_um2(instance: np.ndarray, distance: np.ndarray, sample_size: Sequence[float],
                     threshold_um: float) -> float:
    """The surface of an instance that lies within ``threshold_um`` of a target, in µm².

    The faces of the instance's boundary voxels, counting a voxel's exposed faces when its own distance to the
    target is at most the threshold. That is the layer the liver paper's membrane contacts take (the outer
    voxels of a mitochondrion within two voxels of the ER), with the area of the faces in place of a voxel count.
    ``instance`` is a boolean array.
    """
    # Padded, so that an instance on the edge of the window has background beside it and nothing wraps round.
    inside = np.pad(instance, 1)
    near = np.pad(instance & (distance <= threshold_um), 1)
    voxel = float(np.prod(sample_size))
    area = 0.0
    for axis, size in enumerate(sample_size):
        face = voxel / float(size)                    # the face a step along this axis crosses
        for step in (1, -1):
            area += float((near & ~np.roll(inside, step, axis=axis)).sum()) * face
    return area


def instance_distances(source: ObjectSource, name: str, instance: InstanceBox, reach_um: float,
                       contact_um: Sequence[float] = ()) -> Dict[str, Dict[str, Any]]:
    """How near an instance gets to every other structure, and how much of it is that near, in its own window.

    For each other structure: the smallest distance from the instance to it, exact where it is at most
    ``reach_um`` and ``inf`` where it is farther (the window does not see further), and the area of the
    instance within each of ``contact_um`` of it. The target of a structure is the union of its instances; the
    target of the object's boundary is the outside, so that distance is the depth under the surface.
    """
    window = expanded(instance.box, margin_voxels(reach_um, source.sample_size), source.shape)
    volumes = source.window(window)
    own = volumes[name] == instance.label
    out: Dict[str, Dict[str, Any]] = {}
    for target in source.names:
        if target == name:
            continue
        distance = distance_transform_um(
            distance_target(volumes[target], target, source.kinds[target], source.object_mask_name),
            source.sample_size, num_threads=1)
        nearest = float(distance[own].min())
        found: Dict[str, Any] = {"distance_um": nearest if nearest <= reach_um else float("inf")}
        for threshold in contact_um:
            found[f"contact_um2_{threshold:g}"] = contact_area_um2(own, distance, source.sample_size, threshold)
        out[target] = found
    return out
