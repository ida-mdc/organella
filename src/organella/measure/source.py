"""One object, read a window at a time.

:func:`~organella.measure.loading.load_object` reads every structure of an object whole and stacks them, which
is what a measurement over the whole object needs and what sets the memory a run asks for: a cell 8 times as
fine in each direction is 512 times the voxels. A measurement of one instance needs a box around it, and a box
of a chunked store (zarr, N5, local or on S3) is a handful of its chunks.

An :class:`ObjectSource` opens an object the way ``load_object`` does - the same entities, the same boundary, the
same clipping and the same crop to the box the boundary fills - and reads nothing until a window is asked for.
``window(box)`` is what ``load_object(...).volumes()[name][box]`` would have been, for every structure, and the
tests hold it to that.

Not everything ``load_object`` does can be done a window at a time, and a source says so when it is opened and
does not guess:

- A structure whose kind is not declared is read off its content, which is every voxel of it.
- A binary array declared as labels is split into connected components, which are global.
- A label map is split into structures, also global.

Files that are not chunked (TIFF, NIfTI) cannot be read in part: such a structure is read whole the first time
a window of it is asked for, and kept.
"""

from __future__ import annotations

import logging
from itertools import product
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from organella.config import RunConfig
from organella.measure.discovery import Dataset, discover_dataset, key_for_name, load_volume
from organella.measure.loading import (
    SPATIAL_AXES_3D, _channel_order, _stack_narrowest, object_meta, select_entities, voxel_size_of,
)
from organella.measure.readers import ArrayRef, _array, read_header, read_volume
from organella.analysis.shapes import foreground_bounds, foreground_centroid
from organella.model import ObjectStack

logger = logging.getLogger(__name__)

Box = Tuple[slice, ...]

# Edge of the cube read at once when a whole object is walked, and the most voxels a slab of a decimated read holds.
BLOCK = 256
_SLAB_VOXELS = 64_000_000


def blocks(shape: Sequence[int], block: int = BLOCK) -> Iterator[Tuple[Box, Tuple[int, ...]]]:
    """Cubes of ``block`` voxels an edge that tile ``shape``, each as (its box, its corner)."""
    for corner in product(*(range(0, n, block) for n in shape)):
        yield tuple(slice(c, min(c + block, n)) for c, n in zip(corner, shape)), corner


class ObjectSource:
    """The structures of one object, a window of them at a time."""

    def __init__(self, object_dir: Path, config: RunConfig | None = None) -> None:
        cfg = config or RunConfig()
        self.object_id = object_dir.name
        self._clip = cfg.clip
        if cfg.label_map:
            raise ValueError(f"{self.object_id}: a label map is split into structures whole, so it cannot be "
                             "read a window at a time")
        found = discover_dataset(object_dir, cfg.object_mask)
        entities = select_entities(found.entities, cfg.entities, found.object_mask_name)
        undeclared = sorted(e.name for e in entities.values() if e.kind == "auto")
        if undeclared:
            raise ValueError(f"{self.object_id}: {', '.join(undeclared)} have no declared kind (label or mask), "
                             "which can only be read off the whole array")
        dataset = Dataset(source=found.source, object_mask_name=found.object_mask_name, entities=entities,
                          voxel_size_um=found.voxel_size_um)

        source_shape, _ = read_header(dataset.source)
        if len(source_shape) != 3:
            raise ValueError(f"{self.object_id}: windows are for volumes, and this is {len(source_shape)}D")
        self._source_shape = source_shape
        self.sample_size, self.voxel_size_source = voxel_size_of(dataset, cfg, object_dir, 3)

        self._keys = _channel_order(dataset)
        self._entities = {key: dataset.entities[key] for key in self._keys}
        self.names: List[str] = [self._entities[key].name for key in self._keys]
        self.kinds: Dict[str, str] = {self._entities[key].name: self._entities[key].kind for key in self._keys}
        self._dtypes: Dict[str, np.dtype] = {}
        for key, entity in self._entities.items():
            shape, dtype = read_header(entity.source)
            if shape != source_shape:
                raise ValueError(f"{self.object_id}: entity '{entity.name}' has shape {shape}, "
                                 f"source image has {source_shape}")
            self._dtypes[key] = np.dtype(dtype)

        self.object_mask_name = dataset.object_mask_name
        self._mask_key = key_for_name(dataset.entities, dataset.object_mask_name)
        self._whole: Dict[str, np.ndarray] = {}
        self._dataset = dataset
        # Until the boundary has been found the object is the whole store.
        self.origin: Tuple[int, int, int] = (0, 0, 0)
        self.shape: Tuple[int, int, int] = tuple(source_shape)
        # What the boundary encloses, found a block at a time: the box it fills, how many voxels it
        # has, and where its centre of mass is. The mask itself is not kept, so an object whose mask
        # is larger than memory costs no more to open than a small one.
        self.enclosed_voxels: Optional[int] = None
        self._centroid: Optional[np.ndarray] = None
        if self._mask_key is not None:
            self._scan_mask()
        self.meta = object_meta(dataset, self._keys, self.object_id, self.shape, self.sample_size,
                                self.voxel_size_source, self.center)

    def _scan_mask(self) -> None:
        """Find the box, the voxel count and the centre of the boundary, one block of it at a time."""
        low, high, total, weighted = None, None, 0, np.zeros(3)
        for box, _ in blocks(self.shape):
            inside = self._read(self._mask_key, box) > 0
            found = int(inside.sum())
            if not found:
                continue
            start = np.array([b.start for b in box])
            bounds = np.array(foreground_bounds(inside)) + start[:, None]
            low = bounds[:, 0] if low is None else np.minimum(low, bounds[:, 0])
            high = bounds[:, 1] if high is None else np.maximum(high, bounds[:, 1])
            total += found
            weighted += (foreground_centroid(inside) + start) * found
        if total == 0:
            return
        self.origin = tuple(int(v) for v in low)
        self.shape = tuple(int(h - l) for l, h in zip(low, high))
        self.enclosed_voxels = total
        self._centroid = weighted / total - np.array(self.origin)

    # ── what the object is ────────────────────────────────────────────────────

    @property
    def center(self) -> Optional[Tuple[float, ...]]:
        """The boundary's centroid in µm, the origin of every polarity metric; None for an object with no boundary."""
        if self._centroid is None:
            return None
        return tuple(float(c * size) for c, size in zip(self._centroid, self.sample_size))

    def declared_labels(self) -> List[str]:
        """The label structures a manifest declared: ``load_object`` splits one of these that holds a single id.

        A window of one is the ids as stored. Where the array is binary rather than an instance map, the dense
        stack and a window differ, so the caller has to know which it has.
        """
        return [e.name for e in self._entities.values() if e.declared and e.kind == "label"]

    # ── the whole object, coarsely ────────────────────────────────────────────

    def coarse_factor(self, max_voxels: int) -> int:
        """The smallest whole number of voxels per coarse voxel, along each axis, that leaves a structure
        of at most ``max_voxels`` voxels; 1 when the object is that small already."""
        factor = 1
        while int(np.prod([-(-n // factor) for n in self.shape])) > max_voxels:
            factor += 1
        return factor

    def coarse_stack(self, factor: int) -> ObjectStack:
        """Every structure of the object as one stack, keeping every ``factor``-th voxel along each axis.

        What the measurements that need the whole object run on when the whole object is too big to
        hold: the stack ``load_object`` would build, at ``factor`` times the voxel size. Ids are kept
        as stored, so a label stays the label it was; a structure thinner than ``factor`` voxels may
        miss the voxels that were kept. With ``factor`` 1 it is the object ``load_object`` returns.
        The boundary's centre is the one found at full resolution, not recomputed from what is kept.
        """
        if factor < 1:
            raise ValueError(f"a coarse stack keeps every factor-th voxel, and {factor} is not a whole number")
        shape = tuple(-(-n // factor) for n in self.shape)
        kept = {key: np.zeros(shape, dtype=self._dtypes[key]) for key in self._keys}
        # Slabs of whole multiples of factor along the first axis, so that the voxels kept are the
        # ones a stride over the whole object would keep.
        per_row = int(np.prod(self.shape[1:]))
        rows = factor * max(1, _SLAB_VOXELS // (per_row * factor))
        for start in range(0, self.shape[0], rows):
            box = (slice(start, min(start + rows, self.shape[0])),) + tuple(slice(0, n) for n in self.shape[1:])
            read = self.window(box)
            for key, name in zip(self._keys, self.names):
                piece = read[name][::factor, ::factor, ::factor]
                kept[key][start // factor:start // factor + piece.shape[0]] = piece
            del read
        stack = _stack_narrowest(kept, self._keys, self.object_id)
        center = self.center
        meta = object_meta(self._dataset, self._keys, self.object_id, shape,
                           tuple(float(size) * factor for size in self.sample_size),
                           self.voxel_size_source, center)
        return ObjectStack(stack, "C" + SPATIAL_AXES_3D, meta)

    # ── windows ───────────────────────────────────────────────────────────────

    def window(self, box: Sequence[slice] | None = None) -> Dict[str, np.ndarray]:
        """Every structure in ``box``, by name, as arrays of their own; the whole object for no box.

        ``box`` is in the object's own coordinates (after the crop to the boundary's box), one slice per axis.
        """
        box = self._clipped(box)
        inside = self._inside(box)
        return {name: self._read_clipped(key, box, inside)
                for key, name in zip(self._keys, self.names)}

    def read(self, name: str, box: Sequence[slice] | None = None) -> np.ndarray:
        """One structure in ``box``, as ``window(box)[name]`` would be, without reading the others."""
        box = self._clipped(box)
        key = self._keys[self.names.index(name)]
        return self._read_clipped(key, box, self._inside(box))

    def _inside(self, box: Box) -> Optional[np.ndarray]:
        """What the boundary encloses in ``box``, or None where nothing is clipped."""
        if self._mask_key is None or not self._clip:
            return None
        return self._read(self._mask_key, box) > 0

    def _read_clipped(self, key: str, box: Box, inside: Optional[np.ndarray]) -> np.ndarray:
        if key == self._mask_key:
            return self._read(key, box)                             # the boundary itself is not clipped
        volume = self._read(key, box)
        if inside is not None:
            volume[~inside] = 0
        return volume

    def _clipped(self, box: Sequence[slice] | None) -> Box:
        if box is None:
            return tuple(slice(0, n) for n in self.shape)
        if len(box) != 3:
            raise ValueError(f"a window has one slice per axis, got {len(box)}")
        out = []
        for part, size in zip(box, self.shape):
            start, stop, step = part.indices(size)
            if step != 1:
                raise ValueError("a window is a box: its slices have no step")
            out.append(slice(start, max(start, stop)))
        return tuple(out)

    def _read(self, key: str, box: Box) -> np.ndarray:
        source = self._entities[key].source
        if isinstance(source, ArrayRef):
            # The array itself, not read_volume: that turns a window one slice deep into a plane.
            return np.array(_array(source)[self._in_the_store(source, box)])
        if key not in self._whole:
            self._whole[key] = read_volume(source)
        return np.array(self._whole[key][tuple(slice(o + b.start, o + b.stop) for o, b in zip(self.origin, box))])

    def _in_the_store(self, ref: ArrayRef, box: Box) -> Box:
        """``box`` in the store's own coordinates: past the reference's crop and the boundary's box."""
        base = ref.box or tuple(slice(0, n) for n in self._source_shape)
        return tuple(slice(c.start + o + b.start, c.start + o + b.stop)
                     for c, o, b in zip(base, self.origin, box))
