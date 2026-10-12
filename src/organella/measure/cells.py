"""Cutting the cells of one volume apart, from the membrane around them.

A published segmentation seldom ships a filled cell: it ships the plasma membrane as a thin
surface, the extracellular space as another class, and the nucleus. None of those is the
cell, and a surface that is cut by the edge of the imaged volume cannot be hole-filled -
it is open there. What a person does instead is read which side each thing is on, and that
is what :func:`derive_cell_labels` does:

1. The membrane (thickened by ``wall_voxels``, to close the gaps a predicted surface has)
   and the extracellular space are walls. Two voxels left one cell of 11 open to empty
   background, which is 5,789 µm³ of a Jurkat cell that is 600; three closed it. The edge of the volume is not an exit, only the end of the array.
2. Free space is flooded from each nucleus. What a nucleus can reach without crossing a
   wall is the inside of its cell.
3. Cells that touch with no membrane between them are cut apart at their necks, by a
   watershed on the distance transform that grows from each nucleus.
4. Optionally, nothing further than ``max_membrane_um`` from a membrane is kept. It is off:
   it did nothing once the wall was thick enough, and it clips the middle of a cell the
   volume cuts off, which is far from any membrane.

Every step only removes space from what the walls bound; no boundary is drawn that the
labels do not contain. A cell is derived, not published, and says so in its manifest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

logger = logging.getLogger(__name__)


def derive_cell_labels(
    membrane: np.ndarray,
    outside: Optional[np.ndarray],
    seed: np.ndarray,
    voxel_um: Tuple[float, ...],
    *,
    min_seed_um3: float = 40.0,
    wall_voxels: int = 3,
    max_membrane_um: float | None = None,
) -> np.ndarray:
    """One id per cell, 0 elsewhere, ordered from the cell with the largest nucleus down.

    ``membrane``, ``outside`` (optional) and ``seed`` are the three labels above, read at
    one scale; a nucleus smaller than ``min_seed_um3`` is not a cell of its own, which is
    what keeps the nucleolus, chromatin and debris classes from becoming cells.
    """
    import edt
    from skimage.segmentation import watershed

    membrane = membrane > 0
    outside_mask = (outside > 0) if outside is not None else np.zeros_like(membrane)
    voxel_volume = float(np.prod(voxel_um))

    seed_labels, _ = ndi.label(seed > 0)
    sizes = np.bincount(seed_labels.ravel())
    sizes[0] = 0
    wanted = [i for i in np.argsort(sizes)[::-1] if sizes[i] * voxel_volume >= min_seed_um3]
    if not wanted:
        return np.zeros(membrane.shape, np.uint16)

    structure = ndi.generate_binary_structure(membrane.ndim, membrane.ndim)
    walls = ndi.binary_dilation(membrane, structure=structure, iterations=wall_voxels) | outside_mask
    free_labels, _ = ndi.label(~walls)
    seeded = np.isin(seed_labels, wanted)
    reached = np.isin(free_labels, np.unique(free_labels[seeded & (free_labels > 0)]))
    # Hand back the band the walls took, on the cell's side only.
    cell = ndi.binary_dilation(reached, structure=structure, iterations=wall_voxels) & ~outside_mask
    # The membrane is the cell's own surface: the layer of it the cell now touches is in.
    cell |= membrane & ndi.binary_dilation(cell, structure=structure)
    del walls, free_labels, reached

    if max_membrane_um is not None and membrane.any():
        away = edt.edt(~membrane, anisotropy=tuple(float(v) for v in voxel_um), black_border=False)
        cell &= away <= max_membrane_um
        del away

    markers = np.zeros(membrane.shape, np.int32)
    for k, i in enumerate(wanted, 1):
        markers[(seed_labels == i) & cell] = k
    depth = edt.edt(cell, anisotropy=tuple(float(v) for v in voxel_um), black_border=False)
    labels = watershed(-depth, markers, mask=cell)
    return labels.astype(np.uint16)


@dataclass
class Cell:
    """One cell cut from a volume, at the scale the report will be measured at."""
    number: int
    window: Tuple[slice, ...]                  # in working-scale voxels, array order
    mask: np.ndarray                           # bool, the window's shape
    touches_edge: str = ""                     # axes (z, y, x) whose volume face it reaches
    volume_um3: float = 0.0
    extra: Dict[str, float] = field(default_factory=dict)


def cells_at_scale(
    labels: np.ndarray,
    coarse_um: Tuple[float, ...],
    work_um: Tuple[float, ...],
    work_shape: Tuple[int, ...],
    *,
    drop_edge: bool = False,
) -> List[Cell]:
    """Each cell's window and mask carried to the working scale.

    ``labels`` is at the coarse scale the derivation ran at. Every working voxel takes the
    value of the coarse voxel whose centre-region contains it, so the ratio between the two
    need not be a whole number - a pyramid downsampled by a different factor in z than in
    x and y is common - and a boundary is moved by at most one coarse voxel and is never
    smoother than the labels it came from.
    """
    ratio = np.array(work_um, float) / np.array(coarse_um, float)       # coarse voxels per fine one
    cells: List[Cell] = []
    for number in range(1, int(labels.max()) + 1):
        found = labels == number
        if not found.any():
            continue
        idx = np.argwhere(found)
        lo, hi = idx.min(0), idx.max(0) + 1
        edge = "".join(a for a, l, h, n in zip("zyx", lo, hi, labels.shape) if l == 0 or h == n)
        if drop_edge and edge:
            continue
        window, picks = [], []
        for axis in range(labels.ndim):
            start = int(round(lo[axis] / ratio[axis]))
            stop = min(int(round(hi[axis] / ratio[axis])), int(work_shape[axis]))
            window.append(slice(start, stop))
            at = np.floor((np.arange(start, stop) + 0.5) * ratio[axis]).astype(int)
            picks.append(np.clip(at, lo[axis], hi[axis] - 1) - lo[axis])
        block = found[tuple(slice(int(l), int(h)) for l, h in zip(lo, hi))]
        block = block[np.ix_(*picks)]
        cells.append(Cell(number=number, window=tuple(window), mask=block, touches_edge=edge,
                          volume_um3=float(block.sum()) * float(np.prod(work_um))))
    return cells


def box_string(window: Tuple[slice, ...]) -> str:
    return ",".join(f"{s.start}:{s.stop}" for s in window)


def write_cell_folder(
    folder: Path,
    cell: Cell,
    *,
    store: str,
    scale: str,
    source: str,
    entities: Dict[str, object],
    object_name: str,
    derived_from: Dict[str, object],
) -> Path:
    """A folder an ordinary ``organella process`` reads: a manifest and the cell's mask."""
    import tifffile

    folder.mkdir(parents=True, exist_ok=True)
    mask_file = f"{object_name}_mask.tif"
    tifffile.imwrite(folder / mask_file, cell.mask.astype(np.uint8), compression="zlib")
    manifest = {
        "store": store,
        "scale": scale,
        "crop": box_string(cell.window),
        "source": source,
        "entities": entities,
        "masks": {object_name: mask_file},
        "object_mask": object_name,
        "derived": {
            **derived_from,
            "cell": cell.number,
            "touches_volume_edge": cell.touches_edge or None,
            "volume_um3": round(cell.volume_um3, 1),
        },
    }
    (folder / "source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return folder
