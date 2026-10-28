"""Surface-to-surface gaps between the instances of one structure.

Only between instances of the *same* structure, and only for label entities: two
mitochondria touching is a mitochondrial network, where a mitochondrion touching the nucleus
is a *distance to the nucleus*, which the distance rows carry. A mask is one whole structure,
not a set of instances, so it has no pairs of its own either.

That also makes this cheap: each structure is measured in its own combined volume, so the
work is per structure and never spans every pair of them at once.
"""

from __future__ import annotations

import math
import os
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
    num_threads: int = 0,
) -> List[Contact]:
    """Gaps between instances of one structure, up to ``max_gap_um`` µm.

    The gap is the smallest distance from one instance's voxels to the other's, in whole
    voxel steps: instances sharing a face read one step, not zero, and n empty voxels
    between them read n+1. So the smallest reportable gap depends on direction - in
    0.1×0.02×0.02 µm data, touching along Z reads 0.1 µm and along X 0.02 µm.

    ``num_threads`` is handed to each local transform, 0 meaning every core. It changes
    nothing about the answer, only how long it takes to get: this is the one place in a run
    that walks *every* instance, so on a cell of 13,812 granules it is 14 minutes on one
    core and 3 on eight.

    Returned sorted, so a report is reproducible.
    """
    contacts: List[Contact] = []
    for name, volume in volumes.items():
        if kinds[name] != LABEL:
            continue
        contacts.extend(
            (name, low, high, gap)
            for (low, high), gap in
            _gaps_within(volume, sample_size, max_gap_um, num_threads).items()
        )
    contacts.sort()
    return contacts


def _gaps_within(
    labels: np.ndarray,
    sample_size: Sequence[float],
    max_gap_um: float,
    num_threads: int = 0,
) -> Dict[Tuple[int, int], float]:
    """The closest approach of every pair of instances in one label volume.

    Local EDT per instance: within its own bounding box padded by the threshold, measure
    every other instance's samples to this one, and the minimum is that pair's gap. Bounded
    windows, so this stays affordable with thousands of instances.

    The windows are what makes this the longest serial stretch of a large object, and they
    are also why it is threaded *inside* each transform and not across them: edt holds
    the GIL, so a thread pool over the instances buys nothing (measured: 0.96x on four
    threads) where edt's own threads buy 4.8x on the same window.
    """
    if not labels.any():
        return {}
    anisotropy = tuple(float(v) for v in sample_size)
    threads = num_threads if num_threads and num_threads > 0 else (os.cpu_count() or 1)
    # Bounding-box padding per axis, so everything within max_gap is inside the window.
    reach = reach_voxels(max_gap_um, anisotropy)
    # Not cast to int32 first: find_objects takes any integer type, and the cast was a full
    # second copy of the largest array this measurer holds - and contacts are where a run
    # peaks, which is what sizes the object pool.
    boxes = find_objects(labels)

    closest: Dict[Tuple[int, int], float] = {}
    for label_id, box in enumerate(boxes, start=1):
        if box is None:
            continue
        window = tuple(
            slice(max(0, axis.start - pad), min(extent, axis.stop + pad))
            for axis, pad, extent in zip(box, reach, labels.shape)
        )
        gaps_around(labels[window], label_id, anisotropy, threads, max_gap_um, closest)
    return closest


def reach_voxels(max_gap_um: float, sample_size: Sequence[float]) -> List[int]:
    """How many voxels along each axis make ``max_gap_um``, rounded up: the padding of a window that sees every
    instance close enough to be in contact with the one inside it."""
    return [int(math.ceil(max_gap_um / float(v))) for v in sample_size]


def gaps_around(near: np.ndarray, label_id: int, anisotropy: Sequence[float], threads: int,
                 max_gap_um: float, closest: Dict[Tuple[int, int], float]) -> None:
    """Fold the gaps from one instance to its neighbours into ``closest``.

    ``near`` is a window around the instance ``label_id`` that reaches ``max_gap_um`` past it on every
    side; what it holds of any other instance is what that instance can be in contact with it by.
    """
    others = (near > 0) & (near != label_id)
    if not others.any():
        return
    # Distance from every sample to this instance, whose own voxels read zero.
    to_this_one = edt.edt(np.ascontiguousarray((near != label_id).astype(np.uint8)),
                          anisotropy=anisotropy, parallel=threads)
    for other_id, gap in _nearest_per_instance(near[others], to_this_one[others]):
        if gap > max_gap_um:
            continue
        pair = (label_id, other_id) if label_id < other_id else (other_id, label_id)
        if pair not in closest or gap < closest[pair]:
            closest[pair] = float(gap)


def _nearest_per_instance(ids: np.ndarray, distances: np.ndarray):
    """(instance, smallest distance) for each instance among the samples given."""
    order = np.argsort(ids, kind="stable")
    ids, distances = ids[order], distances[order]
    unique, first = np.unique(ids, return_index=True)
    return zip(unique.tolist(), np.minimum.reduceat(distances, first).tolist())
