#!/usr/bin/env python
"""Mitochondria instances of the seven beta cells, from the raw OpenOrganelle volumes, with empanada's MitoNet.

The published beta-cell mitochondria are a binary mask at 16 nm. This segments the raw 8 nm data
behind them into one label per mitochondrion with MitoNet (empanada), cell by cell, and writes the
result in the 16 nm grid and orientation of the published masks, so it replaces the mask in an
ordinary `organella process` run.

    python examples/betacells_mito.py DATA_DIR OUT_DIR [--cells high_c1,low_c2] [--tile 512]

DATA_DIR is the betacells folder with `{low,high}/<cell>/<cell>_*.tif` (the published masks); where each
cell sits in its OpenOrganelle volume is in `examples/betacells_locations.json`. The raw data is read from
S3 on demand. Needs: empanada-napari (which brings torch), numpy<2, scipy, scikit-image, tifffile, zarr<3,
s3fs. Tiles of 512 x 512 voxels fit in 4 GB; with a larger GPU use --tile 1024 or more.

Per cell, in OUT_DIR/<group>/<cell>/ (an `organella`-readable folder: the cell's other files are linked in):
  <cell>_mitochondria_labels.tif       one label per mitochondrion, 16 nm, the published masks' orientation
  <cell>_mitochondria_8nm.tif          the same at 8 nm, in the volume's orientation with y flipped back
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
import urllib.request
import warnings
from pathlib import Path

import numpy as np

warnings.simplefilter("ignore")

WEIGHTS = "https://zenodo.org/record/6861565/files/MitoNet_v1.pth?download=1"
CONFIDENCE = 0.1            # 0.1 against the published mask scored best on a held-out crop; 0.3 loses small ones


def load_engine(weights: Path, tile_device: bool):
    """empanada's 3D engine. The library imports napari at load and calls cc3d in ways this environment
    does not need, so both are replaced by the two things they stand for."""
    import importlib.util

    import torch
    import yaml

    pkg = Path(importlib.util.find_spec("empanada_napari").submodule_search_locations[0])
    stub = types.ModuleType("empanada_napari")
    stub.__path__ = [str(pkg)]
    sys.modules["empanada_napari"] = stub
    for name in ("napari", "napari.qt", "napari.qt.threading"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["napari.qt.threading"].thread_worker = lambda *a, **k: (lambda f: f)

    import empanada.inference.filters as filters
    from skimage import measure

    filters.connected_components = lambda seg: measure.label(seg, connectivity=seg.ndim).astype(np.uint32)
    from empanada_napari.inference import Engine3d

    config = yaml.safe_load(open(pkg / "configs" / "MitoNet_v1.yaml"))
    config["model"] = str(weights)
    return Engine3d(config, inference_scale=1, use_gpu=tile_device and torch.cuda.is_available(), min_size=500,
                    min_extent=4, save_panoptic=True, confidence_thr=CONFIDENCE, fill_holes_in_segmentation=True)


def read_cell_8nm(loc):
    """The cell's box at 8 nm isotropic, in the volume's orientation, with contrast like the 8-bit volumes."""
    import zarr
    from zarr.n5 import N5FSStore

    root = zarr.open(N5FSStore(loc["store"], anon=True), mode="r")
    arr = root[f"{loc['array']}/s1"]
    z0, y0, x0 = loc["offset_s0_zyx"]
    nz, ny, nx = loc["size_16nm_zyx"]
    zstep = loc["s0_voxel_nm_zyx"][0]
    y1, x1, ys, xs = y0 // 2, x0 // 2, ny * 2, nx * 2
    if zstep == 4.0:                                      # already isotropic
        vol = np.asarray(arr[z0 // 2:z0 // 2 + nz * 2, y1:y1 + ys, x1:x1 + xs]).astype(np.float32)
    else:                                                 # 6.8 nm slices -> 8 nm
        zs = z0 // 2
        raw = np.asarray(arr[zs:zs + int(np.ceil(nz * 2 * 8 / 6.8)) + 2, y1:y1 + ys, x1:x1 + xs]).astype(np.float32)
        pos = np.arange(nz * 2) * 8 / 6.8
        lo = np.floor(pos).astype(int)
        w = (pos - lo).astype(np.float32)
        lo1 = np.minimum(lo + 1, raw.shape[0] - 1)
        vol = np.empty((nz * 2,) + raw.shape[1:], np.float32)
        for i in range(nz * 2):
            vol[i] = raw[lo[i]] * (1 - w[i]) + raw[lo1[i]] * w[i]
        del raw
    if loc["volume_contrast_inverted"]:                   # the 16-bit volume is stored with opposite contrast
        vol = -vol
    lo_, hi_ = np.percentile(vol[::4, ::4, ::4], [1, 99])
    vol = np.clip(vol, lo_, hi_)
    return np.clip((vol - vol.mean()) / vol.std() * 43 + 128, 0, 255).astype(np.uint8)


def segment_tiled(engine, vol, inside_xy, tile, overlap, work: Path):
    """MitoNet on xy tiles of the whole z stack; instances that two tiles share are one mitochondrion.

    Tile results go to a memory-mapped file with the merge state beside it, so an interrupted run resumes."""
    from scipy import ndimage as ndi

    raw_path, state_path = work / "raw.npy", work / "state.json"
    resume = raw_path.exists() and state_path.exists()
    out = np.lib.format.open_memmap(raw_path, mode="r+" if resume else "w+", dtype=np.uint32, shape=vol.shape)
    state = json.loads(state_path.read_text()) if resume else {"nextid": 1, "parent": {}, "done": []}
    nextid = state["nextid"]
    parent = {int(k): v for k, v in state["parent"].items()}
    done = set(map(tuple, state["done"]))

    def find(i):
        while parent.get(i, i) != i:
            parent[i] = parent.get(parent[i], parent[i])
            i = parent[i]
        return i

    _, Y, X = vol.shape
    step = tile - overlap
    t0 = time.time()
    for yi in range(0, max(Y - overlap, 1), step):
        for xi in range(0, max(X - overlap, 1), step):
            ya, xa = min(yi, max(Y - tile, 0)), min(xi, max(X - tile, 0))
            yb, xb = min(ya + tile, Y), min(xa + tile, X)
            if (ya, xa) in done or not inside_xy[ya:yb, xa:xb].any():
                continue
            stack, _ = engine.infer_on_axis(np.ascontiguousarray(vol[:, ya:yb, xa:xb]), "xy")
            stack = np.asarray(stack)
            ids = np.unique(stack[stack > 0])
            remap = np.zeros(int(stack.max()) + 1, np.uint32)
            remap[ids] = np.arange(nextid, nextid + len(ids), dtype=np.uint32)
            nextid += len(ids)
            new = remap[stack]
            old = out[:, ya:yb, xa:xb]
            both = (old > 0) & (new > 0)
            if both.any():
                pairs, counts = np.unique(np.stack([old[both], new[both]]), axis=1, return_counts=True)
                size = np.bincount(new[new > 0], minlength=nextid)
                for (o, n), c in zip(pairs.T, counts):
                    if c >= 50 and c >= 0.2 * min(size[n], (old == o).sum()):
                        ro, rn = find(int(o)), find(int(n))
                        if ro != rn:
                            parent[rn] = ro
            old[old == 0] = new[old == 0]
            out.flush()
            done.add((ya, xa))
            state_path.write_text(json.dumps({"nextid": nextid, "parent": {str(k): int(v) for k, v in parent.items()},
                                              "done": sorted(done)}))
            print(f"  tile y{ya} x{xa}: {len(ids)} instances, {time.time() - t0:.0f} s", flush=True)

    lut = np.arange(nextid, dtype=np.uint32)
    for i in range(1, nextid):
        lut[i] = find(i)
    roots = np.unique(lut[1:])
    final = np.zeros(nextid, np.uint32)
    final[roots] = np.arange(1, len(roots) + 1, dtype=np.uint32)
    lut = final[lut]
    dtype = np.uint16 if len(roots) < 65535 else np.uint32
    result = np.empty(vol.shape, dtype)
    for z in range(0, vol.shape[0], 64):                  # the relabelling in slabs: it never holds two full copies
        result[z:z + 64] = lut[out[z:z + 64]]
    del out
    return result


def to_16nm(inst8):
    """A 16 nm voxel is a mitochondrion when at least half of its eight 8 nm voxels are; it takes the largest label."""
    nz, ny, nx = (s // 2 for s in inst8.shape)
    blocks = inst8.reshape(nz, 2, ny, 2, nx, 2)
    lab = blocks.max((1, 3, 5))
    lab[(blocks > 0).sum((1, 3, 5)) < 4] = 0
    _, inverse = np.unique(lab, return_inverse=True)
    lab = inverse.reshape(lab.shape)
    return lab.astype(np.uint16 if lab.max() < 65535 else np.uint32)


def run_cell(cell, loc, data_dir: Path, out_dir: Path, engine, tile, overlap):
    import tifffile

    group = cell.split("_")[0]
    src, dst = data_dir / group / cell, out_dir / group / cell
    dst.mkdir(parents=True, exist_ok=True)
    nz, ny, nx = loc["size_16nm_zyx"]

    print(f"{cell}: reading the cell at 8 nm", flush=True)
    vol = read_cell_8nm(loc)
    # Where the cell is: skip the xy tiles with none of it. The published masks run opposite to the volume in y.
    inside = np.zeros((nz, ny, nx), bool)
    for name in ("membrane", "nucleus"):
        inside |= tifffile.imread(src / f"{cell}_{name}_mask.tif") > 0
    column = np.kron(inside[:, ::-1, :].any(0), np.ones((2, 2), bool))
    column = np.pad(column, ((0, vol.shape[1] - column.shape[0]), (0, vol.shape[2] - column.shape[1])))
    from scipy import ndimage as ndi
    column = ndi.binary_dilation(column, iterations=24)

    work = out_dir / "_work" / cell
    work.mkdir(parents=True, exist_ok=True)
    inst8 = segment_tiled(engine, vol, column, tile, overlap, work)
    del vol
    print(f"{cell}: {int(inst8.max())} mitochondria", flush=True)

    tifffile.imwrite(dst / f"{cell}_mitochondria_8nm.tif", inst8[:, ::-1, :], compression="zlib")
    lab = to_16nm(inst8)[:, ::-1, :]
    del inst8
    for f in src.iterdir():                               # the cell's other labels, linked: the folder is a whole cell
        if f.name.endswith("_mitochondria_mask.tif") or "mitochondria_" in f.name and f.name.endswith("8nm.tif"):
            continue
        link = dst / f.name
        if not link.exists():
            os.symlink(f.resolve(), link)
    tifffile.imwrite(dst / f"{cell}_mitochondria_labels.tif", lab, compression="zlib")

    old = tifffile.imread(src / f"{cell}_mitochondria_mask.tif") > 0
    new = lab > 0
    print(f"{cell}: {int(lab.max())} instances at 16 nm; Dice with the published mask "
          f"{2 * (new & old).sum() / (new.sum() + old.sum()):.3f}", flush=True)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path, help="the betacells folder holding low/ and high/")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--cells", default="", help="comma list; default: all seven")
    ap.add_argument("--locations", type=Path, default=Path(__file__).with_name("betacells_locations.json"))
    ap.add_argument("--tile", type=int, default=512, help="xy tile edge in 8 nm voxels")
    ap.add_argument("--overlap", type=int, default=64)
    ap.add_argument("--cpu", action="store_true", help="no GPU")
    ap.add_argument("--weights", type=Path, default=Path("MitoNet_v1.pth"))
    args = ap.parse_args()

    if not args.weights.exists():
        print("downloading MitoNet_v1 ->", args.weights)
        urllib.request.urlretrieve(WEIGHTS, args.weights)
    locations = json.loads(args.locations.read_text())
    cells = [c for c in args.cells.split(",") if c] or list(locations)
    engine = load_engine(args.weights.resolve(), not args.cpu)
    for cell in cells:
        run_cell(cell, locations[cell], args.data_dir, args.out_dir, engine, args.tile, args.overlap)


if __name__ == "__main__":
    main()
