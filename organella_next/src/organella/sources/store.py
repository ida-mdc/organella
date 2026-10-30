"""Samples read from a Zarr or N5 store, local or on S3, as described by JSON manifests.

    {"store": "s3://bucket/cell.n5", "scale": "s3", "crop": "3712:4224,256:768,5760:6272",
     "entities": {"mito": "labels/mito_seg", "er": {"array": "labels/er_seg", "kind": "label"}},
     "masks": {"cell": "cell_mask.tif"}, "object_mask": "cell"}

``scale`` names the resolution level under every array; ``masks`` are files beside the manifest already cut to
the crop (a list of names instead marks entities of the store as masks). ``voxel_size_um`` is read from the
arrays' ``transform`` when omitted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Iterator

from organella.app.ports import SampleRef
from organella.core import Kind, Sample, Structure, VoxelSize
from organella.sources.selection import selected
from organella.sources.arrays import DEFAULT_CACHE_BYTES, cache_context, open_store_array, open_tiff
from organella.sources.cell_mask import open_cell_mask
from organella.sources.store_metadata import voxel_size_from_store

MANIFEST = "source.json"


def _crop(spec: str | None) -> tuple[slice, ...]:
    """``"z0:z1,y0:y1,x0:x1"``; an end may be left open and a start may count from the end."""
    window = tuple(slice(*(int(v) if v else None for v in axis.split(":"))) for axis in (spec or ":").split(","))
    if any(s.step not in (None, 1) for s in window):
        raise ValueError(f"crop '{spec}' has a step: the voxel size would no longer match")
    return window


def _manifests(root: Path) -> list[Path]:
    return [root] if root.is_file() else sorted(root.glob(f"*/{MANIFEST}"))


def _open_mask(path: Path):
    return open_cell_mask(path) if path.suffix == ".npz" else open_tiff(path)


@dataclass(frozen=True)
class StoreSource:
    """One manifest file, or a folder of object folders that each hold a ``source.json``."""

    root: Path
    object_mask: str | None = None
    cache_bytes: int = DEFAULT_CACHE_BYTES  # decoded chunks kept in memory, shared by the sample's arrays
    entities: frozenset[str] = frozenset()

    def samples(self) -> Iterator[SampleRef]:
        for manifest in _manifests(self.root):
            sample_id = manifest.parent.name if manifest.name == MANIFEST else manifest.stem
            yield SampleRef(sample_id, partial(self._open, manifest, sample_id))

    def _open(self, manifest: Path, sample_id: str) -> Sample:
        spec = json.loads(manifest.read_text())
        window = _crop(spec.get("crop"))
        level = f"/{spec['scale']}" if spec.get("scale") else ""
        masks = spec.get("masks", [])
        cache = cache_context(self.cache_bytes)
        arrays = {}
        structures = {}
        for name, entry in spec["entities"].items():
            path, kind = (entry, Kind.LABEL) if isinstance(entry, str) else (entry["array"], Kind(entry.get("kind", "label")))
            arrays[name] = path + level
            structures[name] = Structure(name, Kind.MASK if name in masks and isinstance(masks, list) else kind,
                                         open_store_array(spec["store"], arrays[name], cache)[window])
        if isinstance(masks, dict):
            structures.update({name: Structure(name, Kind.MASK, _open_mask(manifest.parent / file))
                               for name, file in masks.items()})
        mask_name = self.object_mask or spec.get("object_mask")
        return Sample(
            id=sample_id,
            group=None,
            voxel_size=self._voxel_size(spec, arrays),
            structures=selected(structures, self.entities, mask_name),
            object_mask=mask_name,
        )

    @staticmethod
    def _voxel_size(spec: dict, arrays: dict[str, str]) -> VoxelSize:
        if "voxel_size_um" in spec:
            return VoxelSize(tuple(spec["voxel_size_um"]))
        found = voxel_size_from_store(spec["store"], next(iter(arrays.values())))
        if found is None:
            raise ValueError("no voxel_size_um in the manifest and no transform on the arrays")
        return found
