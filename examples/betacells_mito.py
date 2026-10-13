#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.12"
# dependencies = [
#     "numpy==1.26.4",
#     "torch==2.14.1",
#     "torchvision==0.29.1",
#     "empanada-napari==1.2.4",
#     "scikit-image==0.22.0",
#     "scipy==1.11.4",
#     "tifffile",
#     "zarr==2.17.1",
#     "numcodecs==0.13.1",
#     "s3fs",
#     "pyyaml",
#     "tqdm",
#     "requests",
# ]
#
# [tool.uv]
# # What it was run with. empanada-napari asks for opencv >= 4.11, which it does not need. empanada-dl is
# # left out on purpose: it ships an older copy of the same `empanada` package that overwrites the one in
# # empanada-napari and imports names the current cztile no longer has.
# override-dependencies = ["numpy==1.26.4", "opencv-python==4.7.0.72", "opencv-python-headless==4.7.0.72"]
#
# # Which CUDA build of torch uv installs is chosen from outside (see betacells_slurm.sh): the one on PyPI is
# # built for CUDA 13, which has no kernels for Volta cards such as the V100, and torch then runs on the CPU;
# # the CUDA 12.6 build covers Volta to Hopper (V100, A100, H100, H200), CUDA 13 is for Blackwell (B100, B200).
# ///
"""Mitochondria instances of the seven beta cells, from the raw OpenOrganelle volumes, with empanada's MitoNet.

The published beta-cell mitochondria are a binary mask at 16 nm. This segments the raw 8 nm data
behind them into one label per mitochondrion with MitoNet (empanada), cell by cell, and writes the
result in the 16 nm grid and orientation of the published masks, so it replaces the mask in an
ordinary `organella process` run.

    uv run examples/betacells_mito.py DATA_DIR OUT_DIR [--cells high_c1,low_c2] [--tile 512]

uv builds the environment from the dependencies above on the first run; there is nothing to install.
DATA_DIR is the betacells folder with `{low,high}/<cell>/<cell>_*.tif` (the published masks); where each
cell sits in its OpenOrganelle volume is in `examples/betacells_locations.json`. The raw data is read from
S3 on demand. Tiles of 512 x 512 voxels fit in 4 GB; with a larger GPU use --tile 1024 or more.

Per cell, in OUT_DIR/<group>/<cell>/ (an `organella`-readable folder: the cell's other files are linked in):
  <cell>_mitochondria_labels.tif       one label per mitochondrion of the cell, 16 nm, the published masks' orientation
  <cell>_mitochondria_8nm.tif          the whole box at 8 nm, neighbouring cells included, y as in the published masks
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

    if tile_device and not torch.cuda.is_available():
        # torch falls back to the CPU without a word, and the warning that says why is silenced above;
        # on the CPU a cell takes days, so say it now. A wheel built for a newer CUDA than the driver is the usual cause.
        raise SystemExit(f"torch {torch.__version__} (built for CUDA {torch.version.cuda}) sees no usable GPU. "
                         "Check `nvidia-smi` on this node; --cpu runs on the CPU anyway.")
    config = yaml.safe_load(open(pkg / "configs" / "MitoNet_v1.yaml"))
    config["model"] = str(weights)
    return Engine3d(config, inference_scale=1, use_gpu=tile_device, min_size=500,
                    min_extent=4, save_panoptic=True, confidence_thr=CONFIDENCE, fill_holes_in_segmentation=True)


def read_cell_8nm(loc, cache: Path | None = None, threads: int = 8):
    """The cell's box at 8 nm isotropic, in the volume's orientation, with contrast like the 8-bit volumes.

    The box is read as the stored data, in z-slabs that line up with the stored chunks (a slab that cuts a chunk
    fetches and unpacks it again for every cut), several at a time. Then the contrast mapping is worked out over
    all of it (clip at the 1st and 99th percentile, then mean 128 and standard deviation 43), and the uint8
    volume is made slab by slab, resampling a 6.8 nm volume to 8 nm on the way: nothing larger than a few slabs
    is ever float32. With `cache` the finished volume is kept there and a resumed run reads it back."""
    from concurrent.futures import ThreadPoolExecutor

    import zarr
    from zarr.n5 import N5FSStore

    if cache is not None and cache.exists():
        print("  reading the cell from", cache, flush=True)
        return np.load(cache, mmap_mode="r")
    root = zarr.open(N5FSStore(loc["store"], anon=True), mode="r")
    arr = root[f"{loc['array']}/s1"]
    cz = arr.chunks[0]
    z0, y0, x0 = loc["offset_s0_zyx"]
    nz, ny, nx = loc["size_16nm_zyx"]
    iso = loc["s0_voxel_nm_zyx"][0] == 4.0                # else 6.8 nm slices to be resampled to 8 nm
    y1, x1, ys, xs = y0 // 2, x0 // 2, ny * 2, nx * 2
    zbase, depth = z0 // 2, nz * 2
    stored = depth if iso else min(int(np.ceil(depth * 8 / 6.8)) + 2, arr.shape[0] - zbase)
    sign = -1.0 if loc["volume_contrast_inverted"] else 1.0   # the 16-bit volume is stored with opposite contrast

    raw = np.empty((stored, ys, xs), arr.dtype)
    edges = [zbase] + list(range((zbase // cz + 1) * cz, zbase + stored, cz)) + [zbase + stored]

    def read(r):
        raw[r[0] - zbase:r[1] - zbase] = arr[r[0]:r[1], y1:y1 + ys, x1:x1 + xs]
        return r[1] - r[0]

    t0, done = time.time(), 0
    with ThreadPoolExecutor(threads) as pool:
        for n in pool.map(read, zip(edges[:-1], edges[1:])):
            done += n
            print(f"  read {done}/{stored} slices, {time.time() - t0:.0f} s", flush=True)

    def resampled(z):
        """Output slices [z, z + 64) at 8 nm, float32 and not yet inverted."""
        if iso:
            return raw[z:z + 64].astype(np.float32)
        pos = np.arange(z, min(z + 64, depth)) * 8 / 6.8
        lo = np.floor(pos).astype(int)
        w = (pos - lo).astype(np.float32)[:, None, None]
        return raw[lo].astype(np.float32) * (1 - w) + raw[np.minimum(lo + 1, stored - 1)].astype(np.float32) * w

    lo_, hi_ = np.percentile(raw[::4, ::4, ::4].astype(np.float32) * sign, [1, 99])
    total = total2 = 0.0
    for z in range(0, depth, 64):
        v = np.clip(resampled(z) * sign, lo_, hi_).astype(np.float64)
        total += v.sum()
        total2 += (v * v).sum()
    count = depth * ys * xs
    mean = total / count
    std = float(np.sqrt(total2 / count - mean * mean))

    vol = np.empty((depth, ys, xs), np.uint8)
    for z in range(0, depth, 64):
        v = np.clip(resampled(z) * sign, lo_, hi_)
        vol[z:z + v.shape[0]] = np.clip((v - mean) / std * 43 + 128, 0, 255).astype(np.uint8)
    del raw
    if cache is not None:
        np.save(cache.with_suffix(".part.npy"), vol)
        del vol
        os.replace(cache.with_suffix(".part.npy"), cache)
        return np.load(cache, mmap_mode="r")
    return vol


def segment_tiled(engine, vol, inside_xy, tile, overlap, work: Path, zblock=256, zoverlap=32, merge_iou=0.3):
    """MitoNet on blocks of the volume; instances that two blocks share are one mitochondrion.

    The blocks are xy tiles of `tile` voxels, each cut in z every `zblock` slices. The z cut is not about
    memory: empanada numbers the objects it tracks through a stack with a counter that starts at 1001 and
    is never reused, and past 1999 a label reads as another class and is dropped. A beta cell uses that up
    about 700 slices in, after which nothing more is found. A block of 256 slices stays far below it.

    Block results go to a memory-mapped file with the merge state beside it, so an interrupted run resumes."""
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

    Z, Y, X = vol.shape
    step = tile - overlap
    zstep = zblock - zoverlap
    t0 = time.time()
    def blocks():
        for zi in range(0, max(Z - zoverlap, 1), zstep):
            za = min(zi, max(Z - zblock, 0))
            for yi in range(0, max(Y - overlap, 1), step):
                for xi in range(0, max(X - overlap, 1), step):
                    ya, xa = min(yi, max(Y - tile, 0)), min(xi, max(X - tile, 0))
                    yield za, min(za + zblock, Z), ya, min(ya + tile, Y), xa, min(xa + tile, X)

    boxes = list(blocks())
    for k, (za, zb, ya, yb, xa, xb) in enumerate(boxes):
        if (za, ya, xa) in done or not inside_xy[ya:yb, xa:xb].any():
            continue
        stack, _ = engine.infer_on_axis(np.ascontiguousarray(vol[za:zb, ya:yb, xa:xb]), "xy")
        stack = np.asarray(stack)
        ids = np.unique(stack[stack > 0])
        remap = np.zeros(int(stack.max()) + 1, np.uint32)
        remap[ids] = np.arange(nextid, nextid + len(ids), dtype=np.uint32)
        nextid += len(ids)
        new = remap[stack]
        old = out[za:zb, ya:yb, xa:xb]
        # The zone this block shares with the blocks done before it, and in it the objects each side found.
        # Two labels are one mitochondrion when they cover the same voxels of the zone (IoU of at least 0.5)
        # and each is the other's best match (`merge_iou`). A looser rule chains touching mitochondria into one object.
        zone = np.zeros(old.shape, bool)
        for (z0, z1, y0, y1, x0, x1) in boxes[:k]:
            if (z0, y0, x0) in done:
                zone[max(z0 - za, 0):max(z1 - za, 0), max(y0 - ya, 0):max(y1 - ya, 0),
                     max(x0 - xa, 0):max(x1 - xa, 0)] = True
        roots = np.array([find(i) for i in range(nextid)], dtype=np.int64)
        old_obj = roots[old]                                   # an object that earlier merges joined counts once
        new_zone = np.where(zone, new, 0)
        both = (old_obj > 0) & (new_zone > 0)
        if both.any():
            pairs, counts = np.unique(np.stack([old_obj[both], new_zone[both]]), axis=1, return_counts=True)
            old_size = np.bincount(old_obj[old_obj > 0], minlength=nextid)
            new_size = np.bincount(new_zone[new_zone > 0], minlength=nextid)
            o, n = pairs
            iou = counts / (old_size[o] + new_size[n] - counts)
            best_o, best_n = {}, {}
            for a, b, v in zip(o, n, iou):
                if v > best_o.get(a, (0, 0))[0]:
                    best_o[a] = (v, b)
                if v > best_n.get(b, (0, 0))[0]:
                    best_n[b] = (v, a)
            for a, (v, b) in best_o.items():
                if v >= merge_iou and best_n[b][1] == a:
                    ra, rb = find(int(a)), find(int(b))
                    if ra != rb:
                        parent[rb] = ra
        old[old == 0] = new[old == 0]
        out.flush()
        done.add((za, ya, xa))
        state_path.write_text(json.dumps({"nextid": nextid, "parent": {str(k): int(v) for k, v in parent.items()},
                                          "done": sorted(done)}))
        print(f"  block z{za}-{zb} y{ya} x{xa}: {len(ids)} instances, {time.time() - t0:.0f} s", flush=True)

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


def run_cell(cell, loc, data_dir: Path, out_dir: Path, engine, tile, overlap, zblock=256, zoverlap=32):
    import tifffile

    group = cell.split("_")[0]
    src, dst = data_dir / group / cell, out_dir / group / cell
    dst.mkdir(parents=True, exist_ok=True)
    nz, ny, nx = loc["size_16nm_zyx"]

    work = out_dir / "_work" / cell
    work.mkdir(parents=True, exist_ok=True)
    print(f"{cell}: reading the cell at 8 nm", flush=True)
    vol = read_cell_8nm(loc, work / "vol8.npy")
    # Where the cell is: skip the xy tiles with none of it. The published masks run opposite to the volume in y.
    inside = np.zeros((nz, ny, nx), bool)
    for name in ("membrane", "nucleus"):
        inside |= tifffile.imread(src / f"{cell}_{name}_mask.tif") > 0
    column = np.kron(inside[:, ::-1, :].any(0), np.ones((2, 2), bool))
    column = np.pad(column, ((0, vol.shape[1] - column.shape[0]), (0, vol.shape[2] - column.shape[1])))
    from scipy import ndimage as ndi
    column = ndi.binary_dilation(column, iterations=24)

    inst8 = segment_tiled(engine, vol, column, tile, overlap, work, zblock, zoverlap)
    del vol
    print(f"{cell}: {int(inst8.max())} mitochondria", flush=True)

    tifffile.imwrite(dst / f"{cell}_mitochondria_8nm.tif", inst8[:, ::-1, :], compression="zlib")
    lab = to_16nm(inst8)[:, ::-1, :]
    del inst8
    # The box holds the neighbouring cells too, and MitoNet segments their mitochondria as well; like the published
    # masks, the 16 nm labels are the cell's own. An object that crosses the membrane is cut there.
    lab = np.where(inside, lab, 0)
    ids, inverse = np.unique(lab, return_inverse=True)
    lab = inverse.reshape(lab.shape).astype(np.uint16 if len(ids) < 65535 else np.uint32)
    for f in src.iterdir():                               # the cell's other labels, linked: the folder is a whole cell
        if f.name.endswith("_mitochondria_mask.tif") or "mitochondria_" in f.name and f.name.endswith("8nm.tif"):
            continue
        link = dst / f.name
        if not link.exists():
            os.symlink(f.resolve(), link)
    tifffile.imwrite(dst / f"{cell}_mitochondria_labels.tif", lab, compression="zlib")

    old = (tifffile.imread(src / f"{cell}_mitochondria_mask.tif") > 0) & inside
    new = lab > 0
    dice = 2 * (new & old).sum() / (new.sum() + old.sum())
    print(f"{cell}: {int(lab.max())} instances inside the cell at 16 nm; Dice with the published mask {dice:.3f}", flush=True)
    if dice < 0.6:
        print(f"{cell}: WARNING the result agrees poorly with the published mask (0.8 or more is usual). "
              "Look at where it is empty along z before using it.", flush=True)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path, nargs="?", help="the betacells folder holding low/ and high/")
    ap.add_argument("out_dir", type=Path, nargs="?")
    ap.add_argument("--cells", default="", help="comma list; default: all seven")
    ap.add_argument("--locations", type=Path, default=Path(__file__).with_name("betacells_locations.json"))
    ap.add_argument("--tile", type=int, default=512, help="xy tile edge in 8 nm voxels")
    ap.add_argument("--overlap", type=int, default=64, help="xy overlap of neighbouring tiles")
    ap.add_argument("--zblock", type=int, default=256, help="slices per block; empanada loses objects past ~1000 per stack")
    ap.add_argument("--zoverlap", type=int, default=32)
    ap.add_argument("--cpu", action="store_true", help="no GPU")
    ap.add_argument("--check", action="store_true", help="download the weights, build the engine and exit")
    ap.add_argument("--weights", type=Path, default=Path("MitoNet_v1.pth"))
    args = ap.parse_args()

    if not args.weights.exists():
        print("downloading MitoNet_v1 ->", args.weights)
        urllib.request.urlretrieve(WEIGHTS, args.weights)
    engine = load_engine(args.weights.resolve(), not args.cpu)
    if args.check:
        import torch
        print(f"MitoNet is ready; torch {torch.__version__} for CUDA {torch.version.cuda}; "
              f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}")
        return
    if not (args.data_dir and args.out_dir):
        ap.error("data_dir and out_dir are required")
    locations = json.loads(args.locations.read_text())
    cells = [c for c in args.cells.split(",") if c] or list(locations)
    for cell in cells:
        run_cell(cell, locations[cell], args.data_dir, args.out_dir, engine, args.tile, args.overlap,
                 args.zblock, args.zoverlap)


if __name__ == "__main__":
    main()
