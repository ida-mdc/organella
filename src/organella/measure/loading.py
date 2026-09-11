"""Reading one object folder: what an object is on disk, and how it becomes a stack.

The unit of analysis is a *folder*: a source image plus its entity volumes
(``<prefix>_<name>_label.tif`` / ``_mask.tif``). ``is_object_dir`` recognises one by what is
inside it, and ``find_object_dirs`` walks a batch without descending into a folder it has
already claimed, so the TIFFs inside are never objects of their own.

``load_object`` stacks every entity along a C axis, ``CZYX`` for a volume and ``CYX`` for a
plane, in the order ``channel_names`` names them. One array holding every entity is what
lets a single measurer answer a cross-entity question - a distance, a contact.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Tuple

import numpy as np

from organella.config import RunConfig, normalize_name
from organella.analysis.distances import object_center_um, segmented_center_um
from organella.model import ObjectStack
from organella.measure.discovery import (
    Dataset,
    Entity,
    key_for_name,
    clip_to_object_mask,
    crop_to_object_bbox,
    discover_dataset,
    infer_voxel_size_um_from_source,
    inspect_object_dir,
    load_volume,
    promote_multicomponent_masks,
)
from organella.measure.readers import read_header, voxel_size_source as _voxel_size_source

logger = logging.getLogger(__name__)

@lru_cache(maxsize=256)
def _cached_inspect(object_dir: str, object_mask: str | None = None):
    """Cache discovery per folder: discovery, read_header and load all ask for it.

    Keyed on the object mask too, so naming a different boundary is not served a stale
    answer from a warm process.
    """
    return inspect_object_dir(Path(object_dir), object_mask)


def _source_header(source_path: Path) -> Tuple[Tuple[int, ...], str]:
    """Spatial shape and dtype of the source image: (Z, Y, X) or (Y, X)."""
    return read_header(source_path)


def _label_dtype(volumes: Dict[str, np.ndarray], object_id: str) -> np.dtype:
    """The narrowest integer type that holds every label id in this object.

    The stack is the largest allocation in a run: a 371×1257×1176 object with five entities
    is 10.5 GB as int32 and 5.2 GB as uint16. Ids are never rounded - a non-integral value
    means the volume is not a segmentation, and says so.
    """
    highest = 0
    for name, vol in volumes.items():
        if vol.size == 0:
            continue
        if np.issubdtype(vol.dtype, np.floating):
            finite = vol[np.isfinite(vol)]
            if finite.size and not np.all(np.equal(np.mod(finite, 1), 0)):
                raise ValueError(
                    f"{object_id}: entity '{name}' has non-integer values, so it is not a "
                    "label or mask volume"
                )
        lo, hi = float(vol.min()), float(vol.max())
        if lo < 0:
            raise ValueError(f"{object_id}: entity '{name}' has negative label ids ({lo})")
        highest = max(highest, hi)
    for dtype in (np.uint8, np.uint16, np.uint32):
        if highest <= np.iinfo(dtype).max:
            return np.dtype(dtype)
    return np.dtype(np.uint64)


def _stack_narrowest(volumes: Dict[str, np.ndarray], keys: List[str], object_id: str) -> np.ndarray:
    """Stack the entities along C, converting one at a time and freeing as we go.

    Written channel by channel into a preallocated array rather than via np.stack, so the
    source volumes and the stack are never both fully in memory.
    """
    dtype = _label_dtype(volumes, object_id)
    first = volumes[keys[0]]
    stack = np.empty((len(keys), *first.shape), dtype=dtype)
    for i, key in enumerate(keys):
        np.copyto(stack[i], volumes[key], casting="unsafe")
        volumes[key] = np.empty((0,) * first.ndim, dtype=dtype)  # release the source volume
    return stack


SPATIAL_AXES_3D = "ZYX"
SPATIAL_AXES_2D = "YX"


def _config_voxel_size(configured: Tuple[float, ...], ndim: int, object_dir: Path) -> Tuple[float, ...]:
    """The configured sample size, checked against the data's dimensionality.

    ``--voxel-size-um`` takes ``z,y,x`` for volumes, ``y,x`` for planes. A mismatch is
    refused rather than padded or truncated, which would rescale every measurement.
    """
    if len(configured) == ndim:
        return tuple(float(v) for v in configured)
    wanted = "z,y,x" if ndim == 3 else "y,x"
    raise ValueError(
        f"{object_dir.name}: images are {ndim}D, so --voxel-size-um needs '{wanted}', "
        f"got {len(configured)} value(s): {','.join(str(v) for v in configured)}"
    )


def resolve_auto_kinds(
    volumes: Dict[str, np.ndarray],
    entities: Dict[str, Entity],
    object_mask_key: str | None,
) -> None:
    """Decide what each ``auto`` entity is from its content, in place.

    A name like ``segmentations/liver.nii.gz`` says nothing about whether it holds one
    structure or many, but the array does: more than one distinct non-zero value is a label
    volume, one is a mask. The object boundary is a mask whatever it holds.
    """
    for key, entity in list(entities.items()):
        if entity.kind != "auto":
            continue
        if key == object_mask_key:
            kind = "mask"
        else:
            values = np.unique(volumes[key])
            kind = "label" if values[values != 0].size > 1 else "mask"
        entities[key] = Entity(name=entity.name, kind=kind, path=entity.path,
                               ref=entity.ref)
        logger.info("organella: '%s' read as a %s entity", entity.name, kind)


def select_entities(
    entities: Dict[str, Entity],
    wanted: FrozenSet[str] | None,
    object_mask_name: str | None,
) -> Dict[str, Entity]:
    """The entities to keep: those named, plus the object mask, or everything.

    TotalSegmentator ships 117 structures per subject, which stack to 118 channels of the
    whole field of view, so selecting is how such an object is measured at all. An unknown
    name is an error listing what the folder has, not a silent empty set.
    """
    if not wanted:
        return entities
    keep, unknown = {}, set(wanted)
    for key, entity in entities.items():
        if entity.name in wanted or entity.name == object_mask_name:
            keep[key] = entity
            unknown.discard(entity.name)
    if unknown:
        available = ", ".join(sorted(e.name for e in entities.values())) or "none"
        raise FileNotFoundError(
            f"No entity named {', '.join(sorted(unknown))} in this folder "
            f"(entities found: {available})"
        )
    return keep


def split_label_map(
    volumes: Dict[str, np.ndarray],
    entities: Dict[str, Entity],
    label_map: Dict[int, str],
    entity_name: str | None,
    wanted: FrozenSet[str] | None,
) -> None:
    """Split one multi-structure label volume into an entity per named id, in place.

    One volume whose ids each mean a different structure is one entity to discovery and many
    to the report. Only named ids become entities, and only those the filter keeps are
    materialised: 117 full-size channels is how an object that fits in memory stops fitting.
    """
    target = key_for_name(entities, entity_name) if entity_name else None
    if target is None:
        labels = [k for k, e in entities.items() if e.kind == "label"]
        if len(labels) != 1:
            names = ", ".join(sorted(e.name for e in entities.values())) or "none"
            raise ValueError(
                f"--label-map needs to know which entity to split: this folder has "
                f"{len(labels)} label entities ({names}). Name one with --label-map-entity."
            )
        target = labels[0]

    source_path = entities[target].path
    source_volume = volumes.pop(target)
    del entities[target]

    present = set(np.unique(source_volume).tolist())
    for value, name in sorted(label_map.items()):
        clean = normalize_name(name)
        if not clean or value not in present:
            continue
        if wanted and clean not in wanted:
            continue
        key = f"mask:{clean}"
        if key in entities:
            continue
        # Every split entity came out of the one file, and the object row records that.
        volumes[key] = (source_volume == value).astype(np.uint8)
        entities[key] = Entity(name=clean, kind="mask", path=source_path)

    if not entities:
        raise ValueError(
            f"--label-map named no id present in {source_path.name} "
            f"(ids in the volume: {sorted(v for v in present if v)[:10]})"
        )


def _entity_origin(entity: Entity) -> str:
    """What the object row records as an entity's origin: a file name, or store and array."""
    if entity.ref is not None:
        return f"{entity.ref.store}/{entity.ref.path}"
    return entity.path.name


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _channel_order(dataset: Dataset) -> List[str]:
    """Entity keys in C order: the object mask first, then the rest by name.

    Ordered by *name*, never by kind, so the C axis of an object does not shift when
    auto-label promotion turns a mask into a label.
    """
    object_mask_key = key_for_name(dataset.entities, dataset.object_mask_name)
    others = sorted(k for k in dataset.entities if k != object_mask_key)
    first = [object_mask_key] if object_mask_key in dataset.entities else []
    return first + others


# What the report records as the source of its rows, in its footer.
LOADER_NAME = "organella"

# One entry per column the object row carries about where it came from and how it was read.
# Declared beside the code that fills it; column_schema gathers it for the report writer.
LOADED_COLUMNS: Dict[str, Any] = {
    "object_id": str,
    "object_mask_name": str,
    "entity_kinds": list,
    "entity_files": list,
    "entity_file_bytes": list,
    "n_entities": int,
    "object_shape": list,
    "spatial_dims": int,
    "voxel_size_source": str,
    "object_center_z_um": float,
    "object_center_y_um": float,
    "object_center_x_um": float,
}

LOADED_DESCRIPTIONS: Dict[str, str] = {
    "object_id": "Name of the object folder the structure volumes were read from.",
    "object_mask_name": "Name of the structure whose mask bounds the object; every measurement is relative to it.",
    "entity_kinds": "Kind ('label' or 'mask') of each structure, in channel_names order.",
    "entity_files": "File each structure was read from, in channel_names order.",
    "entity_file_bytes": "Size on disk of each structure's file, in channel_names order.",
    "n_entities": "How many structures were read for this object and stacked along C.",
    "object_shape": "Extent of the analysed region after cropping to the object mask's bounding box, in the spatial axes of dim_order: (Z, Y, X) for a volume, (Y, X) for a plane.",
    "spatial_dims": "3 for a volume, 2 for a plane. Which size and shape metrics the object's rows carry follows from it.",
    "voxel_size_source": "Where the voxel size came from: 'tiff-metadata' or 'config'.",
    "object_center_z_um": "Z coordinate in µm of the object centre, the origin every polarity metric is measured from: the centroid of the object mask where one was named, and of everything segmented where none was. Null for a 2D object.",
    "object_center_y_um": "Y coordinate in µm of the object centre.",
    "object_center_x_um": "X coordinate in µm of the object centre.",
}


def is_object_dir(path: Path) -> bool:
    """True for a folder holding a source image and at least one label/mask volume.

    Called for every directory while scanning, so it stays cheap: one glob of the names, no
    pixel data. A folder that holds objects has no entity files of its own and is walked
    into as usual.

    Blind to which mask bounds the object, so a missing --object-mask is an error at load
    time rather than a folder skipped without a word.
    """
    if not path.is_dir():
        return False
    d = _cached_inspect(str(path))
    return d.source is not None and bool(d.entities)


def find_object_dirs(root: Path) -> List[Path]:
    """Every folder under root that is an object, including root itself."""
    if is_object_dir(root):
        return [root]
    return sorted(d for d in root.rglob("*") if d.is_dir() and is_object_dir(d))


def load_object(object_dir: Path, config: RunConfig | None = None) -> ObjectStack:
    """Every entity volume of one object folder, as one stack.

    A 2D folder becomes a CYX stack, a 3D one CZYX. Nothing else differs between them.
    """
    cfg = config or RunConfig.from_env()
    dataset = discover_dataset(object_dir, cfg.object_mask)
    # With a label map, the names --entities selects are the ones the *split* produces, so
    # there is nothing to match against yet and the filter is applied once it is done.
    dataset = Dataset(
        source=dataset.source,
        object_mask_name=dataset.object_mask_name,
        entities=(dataset.entities if cfg.label_map else
                  select_entities(dataset.entities, cfg.entities, dataset.object_mask_name)),
        voxel_size_um=dataset.voxel_size_um,
    )

    source_shape, source_dtype = _source_header(dataset.source)
    ndim = len(source_shape)

    if cfg.voxel_size_um is not None:
        voxel_size = _config_voxel_size(cfg.voxel_size_um, ndim, object_dir)
        voxel_size_source = "config"
    elif dataset.voxel_size_um is not None:
        # A manifest may state the size outright, which is the only record for a store that
        # does not carry one - and it travels with the crop it describes.
        voxel_size = _config_voxel_size(dataset.voxel_size_um, ndim, object_dir)
        voxel_size_source = "manifest"
    else:
        voxel_size = infer_voxel_size_um_from_source(dataset.source, ndim)
        voxel_size_source = _voxel_size_source(dataset.source)

    # None when no mask was named: nothing bounds the object, so it is measured as it
    # lies and the columns that need a boundary go unfilled.
    object_mask_key = key_for_name(dataset.entities, dataset.object_mask_name)
    volumes = {key: load_volume(entity.source) for key, entity in dataset.entities.items()}

    for key, vol in volumes.items():
        if vol.shape != source_shape:
            raise ValueError(
                f"{object_dir.name}: entity '{dataset.entities[key].name}' has shape {vol.shape}, "
                f"source image has {source_shape}"
            )

    resolve_auto_kinds(volumes, dataset.entities, object_mask_key)
    if cfg.label_map:
        split_label_map(volumes, dataset.entities, cfg.label_map,
                        cfg.label_map_entity, cfg.entities)
        kept = select_entities(dataset.entities, cfg.entities, dataset.object_mask_name)
        for key in [k for k in dataset.entities if k not in kept]:
            del dataset.entities[key]
            volumes.pop(key, None)
        object_mask_key = key_for_name(dataset.entities, dataset.object_mask_name)

    if object_mask_key is not None:
        if cfg.clip:
            inside = volumes[object_mask_key] > 0
            for key in volumes:
                if key != object_mask_key:
                    volumes[key] = clip_to_object_mask(volumes[key], inside)
        volumes = crop_to_object_bbox(volumes, object_mask_key)
    if cfg.auto_label_masks:
        promote_multicomponent_masks(volumes, dataset.entities, object_mask_key)

    keys = _channel_order(dataset)
    stack = _stack_narrowest(volumes, keys, object_dir.name)

    # What the object knows about itself, in the shape the report's object row takes. The
    # extent of each axis is not in here: it is read off the array itself.
    axes = SPATIAL_AXES_3D if ndim == 3 else SPATIAL_AXES_2D
    meta: Dict[str, Any] = {
        "channel_names": [dataset.entities[k].name for k in keys],
        "entity_kinds": [dataset.entities[k].kind for k in keys],
        "entity_files": [_entity_origin(dataset.entities[k]) for k in keys],
        "entity_file_bytes": [_file_size(dataset.entities[k].path) for k in keys],
        "n_entities": len(keys),
        "object_id": object_dir.name,
        "object_mask_name": dataset.object_mask_name,
        # A plane's extent is (Y, X); which axes these are is in dim_order.
        "object_shape": list(stack.shape[1:]),
        "spatial_dims": ndim,
        "voxel_size_source": voxel_size_source,
    }
    meta.update({f"pixel_size_{ax}": float(size) for ax, size in zip(axes, voxel_size)})
    # The origin for every polarity metric, computed once so a per-entity measurement can
    # still measure against it. Read off the stack, since _stack_narrowest has released the
    # source volumes. With a bounding mask it is that mask's centroid; without one, the
    # centre of everything segmented - bare labels have a middle too, and denying them one
    # dropped every polarity column.
    center = (object_center_um(stack[keys.index(object_mask_key)], voxel_size)
              if object_mask_key is not None
              else segmented_center_um(stack, voxel_size))
    if center is not None:
        meta.update({f"object_center_{ax.lower()}_um": value
                     for ax, value in zip(axes, center)})
    logger.info(
        "organella: %s: %d entities, %s %s as %s (%.1f GB), sample size (%s) µm: %s",
        object_dir.name, len(keys), "×".join(str(s) for s in stack.shape[1:]),
        "voxels" if ndim == 3 else "pixels",
        stack.dtype, stack.nbytes / 1024**3, ",".join(axes.lower()),
        ", ".join(f"{v:.4g}" for v in voxel_size),
    )
    return ObjectStack(stack, "C" + axes, meta)

