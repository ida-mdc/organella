"""Surface-to-surface gaps between the instances of one structure.

Only between instances of the *same* structure, and only for label entities. Two reasons,
and they are the same reason: a contact is a statement about two things of one kind - two
mitochondria touching is a mitochondrial network, where a mitochondrion touching the
nucleus is a *distance to the nucleus*, which is what the distance rows are for. And a mask
is one whole structure rather than a set of instances, so it has no pairs of its own to
report; how close an instance gets to it is again a distance.

That also makes this cheap: each structure is measured in its own combined volume, so the
work is per structure rather than over every pair of structures at once.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Sequence, Tuple

import edt
import numpy as np
from scipy.ndimage import find_objects

# One contact: (entity, label_a, label_b, gap_um) - both instances are of the same entity.
Contact = Tuple[str, int, int, float]

LABEL = "label"


def pairwise_instance_gaps(
    volumes: Mapping[str, np.ndarray],
    kinds: Mapping[str, str],
    sample_size: Sequence[float],
    max_gap_um: float,
) -> List[Contact]:
    """Gaps between instances of one structure, up to ``max_gap_um`` µm.

    The gap between two instances is the smallest distance from one's voxels to the nearest
    voxel of the other. It is measured in whole voxel steps, so instances sharing a face read
    one voxel step rather than zero, and n empty voxels between them read n+1 steps. With
    anisotropic voxels the smallest reportable gap therefore depends on direction (in
    0.1×0.02×0.02 µm data, touching along Z reads 0.1 µm and along X reads 0.02 µm).

    Returned sorted, so a report is reproducible.
    """
    contacts: List[Contact] = []
    for name, volume in volumes.items():
        if kinds[name] != LABEL:
            continue
        contacts.extend(
            (name, low, high, gap)
            for (low, high), gap in _gaps_within(volume, sample_size, max_gap_um).items()
        )
    contacts.sort()
    return contacts


def _gaps_within(
    labels: np.ndarray, sample_size: Sequence[float], max_gap_um: float,
) -> Dict[Tuple[int, int], float]:
    """The closest approach of every pair of instances in one label volume.

    Local EDT per instance: within its own bounding box padded by the threshold, measure
    every other instance's samples to this one, and the minimum is that pair's gap. Bounded
    windows, so this stays affordable with thousands of instances.
    """
    if not labels.any():
        return {}
    anisotropy = tuple(float(v) for v in sample_size)
    # Bounding-box padding per axis, so everything within max_gap is inside the window. One
    # entry per spatial axis, which is the only difference between 2D and 3D here.
    reach = [int(math.ceil(max_gap_um / v)) for v in anisotropy]
    boxes = find_objects(labels.astype(np.int32, copy=False))

    closest: Dict[Tuple[int, int], float] = {}
    for label_id, box in enumerate(boxes, start=1):
        if box is None:
            continue
        window = tuple(
            slice(max(0, axis.start - pad), min(extent, axis.stop + pad))
            for axis, pad, extent in zip(box, reach, labels.shape)
        )
        near = labels[window]
        others = (near > 0) & (near != label_id)
        if not others.any():
            continue
        # Distance from every sample to the nearest sample of this instance, which is where
        # the zeros are.
        to_this_one = edt.edt(np.ascontiguousarray((near != label_id).astype(np.uint8)),
                              anisotropy=anisotropy, parallel=1)
        for other_id, gap in _nearest_per_instance(near[others], to_this_one[others]):
            if gap > max_gap_um:
                continue
            pair = (label_id, other_id) if label_id < other_id else (other_id, label_id)
            if pair not in closest or gap < closest[pair]:
                closest[pair] = float(gap)
    return closest


def _nearest_per_instance(ids: np.ndarray, distances: np.ndarray):
    """(instance, smallest distance) for each instance among the samples given."""
    order = np.argsort(ids, kind="stable")
    ids, distances = ids[order], distances[order]
    unique, first = np.unique(ids, return_index=True)
    return zip(unique.tolist(), np.minimum.reduceat(distances, first).tolist())
