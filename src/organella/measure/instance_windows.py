"""Instances measured in a window of the object each, instead of in the object whole.

The dense measurers hold every structure at once: a cell's distance maps, its foreground index and the ITK pass
over all its labels are each as large as the cell. An instance needs a box around itself. Three steps:

- :func:`instance_boxes` finds, in one pass over a structure in blocks, the box and the voxel count of every
  instance. Nothing larger than a block is in memory.
- :func:`instance_shape`, :func:`instance_distances` and the gap search measure one instance in its own box and a
  margin, with the same code the dense path uses, so the numbers are the dense ones.
- :func:`tiles` decides what is read: instances that sit near each other are measured from one read of the
  region around them, so a chunk of the store is read once for all of them and not once for each.

A measurement of an instance depends only on that instance's voxels and the margin around them, which is why a
window gives the dense answer: the tests hold the two equal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from organella.analysis.gaps import Contact, gaps_around, reach_voxels
from organella.analysis.distances import distance_target, distance_transform_um, polarity_from_offset
from organella.analysis.shapes import METRICS_3D, label_metrics
from organella.analysis.meshes import MeshOptions, contact_rows, instance_rows, mask_rows
from organella.analysis.parallel import WorkPool, worker_share
from organella.measure.source import ObjectSource, blocks
from organella.model import ObjectStack

logger = logging.getLogger(__name__)

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
    lo: Dict[int, list] = {}
    hi: Dict[int, list] = {}
    count: Dict[int, int] = {}
    cubes = list(blocks(source.shape, block))
    for (window, corner), part in zip(cubes, source.read_boxes(name, [box for box, _ in cubes])):
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


def instance_shape(source, name: str, instance: InstanceBox, margin: int = 1) -> Dict[str, Any]:
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


def instance_distances(reader, name: str, instance: InstanceBox, reach_um: float,
                       contact_um: Sequence[float] = ()) -> Dict[str, Dict[str, Any]]:
    """How near an instance gets to every other structure, and how much of it is that near, in its own window.

    For each other structure: the smallest distance from the instance to it, exact where it is at most
    ``reach_um`` and ``inf`` where it is farther (the window does not see further), and the area of the
    instance within each of ``contact_um`` of it. The target of a structure is the union of its instances; the
    target of the object's boundary is the outside, so that distance is the depth under the surface.

    ``reader`` is an ``ObjectSource`` or a tile of one. Structures are read one at a time, so an instance too
    large for a tile holds two windows and one distance transform, not every structure at once.
    """
    window = expanded(instance.box, margin_voxels(reach_um, reader.sample_size), reader.shape)
    own = reader.read(name, window) == instance.label
    out: Dict[str, Dict[str, Any]] = {}
    for target in reader.names:
        if target == name:
            continue
        distance = distance_transform_um(
            distance_target(reader.read(target, window), target, reader.kinds[target], reader.object_mask_name),
            reader.sample_size, num_threads=1)
        nearest = float(distance[own].min())
        found: Dict[str, Any] = {"distance_um": nearest if nearest <= reach_um else float("inf")}
        for threshold in contact_um:
            found[f"contact_um2_{threshold:g}"] = contact_area_um2(own, distance, reader.sample_size, threshold)
        out[target] = found
    return out


# ── what is read: tiles ──────────────────────────────────────────────────────

# The edge of the cell of the object an instance is assigned to by where it starts, and the largest a window may be
# (along any axis) and still be read with the others of its tile. A tile is at most TILE + SMALL voxels an edge,
# of every structure: with 8 structures that is under 0.5 GB at 256 + 128.
TILE = 128
SMALL = 256


class _Tile:
    """A region of the object read once, for the instances in it: what an ``ObjectSource`` is to a measurement of one."""

    def __init__(self, source: ObjectSource, box: Box) -> None:
        self._source = source
        self._box = box
        self._volumes = source.window(box)

    def read(self, name: str, box: Box) -> np.ndarray:
        """One structure in ``box``, which lies inside the tile, as the source would give it."""
        inside = tuple(slice(b.start - t.start, b.stop - t.start) for b, t in zip(box, self._box))
        if any(i.start < 0 or b.stop > t.stop for i, b, t in zip(inside, box, self._box)):
            raise ValueError(f"{box} is not inside the tile {self._box}")
        return self._volumes[name][inside]

    def __getattr__(self, attribute: str):
        return getattr(self._source, attribute)


def tiles(source: ObjectSource, boxes: Mapping[str, Mapping[int, InstanceBox]], margin: Sequence[int]):
    """Every instance, grouped by what to read for it: ``(region, [(structure, instance), ...])``.

    Instances whose windows (their box and ``margin``) are small are grouped by where they start, and ``region``
    is the box covering all their windows, in order, so neighbours share a read. One whose window is large is its
    own group, and its ``region`` is ``None``: it is read from the source itself, a structure at a time.
    """
    shape = source.shape
    groups: Dict[Tuple[int, ...], List[Tuple[str, InstanceBox, Box]]] = {}
    alone: List[Tuple[str, InstanceBox]] = []
    for name, instances in boxes.items():
        for instance in instances.values():
            window = expanded(instance.box, margin, shape)
            if all(w.stop - w.start <= SMALL for w in window):
                groups.setdefault(tuple(w.start // TILE for w in window), []).append((name, instance, window))
            else:
                alone.append((name, instance))
    for key in sorted(groups):
        members = groups[key]
        region = tuple(slice(min(w[axis].start for *_, w in members), max(w[axis].stop for *_, w in members))
                       for axis in range(len(shape)))
        yield region, [(name, instance) for name, instance, _ in members]
    for name, instance in alone:
        yield None, [(name, instance)]


def _reader(source: ObjectSource, region: Box | None):
    """What a measurement reads from: the tile of ``source`` over ``region``, or the source for ``None``."""
    return source if region is None else _Tile(source, region)


@dataclass
class Measured:
    """What measuring every instance in its window found: shape, distances and the gaps between instances."""

    stats: Dict[str, Dict[int, Dict[str, Any]]]
    distances: Dict[Tuple[str, int], Dict[str, Dict[str, Any]]]
    contacts: List[Contact]


# How many tiles are measured between two lines in the log.
_PROGRESS = 32

# The object a worker measures in. A process of the pool opens it once as it starts (the task then carries only the
# instances), and the process that measures in itself sets it directly.
_SOURCE: ObjectSource | None = None


def _use_source(source: ObjectSource) -> None:
    global _SOURCE
    _SOURCE = source


def _open_source(folder, config) -> None:
    _use_source(ObjectSource(folder, config))


def _measure_tile(task) -> list:
    """Measure the instances of one tile: ``(structure, label, shape, distances, gaps)`` for each of them."""
    region, members, reach_um, gap_um = task
    source = _SOURCE
    reader = _reader(source, region)
    anisotropy = tuple(float(v) for v in source.sample_size)
    gap_reach = reach_voxels(gap_um, anisotropy) if gap_um is not None else None
    found = []
    for name, instance in members:
        distances = instance_distances(reader, name, instance, reach_um) if reach_um is not None else None
        gaps: Dict[Tuple[int, int], float] = {}
        if gap_um is not None:
            near = reader.read(name, expanded(instance.box, gap_reach, source.shape))
            # One thread: the parallelism of a windowed run is across tiles, not inside a transform.
            gaps_around(near, instance.label, anisotropy, 1, gap_um, gaps)
        found.append((name, instance.label, instance_shape(reader, name, instance), distances, gaps))
    return found


def measure_instances(source: ObjectSource, boxes: Mapping[str, Mapping[int, InstanceBox]],
                      reach_um: float | None, gap_um: float | None, workers: int = 1) -> Measured:
    """Shape of every instance, and, unless ``None``, its distance to each other structure and its gap to neighbours.

    ``reach_um`` is how far a distance is exact (``instance_distances``) and ``gap_um`` the largest gap between two
    instances of a structure that is a contact. Each region of the object is read once for every instance in it,
    and ``workers`` processes measure regions side by side.
    """
    anisotropy = tuple(float(v) for v in source.sample_size)
    gap_reach = reach_voxels(gap_um, anisotropy) if gap_um is not None else [0] * len(anisotropy)
    reach = margin_voxels(reach_um, anisotropy) if reach_um is not None else (0,) * len(anisotropy)
    margin = tuple(max(1, a, b) for a, b in zip(reach, gap_reach))

    tasks = [(region, members, reach_um, gap_um) for region, members in tiles(source, boxes, margin)]
    _use_source(source)
    pool = WorkPool(workers, total=len(tasks), what="measuring instances",
                    initializer=_open_source, initargs=(source.folder, source.config))
    results = []
    try:
        for start in range(0, len(tasks), _PROGRESS):
            results.extend(pool.map(_measure_tile, tasks[start:start + _PROGRESS], total=len(tasks)))
            logger.info("organella: %s: %d of %d regions measured", source.object_id,
                        min(start + _PROGRESS, len(tasks)), len(tasks))
    finally:
        pool.shutdown()

    stats: Dict[str, Dict[int, Dict[str, Any]]] = {name: {} for name in boxes}
    distances: Dict[Tuple[str, int], Dict[str, Dict[str, Any]]] = {}
    closest: Dict[str, Dict[Tuple[int, int], float]] = {name: {} for name in boxes}
    for found in results:
        for name, label, shape, nearest, gaps in found:
            stats[name][label] = shape
            if nearest is not None:
                distances[(name, label)] = nearest
            for pair, gap in gaps.items():
                if pair not in closest[name] or gap < closest[name][pair]:
                    closest[name][pair] = gap
    contacts = sorted((name, low, high, gap) for name, pairs in closest.items() for (low, high), gap in pairs.items())
    return Measured({name: dict(sorted(found.items())) for name, found in stats.items()}, distances, contacts)


def instance_tables(source: ObjectSource, boxes: Mapping[str, Mapping[int, InstanceBox]], measured: Measured,
                    polarity_spread: bool = False):
    """The instance rows and the distance rows of ``InstanceMeasurer``, built from what was measured in windows.

    ``boxes`` are the instances of each label structure, as ``instance_boxes`` found them, and ``measured`` what
    ``measure_instances`` found of them, distances included. Returns ``(instances, distances)``, dicts of column
    lists like the dense measurer's. Not what the dense measurer gives where it needs more than a window: skeletons
    and their tip distances, distance histograms, the chance distributions and the depth of a centre are not here,
    and a distance beyond the window's reach is ``inf``.
    """
    from organella.measure.instances import _INSTANCE_COLUMNS, _POLARITY_COLUMNS, _UNMEASURED_SHAPE, _polar_spread_deg

    if source.center is None:
        raise ValueError(f"{source.object_id}: windowed instance tables need a boundary mask, which polarity is "
                         "measured from")
    center = source.center
    sample = np.array(source.sample_size)
    inst: Dict[str, List[Any]] = {col: [] for col in _INSTANCE_COLUMNS}
    dist: Dict[str, List[Any]] = {c: [] for c in ("distance_entity", "distance_label", "distance_target", "distance_um")}
    unmeasured = {"branches": float("nan"), "length_um": float("nan"), "tortuosity": float("nan")}
    for name, instances in boxes.items():
        centroids = []
        for label, instance in instances.items():
            stats = measured.stats[name][label]
            inst["instance_entity"].append(name)
            inst["instance_label"].append(label)
            for key in METRICS_3D:
                inst[f"instance_{key}"].append(stats.get(key, float("nan")))
            for key in _UNMEASURED_SHAPE[3]:
                inst[f"instance_{key}"].append(float("nan"))
            for key, value in unmeasured.items():
                inst[f"instance_{key}"].append(value)
            centroid = np.array(stats["centroid_um"])
            centroids.append(centroid)
            polar = polarity_from_offset(centroid - np.array(center))
            for key in _POLARITY_COLUMNS:
                inst[f"instance_{key}"].append(polar.get(key, float("nan")))
            if polarity_spread:
                window = expanded(instance.box, 1, source.shape)
                at = np.argwhere(source.read(name, window) == label) + np.array([s.start for s in window])
                inst["instance_polar_spread_deg"].append(_polar_spread_deg(at, sample, center))
            else:
                inst["instance_polar_spread_deg"].append(float("nan"))
        if len(centroids) > 1:
            points = np.vstack(centroids)
            nearest = cKDTree(points).query(points, k=2)[0][:, 1].tolist()
        else:
            nearest = [float("nan")] * len(centroids)
        inst["instance_distance_to_closest_same_type_um"].extend(nearest)
        for label in instances:
            for target, found in measured.distances[(name, label)].items():
                dist["distance_entity"].append(name)
                dist["distance_label"].append(label)
                dist["distance_target"].append(target)
                dist["distance_um"].append(found["distance_um"])
    return inst, dist


class _InstancesOf:
    """The instances of one structure as the geometry code reads them, each with its mask, tile by tile.

    Iterating reads the object once through ``tiles``, and yields instances with what ``regionprops`` has of one: a
    ``label``, a ``bbox`` (lows, then highs, in voxels), an ``area`` in voxels and the ``image`` of the instance in
    its box. It is a sequence only in that it has a length and can be walked.
    """

    def __init__(self, source: ObjectSource, name: str, instances: Mapping[int, InstanceBox]) -> None:
        self._source, self._name, self._instances = source, name, instances

    def __len__(self) -> int:
        return len(self._instances)

    def __iter__(self):
        for region, members in tiles(self._source, {self._name: self._instances}, (0,) * len(self._source.shape)):
            reader = _reader(self._source, region)
            for _, instance in members:
                yield SimpleNamespace(
                    label=instance.label, area=instance.voxels,
                    bbox=tuple(b.start for b in instance.box) + tuple(b.stop for b in instance.box),
                    image=reader.read(self._name, instance.box) == instance.label)


def mesh_rows_for_windows(source: ObjectSource, coarse: ObjectStack, boxes: Mapping[str, Mapping[int, InstanceBox]],
                          stats: Mapping[str, Mapping[int, Mapping[str, Any]]], options: MeshOptions,
                          metrics: Mapping[tuple, Mapping[str, Any]], contacts: Sequence[Contact]) -> List[Dict[str, Any]]:
    """The geometry rows of an object, as ``mesh_rows_for_object`` has them, with its instances meshed from windows.

    A label structure's instances are meshed at full resolution from their own boxes, and a whole-structure mask
    from the coarse view ``coarse``: the surface of a network the size of the cell is not a window's work, and
    at the coarse voxel size it is as much detail as it can be drawn with. Skeletons are not here.
    """
    pool = WorkPool(worker_share(options.mesh_workers), what="meshing")
    rows: List[Dict[str, Any]] = []
    try:
        for index, name in enumerate(coarse.entity_names):
            kind = coarse.kinds[name]
            if kind != "label":
                rows.extend(mask_rows(name, kind, coarse.volume(index), coarse.sample_size, source.object_id, "",
                                      options, 3, False))
                continue
            instances = boxes.get(name, {})
            if instances:
                rows.extend(instance_rows(name, kind, _InstancesOf(source, name, instances), stats[name], {},
                                          source.sample_size, source.object_id, "", options, metrics, 3, False,
                                          source.center, pool))
    finally:
        pool.shutdown()
    if options.contact_max_um is not None:
        rows.extend(contact_rows(contacts, source.object_id, ""))
    return rows
