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


def expanded(box: Box, margin: int, shape: Sequence[int]) -> Box:
    """``box`` grown by ``margin`` voxels on every side, kept inside an object of ``shape``."""
    return tuple(slice(max(0, s.start - margin), min(n, s.stop + margin)) for s, n in zip(box, shape))


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
