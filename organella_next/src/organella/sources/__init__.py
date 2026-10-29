from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from organella.app.ports import SampleSource
from organella.sources.arrays import DEFAULT_CACHE_BYTES
from organella.sources.folder import FolderSource
from organella.sources.store import MANIFEST, StoreSource

__all__ = ["FolderSource", "StoreSource", "open_source"]


def open_source(path: Path, groups: Sequence[str] = (), object_mask: str | None = None,
                voxel_size_um: tuple[float, ...] | None = None, cache_bytes: int = DEFAULT_CACHE_BYTES,
                entities: frozenset[str] = frozenset(), label_map: Mapping[int, str] | None = None,
                label_map_entity: str | None = None) -> SampleSource:
    """A manifest file, or a folder of object folders holding one, is a store; any other folder is TIFFs."""
    if path.suffix == ".json" or any(path.glob(f"*/{MANIFEST}")):
        return StoreSource(path, object_mask, cache_bytes, entities)
    return FolderSource(path, groups, object_mask, voxel_size_um, entities, label_map, label_map_entity)
