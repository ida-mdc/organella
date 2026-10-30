"""A small batch to measure, for the install job of CI: ``python tests/synthetic.py batch``.

Four objects under ``batch/control/``, each a plasma membrane mask (``pm``, the object mask), a nucleus mask and
labelled mitochondria, with the voxel size in the ImageJ metadata of ``sample.tif``. Needs only numpy and tifffile,
which the wheel installs, so it runs in an environment holding nothing but the installed package.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile

SHAPE = (48, 96, 96)
VOXEL_UM = (0.1, 0.02, 0.02)


def write_object(folder: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    z, y, x = np.indices(SHAPE)
    pm = np.zeros(SHAPE, np.uint8)
    pm[4:44, 8:88, 8:88] = 1
    nucleus = (((z - 24) ** 2 + (y - 48) ** 2 + (x - 30) ** 2) <= 10 ** 2).astype(np.uint8)
    mito = np.zeros(SHAPE, np.uint16)
    for label in range(1, 12):
        cz, cy, cx = (int(rng.integers(8, n - 8)) for n in SHAPE)
        mito[cz - 3:cz + 3, cy - 5:cy + 5, cx - 5:cx + 5] = label
    mito[(pm == 0) | (nucleus > 0)] = 0
    folder.mkdir(parents=True, exist_ok=True)
    zs, ys, xs = VOXEL_UM
    tifffile.imwrite(folder / "sample.tif", np.zeros(SHAPE, np.uint8), imagej=True, resolution=(1 / xs, 1 / ys),
                     metadata={"spacing": zs, "unit": "micron"})
    for name, array in (("pm_mask", pm), ("nucleus_mask", nucleus), ("mito_label", mito)):
        tifffile.imwrite(folder / f"sample_{name}.tif", array)


def make_batch(root: Path) -> None:
    for seed, name in enumerate(("object_a", "object_b", "object_c", "object_d")):
        write_object(root / "control" / name, seed)


if __name__ == "__main__":
    if sys.argv[1:2] != ["batch"]:
        raise SystemExit("usage: python tests/synthetic.py batch")
    make_batch(Path("batch"))
    print("wrote batch/control/object_{a,b,c,d}")
