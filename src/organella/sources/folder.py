"""Samples laid out as folders: per object, ``<prefix>_<name>_label.tif`` and ``<prefix>_<name>_mask.tif`` beside a source
image ``<prefix>.tif`` (or NIfTI, NRRD, MetaImage), or one file per structure in a ``segmentations/`` folder."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import dask.array as da

from organella.app.ports import SampleRef
from organella.core import Kind, Sample, Structure, VoxelSize
from organella.sources.arrays import image_stem, image_suffix, open_image
from organella.sources.image_metadata import voxel_size_from_file
from organella.sources.label_map import split_label_map
from organella.sources.selection import selected

_ENTITY_FILE = re.compile(r"^(?P<body>.+)_(?P<kind>labels?|mask)$", re.IGNORECASE)
SEGMENTATION_DIRS = ("segmentations", "segmentation", "masks", "labels")


@dataclass(frozen=True)
class EntityFile:
    name: str
    kind: Kind | None   # None: not said by the name, so read off the content
    path: Path


@dataclass(frozen=True)
class ObjectFiles:
    """What one object folder holds: the source image, if any, and the entity files."""

    source_image: Path | None
    entities: list[EntityFile]


def _kind_of(word: str) -> Kind:
    return Kind.MASK if word.lower() == "mask" else Kind.LABEL


def object_files(folder: Path) -> ObjectFiles:
    """The plain image beside the entity files says which prefix to strip from their names; a file in a segmentation
    folder is named by itself alone."""
    images = [p for p in sorted(folder.iterdir()) if p.is_file() and image_suffix(p)]
    parsed = [(p, _ENTITY_FILE.match(image_stem(p))) for p in images]
    plain = [p for p, match in parsed if match is None]
    prefixes = [image_stem(p) + "_" for p in plain]
    found = []
    for path, match in parsed:
        if match is None:
            continue
        body = match["body"]
        prefix = max((pre for pre in prefixes if body.startswith(pre)), key=len, default="")
        found.append(EntityFile(body.removeprefix(prefix), _kind_of(match["kind"]), path))
    for name in SEGMENTATION_DIRS:
        for path in sorted(p for p in (folder / name).glob("*") if p.is_file() and image_suffix(p)) if (folder / name).is_dir() else ():
            match = _ENTITY_FILE.match(image_stem(path))
            found.append(EntityFile(match["body"], _kind_of(match["kind"]), path) if match else EntityFile(image_stem(path), None, path))
    names = [f.name for f in found]
    if len(set(names)) != len(names):
        clash = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"{folder}: more than one file for {', '.join(repr(n) for n in clash)}")
    return ObjectFiles(plain[0] if plain else None, found)


def object_folders(root: Path, groups: Sequence[str]) -> Iterator[tuple[Path, str | None]]:
    """Object folders under ``root``, with the group each was found in."""
    if groups:
        for group in groups:
            yield from ((f, group) for f in _children_with_entities(root / group))
    elif object_files(root).entities:
        yield root, None
    else:
        yield from ((f, None) for f in _children_with_entities(root))


def _children_with_entities(folder: Path) -> list[Path]:
    return [c for c in sorted(folder.iterdir()) if c.is_dir() and object_files(c).entities]


def _kind_from_content(data: da.Array) -> Kind:
    """More than one distinct non-zero value is a label volume, one is a mask."""
    values = da.unique(data).compute()
    return Kind.LABEL if (values != 0).sum() > 1 else Kind.MASK


@dataclass(frozen=True)
class FolderSource:
    root: Path
    groups: Sequence[str] = ()
    object_mask: str | None = None
    voxel_size_um: tuple[float, ...] | None = None
    entities: frozenset[str] = frozenset()
    label_map: Mapping[int, str] | None = None
    label_map_entity: str | None = None

    def samples(self) -> Iterator[SampleRef]:
        for folder, group in object_folders(self.root, self.groups):
            yield SampleRef(folder.name, partial(self._open, folder, group))

    def _open(self, folder: Path, group: str | None) -> Sample:
        files = object_files(folder)
        structures = {f.name: self._structure(f) for f in files.entities}
        if self.label_map:
            structures = split_label_map(structures, self.label_map, self.label_map_entity)
        return Sample(
            id=folder.name,
            group=group,
            voxel_size=self._voxel_size(files),
            structures=selected(structures, self.entities, self.object_mask),
            object_mask=self.object_mask,
        )

    def _structure(self, file: EntityFile) -> Structure:
        data = open_image(file.path)
        if file.kind is not None:
            return Structure(file.name, file.kind, data)
        return Structure(file.name, Kind.MASK if file.name == self.object_mask else _kind_from_content(data), data)

    def _voxel_size(self, files: ObjectFiles) -> VoxelSize:
        if self.voxel_size_um is not None:
            return VoxelSize(self.voxel_size_um)
        candidates = [files.source_image, *(f.path for f in files.entities)]
        for path in filter(None, candidates):
            if (found := voxel_size_from_file(path)) is not None:
                return found
        raise ValueError(f"{candidates[-1].parent}: no voxel size in the files; pass --voxel-size-um")
