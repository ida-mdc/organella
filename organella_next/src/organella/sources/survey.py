"""What a batch holds and whether each object can be analysed, from file headers alone."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import prod
from pathlib import Path
from typing import Sequence

from organella.core import Kind
from organella.sources.arrays import image_header
from organella.sources.folder import object_files, object_folders
from organella.sources.image_metadata import voxel_size_from_file


@dataclass
class ObjectSurvey:
    name: str
    source: str | None = None
    entities: dict[str, tuple[Kind | None, tuple[int, ...], str]] = field(default_factory=dict)   # name -> kind, shape, dtype
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def names(self, kind: Kind | None) -> list[str]:
        return sorted(n for n, (k, _, _) in self.entities.items() if k == kind)

    @property
    def voxels(self) -> int:
        return max((prod(shape) for _, shape, _ in self.entities.values()), default=0)


def survey(root: Path, groups: Sequence[str] = (), object_mask: str | None = None, voxel_size_given: bool = False) -> list[ObjectSurvey]:
    """One entry per object folder. Without an object mask a missing one is not a problem: listing the masks is what a
    survey is for; with one, every object is checked to have it."""
    found = []
    for folder, group in object_folders(root, groups):
        entry = ObjectSurvey(str(folder.relative_to(root)) if folder != root else folder.name)
        found.append(entry)
        try:
            files = object_files(folder)
        except ValueError as error:
            entry.errors.append(str(error))
            continue
        entry.source = files.source_image.name if files.source_image else None
        if entry.source is None:
            entry.warnings.append("no source image: the voxel size comes from the entity files, or --voxel-size-um")
        for file in files.entities:
            try:
                shape, dtype = image_header(file.path)
                entry.entities[file.name] = (file.kind, shape, dtype)
            except Exception as error:
                entry.errors.append(f"{file.path.name}: cannot read its header ({type(error).__name__}: {error})")
        shapes = {shape for _, shape, _ in entry.entities.values()}
        if len(shapes) > 1:
            entry.errors.append("structures differ in shape: " + ", ".join(f"{n} {s}" for n, (_, s, _) in sorted(entry.entities.items())))
        if object_mask and object_mask not in entry.entities:
            entry.errors.append(f"no object mask named '{object_mask}' (the structures are {', '.join(sorted(entry.entities)) or 'none'})")
        if not voxel_size_given and not any(voxel_size_from_file(p) for p in [files.source_image, *(f.path for f in files.entities)] if p):
            entry.errors.append("no voxel size in the files: pass --voxel-size-um")
    return found
