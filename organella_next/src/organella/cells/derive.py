"""Cutting the cells of one volume apart, from the membrane around them.

A published segmentation seldom ships a filled cell: it ships the plasma membrane as a thin surface, the extracellular
space as another class, and the nucleus. None of those is the cell, and a surface cut by the edge of the imaged volume
cannot be hole-filled, since it is open there. What a person does instead is read which side each thing is on:

1. The membrane (thickened by ``wall_voxels``, to close the gaps a predicted surface has) and the extracellular space are
   walls. Two voxels left one cell of 11 open to empty background; three closed it. The edge of the volume is not an exit.
2. Free space is flooded from each nucleus. What a nucleus can reach without crossing a wall is the inside of its cell.
3. Cells that touch with no membrane between them are cut apart at their necks, by a watershed on the distance transform
   that grows from each nucleus.
4. Optionally, nothing further than ``max_membrane_um`` from a membrane is kept. Off by default: it clips the middle of a
   cell the volume cuts off, which is far from any membrane.

Every step only removes space from what the walls bound. A cell is derived, not published, and says so in its manifest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import edt
import numpy as np
from scipy import ndimage as ndi
from skimage.segmentation import watershed


def derive_cell_labels(membrane: np.ndarray, outside: np.ndarray | None, seed: np.ndarray, voxel_um: Sequence[float], *,
                       min_seed_um3: float = 40.0, wall_voxels: int = 3, max_membrane_um: float | None = None) -> np.ndarray:
    """One id per cell, 0 elsewhere, from the cell with the largest nucleus down.

    A nucleus smaller than ``min_seed_um3`` is not a cell of its own, which keeps the nucleolus, chromatin and debris
    classes from becoming cells.
    """
    membrane = membrane > 0
    outside_mask = (outside > 0) if outside is not None else np.zeros_like(membrane)
    voxel_volume = float(np.prod(voxel_um))
    anisotropy = tuple(float(v) for v in voxel_um)

    seed_labels, _ = ndi.label(seed > 0)
    sizes = np.bincount(seed_labels.ravel())
    sizes[0] = 0
    wanted = [i for i in np.argsort(sizes)[::-1] if sizes[i] * voxel_volume >= min_seed_um3]
    if not wanted:
        return np.zeros(membrane.shape, np.uint16)

    structure = ndi.generate_binary_structure(membrane.ndim, membrane.ndim)
    walls = ndi.binary_dilation(membrane, structure=structure, iterations=wall_voxels) | outside_mask
    free_labels, _ = ndi.label(~walls)
    reached = np.isin(free_labels, np.unique(free_labels[np.isin(seed_labels, wanted) & (free_labels > 0)]))
    cell = ndi.binary_dilation(reached, structure=structure, iterations=wall_voxels) & ~outside_mask   # the band the walls took
    cell |= membrane & ndi.binary_dilation(cell, structure=structure)   # the membrane is the cell's own surface
    del walls, free_labels, reached

    if max_membrane_um is not None and membrane.any():
        cell &= edt.edt(~membrane, anisotropy=anisotropy, black_border=False) <= max_membrane_um

    markers = np.zeros(membrane.shape, np.int32)
    for number, i in enumerate(wanted, 1):
        markers[(seed_labels == i) & cell] = number
    depth = edt.edt(cell, anisotropy=anisotropy, black_border=False)
    return watershed(-depth, markers, mask=cell).astype(np.uint16)


@dataclass
class Cell:
    """One cell cut from a volume, at the scale the report will be measured at."""

    number: int
    window: tuple[slice, ...]     # in working-scale voxels, array order
    coarse: np.ndarray            # bool, the cell's box of the coarse labels
    picks: tuple[np.ndarray, ...]  # per axis, the coarse voxel each working voxel of the window takes
    touches_edge: str = ""        # axes (z, y, x) whose volume face it reaches
    volume_um3: float = 0.0

    @property
    def mask(self) -> np.ndarray:
        """The mask at the working scale, in the window's shape. It is as large as the window: only a small volume or a
        test should ask for it, since the folder written for a cell holds the coarse block and these picks instead."""
        return self.coarse[np.ix_(*self.picks)]


def cells_at_scale(labels: np.ndarray, coarse_um: Sequence[float], work_um: Sequence[float], work_shape: Sequence[int], *,
                   drop_edge: bool = False) -> list[Cell]:
    """Each cell's window and mask carried to the working scale.

    Every working voxel takes the value of the coarse voxel whose centre region contains it, so the ratio between the two
    need not be a whole number, and a boundary is moved by at most one coarse voxel and is never smoother than the labels
    it came from.
    """
    ratio = np.array(work_um, float) / np.array(coarse_um, float)       # coarse voxels per fine one
    cells: list[Cell] = []
    for number in range(1, int(labels.max()) + 1):
        found = labels == number
        if not found.any():
            continue
        where = np.argwhere(found)
        low, high = where.min(0), where.max(0) + 1
        edge = "".join(a for a, lo, hi, n in zip("zyx", low, high, labels.shape) if lo == 0 or hi == n)
        if drop_edge and edge:
            continue
        window, picks = [], []
        for axis in range(labels.ndim):
            start = int(round(low[axis] / ratio[axis]))
            stop = min(int(round(high[axis] / ratio[axis])), int(work_shape[axis]))
            window.append(slice(start, stop))
            at = np.floor((np.arange(start, stop) + 0.5) * ratio[axis]).astype(int)
            picks.append(np.clip(at, low[axis], high[axis] - 1) - low[axis])
        coarse = found[tuple(slice(int(lo), int(hi)) for lo, hi in zip(low, high))]
        # How many working voxels take each coarse voxel, so the volume is known without building the mask.
        weights = [np.bincount(pick, minlength=n) for pick, n in zip(picks, coarse.shape)]
        voxels = float(np.einsum("ijk,i,j,k->", coarse.astype(np.float64), *weights)) if coarse.ndim == 3 else \
            float(np.einsum("ij,i,j->", coarse.astype(np.float64), *weights))
        cells.append(Cell(number, tuple(window), coarse, tuple(picks), edge, voxels * float(np.prod(work_um))))
    return cells
