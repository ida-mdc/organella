"""Object-folder entity discovery, voxel size, and volume preparation.

The naming rules that turn one object folder into one record.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
from scipy.ndimage import label as nd_label

from organella.analysis.shapes import foreground_bounds
from organella.config import normalize_name
from organella.measure.readers import (
    ArrayRef,
    image_stem,
    is_image,
    read_voxel_size_um,
    read_volume,
)

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    name: str
    kind: str  # "label", "mask", or "auto" until its content settles it
    path: Path
    # Set instead of reading ``path`` when the object is described by a manifest: the
    # entity is a window onto an array in a chunked store, not a file on disk.
    ref: "ArrayRef | None" = None

    @property
    def source(self) -> "Path | ArrayRef":
        """Where the pixels come from, whichever kind of object this is."""
        return self.ref if self.ref is not None else self.path


@dataclass
class Dataset:
    source: "Path | ArrayRef"
    object_mask_name: str | None
    entities: Dict[str, Entity]
    # µm per axis when the manifest states it, overriding whatever the store records.
    voxel_size_um: Tuple[float, ...] | None = None


@dataclass
class Discovery:
    """Everything entity discovery learned about an object folder, including what it threw away."""
    source: "Path | ArrayRef | None"
    entities: Dict[str, Entity]
    object_mask_name: str | None
    unparsed: list[Path]
    rejected: list[Tuple[Path, str]]
    errors: list[str]
    voxel_size_um: Tuple[float, ...] | None = None


def shared_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def split_kind_suffix(stem: str) -> tuple[str, str] | None:
    """``('s0011_rib_left_11', 'mask')`` for ``s0011_rib_left_11_mask``, else None.

    Only the trailing ``_label`` / ``_labels`` / ``_mask`` is read here; where the object's
    prefix ends inside the rest needs the source file to say, in
    :func:`entity_name_from_body`.
    """
    s = normalize_name(stem)
    m = re.match(r"^(.*?)_(labels?|mask)$", s)
    if not m or not m.group(1):
        return None
    return m.group(1), ("label" if m.group(2).startswith("label") else "mask")


def entity_name_from_body(body: str, shared: int) -> str | None:
    """The entity's own name: the body with the object's prefix taken off the front.

    ``shared`` is how much of the body the source name accounts for, from
    :func:`shared_prefix_len`. Splitting there rather than at the last underscore keeps
    names with underscores in them: ``s0011_rib_left_11`` is ``rib_left_11``, not ``11``,
    which would collide with ``rib_right_11``.
    """
    name = normalize_name(body[shared:])
    return name or None


def object_masks(entities: Dict[str, Entity]) -> list[str]:
    """The entities that could be the one that bounds the object.

    An ``auto`` entity is included: what it is gets decided from its content when the
    pixels are read, and naming it as the boundary is itself the answer for that one.
    """
    return sorted({e.name for e in entities.values() if e.kind in ("mask", "auto")})


def key_for_name(entities: Dict[str, Entity], name: str | None) -> str | None:
    """The key of the entity called ``name``, whatever kind it turned out to be.

    Keys are ``<kind>:<name>``, but a kind can change after the key is made - a promoted
    mask, an ``auto`` entity learning what it is. The key is an identity, not a claim, so
    nothing rebuilds one from an assumed kind.
    """
    if name is None:
        return None
    for key, entity in entities.items():
        if entity.name == name:
            return key
    return None


def choose_object_mask(entities: Dict[str, Entity], requested: str | None) -> Tuple[str | None, str | None]:
    """The mask that bounds each object: the one that was asked for, and only that.

    Returns (name, error). Never guessed, because it decides the origin of every distance
    and polarity in the report. A mask the folder does not have is an error; naming none is
    not - the entities are then measured where they lie.
    """
    masks = object_masks(entities)
    available = ", ".join(masks) or "none"
    if requested is None:
        return None, None
    wanted = normalize_name(requested)
    if wanted in masks:
        return wanted, None
    return None, (f"No mask named '{wanted}' in this folder (masks found: {available}). "
                  "The object mask is the one every measurement is relative to.")


# An object folder may describe its object instead of holding it: one small JSON naming a
# chunked store, the arrays in it, and the window to read. That is what lets a 512³ crop of a
# 122-gigavoxel volume be measured at full resolution without downloading it, and it records
# exactly which store, scale and window a report came from.
MANIFEST_NAME = "source.json"


def read_manifest(path: Path) -> Discovery:
    """One object, described rather than stored. See :data:`MANIFEST_NAME` for the format.

        {
          "store": "s3://janelia-cosem-datasets/jrc_hela-2/jrc_hela-2.n5",
          "scale": "s0",                       # appended to every array path below
          "crop": "3456:3968,256:768,5504:6016",
          "source": "em/fibsem-uint16",
          "entities": { "mito": "labels/mito_seg", "er": "labels/er_seg" },
          "voxel_size_um": [0.00524, 0.004, 0.004]      # optional; else from the store
        }

    Every entity arrives as ``auto``: an array says nothing about whether it holds one
    structure or many, so the kind is read off its content with the pixels.
    """
    from organella.measure.readers import ArrayRef, parse_box

    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return Discovery(None, {}, None, [], [], [f"Could not read {path.name}: {exc}"])

    store = str(manifest.get("store") or "").strip()
    entities_named = manifest.get("entities") or {}
    if not store:
        return Discovery(None, {}, None, [], [], [f"{path.name} names no 'store'"])
    if not manifest.get("source"):
        return Discovery(None, {}, None, [], [], [f"{path.name} names no 'source' array"])
    if not entities_named:
        return Discovery(None, {}, None, [], [], [f"{path.name} names no 'entities'"])

    scale = str(manifest.get("scale") or "").strip("/")
    try:
        box = parse_box(manifest.get("crop"))
    except ValueError as exc:
        return Discovery(None, {}, None, [], [], [f"{path.name}: {exc}"])

    def ref(array_path: str, label: str) -> "ArrayRef":
        full = f"{str(array_path).strip('/')}/{scale}" if scale else str(array_path).strip("/")
        return ArrayRef(store=store, path=full, box=box, label=label)

    entities: Dict[str, Entity] = {}
    for raw_name, array_path in entities_named.items():
        name = normalize_name(str(raw_name))
        if not name:
            continue
        entities[f"auto:{name}"] = Entity(name=name, kind="auto", path=path,
                                          ref=ref(array_path, name))

    voxel = manifest.get("voxel_size_um")
    voxel_size = tuple(float(v) for v in voxel) if voxel else None
    return Discovery(
        source=ref(manifest["source"], path.parent.name),
        entities=entities,
        object_mask_name=None,
        unparsed=[],
        rejected=[],
        errors=[],
        voxel_size_um=voxel_size,
    )


# Subfolders holding an object's entities named by nothing but the structure, which is how
# most published segmentations ship - TotalSegmentator puts ``<subject>/ct.nii.gz`` beside
# ``<subject>/segmentations/liver.nii.gz``, and requiring the prefix on each of 117 files
# would mean rewriting gigabytes to rename them.
SEGMENTATION_DIRS = ("segmentations", "segmentation", "masks", "labels")


def _subfolder_entity_files(object_dir: Path) -> list[Path]:
    """Every image inside this object's segmentation subfolder, if it has one."""
    found: list[Path] = []
    for sub_name in SEGMENTATION_DIRS:
        sub = object_dir / sub_name
        if sub.is_dir():
            found.extend(sorted(p for p in sub.glob("*") if p.is_file() and is_image(p)))
    return found


def inspect_object_dir(object_dir: Path, object_mask: str | None = None) -> Discovery:
    """Run entity discovery without raising, reporting what was accepted and rejected.

    Without ``object_mask`` it still reports everything it found - which is what ``dry-run``
    shows you to pick a name from - and records the missing name as an error.
    """
    manifest = object_dir / MANIFEST_NAME
    if manifest.is_file():
        found = read_manifest(manifest)
        name, mask_error = choose_object_mask(found.entities, object_mask)
        if mask_error:
            found.errors.append(mask_error)
        found.object_mask_name = name
        return found

    errors: list[str] = []
    images = sorted(p for p in object_dir.glob("*") if p.is_file() and is_image(p))
    sub_entities = _subfolder_entity_files(object_dir)
    if not images:
        return Discovery(None, {}, None, [], [], [f"No image files found in {object_dir}"])

    # The body is everything before the trailing _label/_labels/_mask. Where the object's
    # own prefix ends inside it is settled below, once the source file is known.
    parsed: Dict[Path, Tuple[str, str]] = {}
    for p in images:
        split = split_kind_suffix(image_stem(p))
        if split:
            parsed[p] = split

    if not parsed and not sub_entities:
        errors.append("No NAME_label(s) or NAME_mask files found.")

    source_candidates = [p for p in images if p not in parsed]
    if not source_candidates:
        errors.append("No source image found (expected one that is not a label/mask).")
        return Discovery(None, {}, None, [], [], errors)

    # Choose the source image with the strongest shared prefix with the entity bodies.
    bodies = [body for body, _ in parsed.values()]

    def source_score(p: Path) -> tuple[int, int]:
        s = normalize_name(image_stem(p))
        score = sum(shared_prefix_len(s, body) for body in bodies)
        return score, int(p.stat().st_size)

    source = max(source_candidates, key=source_score)
    source_norm = normalize_name(image_stem(source))

    # Only entities whose shared prefix with the source name is maximal, with a
    # word-boundary guard so "c1" does not match a "c10" prefix.
    shared_lens = {p: shared_prefix_len(source_norm, body) for p, (body, _) in parsed.items()}
    max_shared = max(shared_lens.values()) if shared_lens else 0

    entities: Dict[str, Entity] = {}
    rejected: list[Tuple[Path, str]] = []
    for p, (body, kind) in parsed.items():
        sl = shared_lens[p]
        if sl < max_shared:
            rejected.append((p, f"'{body}' matches source '{source_norm}' less closely than the other entity files"))
            continue
        min_len = min(len(source_norm), len(body))
        if sl >= min_len:
            longer = source_norm if len(source_norm) >= len(body) else body
            if sl < len(longer) and longer[sl] != "_":
                rejected.append((p, f"'{body}' is not a word-boundary match for source '{source_norm}'"))
                continue
        name = entity_name_from_body(body, sl)
        if name is None:
            rejected.append((p, f"'{body}' is the source name with no entity name after it"))
            continue
        key = f"{kind}:{name}"
        if key in entities:
            rejected.append((p, f"duplicate {kind} '{name}': keeping {entities[key].path.name}"))
            continue
        entities[key] = Entity(name=name, kind=kind, path=p)

    # Named by the file alone: no prefix to strip, and label-or-mask is read off the
    # content when the pixels are loaded rather than guessed from the name.
    for p in sub_entities:
        stem = image_stem(p)
        split = split_kind_suffix(stem)
        name, kind = (normalize_name(split[0]), split[1]) if split else (normalize_name(stem), "auto")
        if not name:
            rejected.append((p, "file name is empty once normalised"))
            continue
        key = f"{kind}:{name}"
        if key in entities or key_for_name(entities, name) is not None:
            rejected.append((p, f"duplicate '{name}': keeping {(entities.get(key) or entities[key_for_name(entities, name)]).path.name}"))
            continue
        entities[key] = Entity(name=name, kind=kind, path=p)

    if not entities and not errors:
        errors.append("No NAME_label(s) or NAME_mask entities matching source basename were found.")

    object_mask_name, mask_error = choose_object_mask(entities, object_mask)
    if mask_error:
        errors.append(mask_error)

    unparsed = [p for p in images if p not in parsed and p != source]
    return Discovery(
        source=source,
        entities=entities,
        object_mask_name=object_mask_name,
        unparsed=unparsed,
        rejected=rejected,
        errors=errors,
    )


def discover_dataset(object_dir: Path, object_mask: str | None = None) -> Dataset:
    """The dataset in one folder, or a FileNotFoundError naming what is wrong with it."""
    d = inspect_object_dir(object_dir, object_mask)
    if d.errors:
        raise FileNotFoundError(d.errors[0])
    assert d.source is not None
    return Dataset(source=d.source, object_mask_name=d.object_mask_name, entities=d.entities,
                   voxel_size_um=d.voxel_size_um)


def infer_voxel_size_um_from_source(source_path: Path, ndim: int = 3) -> Tuple[float, ...]:
    """Sample size in µm along each spatial axis, in array order, from the file's own header.

    A 2D source has no Z spacing to read and none is invented: the returned tuple is
    (y, x), and everything downstream measures areas rather than volumes because of it.
    """
    return read_voxel_size_um(source_path, ndim)


def load_volume(path: Path) -> np.ndarray:
    """One entity's pixels: a 3D volume or a 2D plane, exactly as stored."""
    return read_volume(path)


def clip_to_object_mask(arr: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Zero everything outside the object: what the mask does not enclose is not measured."""
    out = arr.copy()
    out[~inside] = 0
    return out


def crop_to_object_bbox(volumes: Dict[str, np.ndarray], object_mask_key: str) -> Dict[str, np.ndarray]:
    bounds = foreground_bounds(volumes[object_mask_key] > 0)
    if bounds is None:
        return volumes
    sl = tuple(slice(lo, hi) for lo, hi in bounds)
    return {k: v[sl] for k, v in volumes.items()}


def promote_multicomponent_masks(
    volumes: Dict[str, np.ndarray],
    entities: Dict[str, Entity],
    object_mask_key: str,
) -> None:
    """Promote masks with >1 connected component (in the cropped volume) to label entities.

    Mutates ``volumes`` and ``entities`` in place. Must be called after
    ``crop_to_object_bbox`` so the component count reflects what is actually
    inside the object, not the full image.
    """
    # Full connectivity: 26-connected in 3D, 8-connected in 2D, so diagonal touching is
    # one component either way.
    any_volume = next(iter(volumes.values()), None)
    ndim = 3 if any_volume is None else any_volume.ndim
    cc_struct = np.ones((3,) * ndim, dtype=np.uint8)
    for key in list(volumes.keys()):
        entity = entities[key]
        if key == object_mask_key:
            continue
        if entity.kind == "label" and int(volumes[key].max()) > 1:
            continue

        binary = volumes[key] > 0
        if not binary.any():
            continue

        labeled, n = nd_label(binary, structure=cc_struct)
        if n <= 1:
            continue

        volumes[key] = labeled.astype(np.int32)
        entities[key] = Entity(name=entity.name, kind="label", path=entity.path,
                               ref=entity.ref)
        logger.info("organella: auto-label '%s': %d components -> label entity",
                    entity.name, n)
