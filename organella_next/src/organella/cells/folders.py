from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile

from organella.cells.derive import Cell


def box_string(window: tuple[slice, ...]) -> str:
    return ",".join(f"{s.start}:{s.stop}" for s in window)


def write_cell_folder(folder: Path, cell: Cell, *, store: str, scale: str, source: str, entities: dict, object_name: str,
                      derived_from: dict) -> Path:
    """A folder ``organella process`` reads: a manifest naming the store and the cell's window, and the cell's mask."""
    folder.mkdir(parents=True, exist_ok=True)
    mask_file = f"{object_name}_mask.tif"
    tifffile.imwrite(folder / mask_file, cell.mask.astype(np.uint8), compression="zlib")
    manifest = {
        "store": store, "scale": scale, "crop": box_string(cell.window), "source": source, "entities": entities,
        "masks": {object_name: mask_file}, "object_mask": object_name,
        "derived": {**derived_from, "cell": cell.number, "touches_volume_edge": cell.touches_edge or None,
                    "volume_um3": round(cell.volume_um3, 1)},
    }
    (folder / "source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return folder
