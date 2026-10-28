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
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from organella.config import RunConfig
from organella.measure.discovery import Dataset, discover_dataset, key_for_name, load_volume
from organella.measure.loading import _channel_order, select_entities, voxel_size_of
from organella.measure.readers import ArrayRef, _array, read_header, read_volume
from organella.analysis.shapes import foreground_bounds

logger = logging.getLogger(__name__)

Box = Tuple[slice, ...]


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
        for key, entity in self._entities.items():
            shape, _ = read_header(entity.source)
            if shape != source_shape:
                raise ValueError(f"{self.object_id}: entity '{entity.name}' has shape {shape}, "
                                 f"source image has {source_shape}")

        self.object_mask_name = dataset.object_mask_name
        mask_key = key_for_name(dataset.entities, dataset.object_mask_name)
        self._mask_key = mask_key
        self._mask: Optional[np.ndarray] = None
        origin = (0, 0, 0)
        shape = tuple(source_shape)
        if mask_key is not None:
            whole = load_volume(self._entities[mask_key].source)
            bounds = foreground_bounds(whole > 0)
            if bounds is not None:
                box = tuple(slice(lo, hi) for lo, hi in bounds)
                origin = tuple(b.start for b in box)
                shape = tuple(b.stop - b.start for b in box)
                whole = whole[box]
            self._mask = np.array(whole)
        self.origin: Tuple[int, int, int] = origin
        self.shape: Tuple[int, int, int] = shape
        self._whole: Dict[str, np.ndarray] = {}

    # ── what the object is ────────────────────────────────────────────────────

    @property
    def center(self) -> Optional[Tuple[float, ...]]:
        """The boundary's centroid in µm, the origin of every polarity metric; None for an object with no boundary."""
        if self._mask is None:
            return None
        from organella.analysis.distances import object_center_um

        return object_center_um(self._mask, self.sample_size)

    def declared_labels(self) -> List[str]:
        """The label structures a manifest declared: ``load_object`` splits one of these that holds a single id.

        A window of one is the ids as stored. Where the array is binary rather than an instance map, the dense
        stack and a window differ, so the caller has to know which it has.
        """
        return [e.name for e in self._entities.values() if e.declared and e.kind == "label"]

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
        if self._mask is None or not self._clip:
            return None
        return self._mask[box] > 0

    def _read_clipped(self, key: str, box: Box, inside: Optional[np.ndarray]) -> np.ndarray:
        if key == self._mask_key:
            return np.array(self._mask[box])                        # the boundary itself is not clipped
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
