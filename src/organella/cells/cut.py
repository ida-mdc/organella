from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from organella.cells.derive import Cell, cells_at_scale, derive_cell_labels
from organella.cells.folders import box_string, write_cell_folder
from organella.sources.arrays import open_store_array
from organella.sources.store_metadata import voxel_size_from_store


def declared_entities(named: Mapping[str, str], instances: str | None, mask_names: str | None) -> dict:
    """Entities as a manifest declares them: a bare array, or ``{array, kind}`` where the structure is said to be a set of
    instances or one region. Said once here, so it is the same in every cell: left to be read off each cell's pixels, a
    structure with a single instance in one cell is a mask there and a label in the next, and a group splits it in two."""
    kinds: dict[str, str] = {}
    for given, kind, flag in ((instances, "label", "--instances"), (mask_names, "mask", "--masks")):
        for name in (n.strip() for n in (given or "").split(",") if n.strip()):
            if name not in named:
                raise ValueError(f"{flag} names '{name}', which is not one of the --entity names")
            if kinds.setdefault(name, kind) != kind:
                raise ValueError(f"'{name}' is named as both instances and a mask")
    return {name: ({"array": array, "kind": kinds[name]} if name in kinds else array) for name, array in named.items()}


def cut_cells(store: str, out_dir: Path, *, source: str, entities: Mapping[str, str], instances: str | None = None,
              mask_names: str | None = None, membrane: str, seed: str, outside: str | None = None, scale: str,
              mask_scale: str | None = None, object_name: str = "cell", min_seed_um3: float = 40.0,
              max_membrane_um: float | None = None, prefix: str = "cell", wall_voxels: int = 3, drop_edge: bool = False,
              say: Callable[[str], None] = lambda _: None) -> list[Cell]:
    """Cut the cells of a chunked volume apart, one object folder each.

    Nothing is downloaded beyond the three label arrays at ``mask_scale``, which is the scale that has to fit in memory;
    each cell's folder holds a manifest and its mask, and ``process`` reads the pixels it needs from the store itself.
    """
    declared = declared_entities(entities, instances, mask_names)
    coarse = mask_scale or scale
    read = lambda array, level: open_store_array(store, f"{array.strip('/')}/{level}").compute()
    coarse_um = voxel_size_from_store(store, f"{seed.strip('/')}/{coarse}")
    work_um = voxel_size_from_store(store, f"{source.strip('/')}/{scale}")
    if coarse_um is None or work_um is None:
        raise ValueError("the arrays carry no voxel size (a 'transform' attribute) to cut cells at")
    work_shape = open_store_array(store, f"{source.strip('/')}/{scale}").shape
    say(f"reading {membrane}, {seed}" + (f", {outside}" if outside else "") + f" at {coarse} ({coarse_um.um[1] * 1000:.0f} nm)")
    labels = derive_cell_labels(read(membrane, coarse), read(outside, coarse) if outside else None, read(seed, coarse),
                                coarse_um.um, min_seed_um3=min_seed_um3, wall_voxels=wall_voxels, max_membrane_um=max_membrane_um or None)
    found = cells_at_scale(labels, coarse_um.um, work_um.um, work_shape, drop_edge=drop_edge)
    if not found:
        raise ValueError(f"No cell found: no {seed} blob of at least {min_seed_um3:g} µm³ with a wall around it. "
                         "Lower --min-seed-um3, or check the three arrays are the same crop.")
    derived = {"membrane": membrane, "outside": outside, "seed": seed, "mask_scale": coarse, "min_seed_um3": min_seed_um3,
               "wall_voxels": wall_voxels, "max_membrane_um": max_membrane_um or None, "method": "organella.cells.derive_cell_labels"}
    for cell in found:
        folder = write_cell_folder(out_dir / f"{prefix}_{cell.number:02d}", cell, store=store, scale=scale, source=source.strip("/"),
                                   entities=declared, object_name=object_name, derived_from=derived)
        say(f"{folder.name}: {cell.volume_um3:8.0f} µm³  window {box_string(cell.window)}"
            + (f"  cut off at {cell.touches_edge}" if cell.touches_edge else "  whole"))
    say(f"{len(found)} cell(s) -> {out_dir}")
    return found
