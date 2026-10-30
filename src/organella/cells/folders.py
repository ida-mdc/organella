from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from organella.cells.derive import Cell


def box_string(window: tuple[slice, ...]) -> str:
    return ",".join(f"{s.start}:{s.stop}" for s in window)


def write_cell_folder(folder: Path, cell: Cell, *, store: str, scale: str, source: str, entities: dict, object_name: str,
                      derived_from: dict) -> Path:
    """A folder ``organella process`` reads: a manifest naming the store and the cell's window, and the cell's mask."""
    folder.mkdir(parents=True, exist_ok=True)
    mask_file = f"{object_name}_mask.npz"
    # The coarse block and which of its voxels each working voxel takes: the mask at the working scale is rebuilt a chunk at
    # a time, so a cell is never held whole however fine the scale.
    np.savez_compressed(folder / mask_file, coarse=cell.coarse, **{f"pick{axis}": pick for axis, pick in enumerate(cell.picks)})
    manifest = {
        "store": store, "scale": scale, "crop": box_string(cell.window), "source": source, "entities": entities,
        "masks": {object_name: mask_file}, "object_mask": object_name,
        "derived": {**derived_from, "cell": cell.number, "touches_volume_edge": cell.touches_edge or None,
                    "volume_um3": round(cell.volume_um3, 1)},
    }
    (folder / "source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return folder
