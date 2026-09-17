"""Measuring each object's expensive intermediates once.

Every function here is ``<what>_for(object_id, ...)``: it answers from the cache if this
object has been asked for that before, and computes and stores it if not.

Skeletonising is the most expensive measurement here, 103 s of a 2-minute object; which
entities are worth it at all is ``--skeletons``, in :mod:`organella.config`.

The same skeletons feed the table metrics and the overlay geometry. Measurers pass nothing
to each other, but they run one after another in one process per object, so a cache holding
that object's skeletons turns the second computation into a lookup.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from organella.analysis.shapes import compute_skeletons
from organella.config import normalize_name

logger = logging.getLogger(__name__)

class _PerObjectCache:
    """One object's expensive intermediates, keyed however the caller asks.

    Validity is the object id *and* the identity of the array the values came from: folder
    names are not unique across groups, and answering on the name alone would hand one
    object another's results. The array is held weakly, so the cache costs no memory beyond
    the values.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._object_id: Optional[str] = None
        self._owner: Optional[weakref.ref] = None
        self._by_entity: Dict[Tuple[Any, ...], Any] = {}

    def get_or_compute(self, object_id: str, key: Tuple[Any, ...], array: np.ndarray, factory) -> Any:
        """The cached value for (object, key), or ``factory()`` stored under it."""
        owner = array.base if array.base is not None else array
        with self._lock:
            same_object = self._object_id == object_id and (
                self._owner is not None and self._owner() is owner
            )
            if not same_object:
                # A different object, or the same name over different data: drop the
                # previous entry rather than answering from it.
                self._object_id, self._by_entity = object_id, {}
                try:
                    self._owner = weakref.ref(owner)
                except TypeError:      # not weak-referenceable: no caching, still correct
                    self._owner = None
            cached = self._by_entity.get(key)
        if cached is not None:
            logger.debug("organella: reusing %s of %s", key, object_id)
            return cached

        computed = factory()
        with self._lock:
            if self._object_id == object_id and self._owner is not None and self._owner() is owner:
                self._by_entity[key] = computed
        return computed

    def clear(self) -> None:
        with self._lock:
            self._object_id, self._owner, self._by_entity = None, None, {}


# Process-local: each Dask worker handles one record at a time, so this holds the object
# currently being processed and nothing else.
CACHE = _PerObjectCache()


def skeletons_for(
    object_id: str,
    entity: str,
    labels: np.ndarray,
    sample_size: Sequence[float],
    max_voxels: Optional[int],
) -> dict:
    """This entity's skeletons, computed once per object however many readers ask.

    TEASAR for a volume, a thinned medial axis for a plane; both answer branches, length
    and tortuosity, and share one cache.
    """
    return CACHE.get_or_compute(
        object_id, ("skeletons", entity, max_voxels), labels,
        lambda: compute_skeletons(
            labels, tuple(float(v) for v in sample_size), max_voxels=max_voxels,
        ),
    )


def label_metrics_for(object_id: str, entity: str, labels: np.ndarray,
                      sample_size: Sequence[float]) -> Dict[int, Dict[str, Any]]:
    """ITK's shape statistics for every instance of an entity, once per object.

    A --with-mesh run has two readers: the instance table, and the geometry rows that carry
    the same numbers beside each mesh. It is a full pass over the entity either time.
    """
    from organella.analysis.shapes import label_metrics

    return CACHE.get_or_compute(
        object_id, ("label_metrics", normalize_name(entity)), labels,
        lambda: label_metrics(labels, sample_size),
    )


def region_metrics_for(object_id: str, entity: str, volume: np.ndarray,
                       sample_size: Sequence[float]) -> Dict[str, float]:
    """Shape statistics for a whole-structure mask, once per object and entity.

    The same two readers the label entities have, and for the object mask - the largest
    structure there is - the most expensive measurement in the object.

    Keyed on the volume, not the boolean derived from it: ``volume > 0`` is a fresh array
    every call, which would not merely miss but read as a different object and evict
    everything stored for this one.
    """
    from organella.analysis.shapes import region_metrics

    return CACHE.get_or_compute(
        object_id, ("region_metrics", normalize_name(entity)), volume,
        lambda: region_metrics(volume > 0, sample_size),
    )


def regions_for(object_id: str, entity: str, labels: np.ndarray):
    """``regionprops`` for an entity, once per object.

    The same two readers and the same full find_objects pass. Sharing them shares the
    properties they compute lazily, so an area read for the instance table is not measured
    again for the geometry.
    """
    from skimage.measure import regionprops

    return CACHE.get_or_compute(
        object_id, ("regions", normalize_name(entity)), labels,
        lambda: regionprops(labels),
    )


def contacts_for(object_id: str, volumes, kinds, voxel_size_zyx, max_gap_um: float,
                 num_threads: int = 0):
    """This object's contacts, computed once whether the report or the geometry file asks.

    Both want the same pairs at the same threshold, and finding them is seconds to minutes
    depending on the instance count.

    ``num_threads`` is not part of the key: it decides how fast the pairs are found and
    nothing about which pairs they are, so a cached answer is as good whatever it was found
    with.
    """
    from organella.analysis.gaps import pairwise_instance_gaps

    any_view = next(iter(volumes.values()))
    return CACHE.get_or_compute(
        object_id, ("contacts", round(float(max_gap_um), 6)), any_view,
        lambda: pairwise_instance_gaps(volumes, kinds, voxel_size_zyx, max_gap_um,
                                       num_threads),
    )
