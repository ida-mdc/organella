#!/usr/bin/env python
"""Endoplasmic reticulum of the seven beta cells, at the full 4 nm resolution, with ASEM's pretrained model.

The beta-cell masks are 16 nm; the raw volumes behind them are on OpenOrganelle at 4 nm, and the
ER is a thin tubular network that the 16 nm data cannot resolve. ASEM (Kirchhausen lab, `incasem`)
publishes a 3D U-Net for the ER of high-pressure-frozen FIB-SEM at 4 x 4 x 4 nm. This script runs
that network over each cell and writes the ER as an `er` mask beside the cell's other labels.

    python examples/betacells_er.py DATA_DIR OUT_DIR [--cells high_c1,low_c2] [--device cuda]

DATA_DIR is the betacells folder with `{low,high}/<cell>/<cell>_membrane_mask.tif`; where each cell sits in
its OpenOrganelle volume is in `examples/betacells_locations.json` (--locations). Everything else is read
from S3 on demand.
Needs: torch, numpy, scipy, zarr<3, s3fs, scikit-image, tifffile. A GPU with 16 GB or more takes
about 20-40 minutes a cell with the default block; --block-k 12 fits in 4 GB, ~10x slower.

Written per cell, in OUT_DIR:
  <cell>_er_4nm.zarr   uint8 0/1, the cell's box at 4 nm isotropic, in the volume's orientation
  <cell>_er_mask.tif   uint8 0/1 at the 16 nm of the published masks, in their orientation
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
import warnings
from pathlib import Path

import numpy as np

warnings.simplefilter("ignore")

WEIGHTS = "https://asem-project.s3.amazonaws.com/models/pretrained_checkpoints/model_checkpoint_1669_er_HPF.pt"


# ── the network: ASEM's U-Net (valid convolutions, 3 x 2 downsampling, 32 base feature maps) ──

def build_model():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class ConvPass(nn.Module):
        def __init__(self, i, o):
            super().__init__()
            self.conv_pass = nn.Sequential(nn.Conv3d(i, o, 3), nn.ReLU(), nn.Conv3d(o, o, 3), nn.ReLU())

        def forward(self, x):
            return self.conv_pass(x)

    class Final(nn.Module):
        def __init__(self, i, o):
            super().__init__()
            self.conv_pass = nn.Sequential(nn.Conv3d(i, o, 3), nn.Identity())

        def forward(self, x):
            return self.conv_pass(x)

    def crop(x, shape):
        o = [(a - b) // 2 for a, b in zip(x.shape[2:], shape)]
        return x[:, :, o[0]:o[0] + shape[0], o[1]:o[1] + shape[1], o[2]:o[2] + shape[2]]

    class Core(nn.Module):
        def __init__(self, nf=32, inc=2, levels=4):
            super().__init__()
            self.levels = levels
            ch = [nf * inc ** i for i in range(levels)]
            self.l_conv = nn.ModuleList([ConvPass(1 if i == 0 else ch[i - 1], ch[i]) for i in range(levels)])
            self.r_conv = nn.ModuleList([nn.ModuleList([ConvPass(ch[i] + ch[i + 1], ch[i]) for i in range(levels - 1)])])

        def forward(self, x):
            left = []
            for i in range(self.levels):
                x = self.l_conv[i](x)
                left.append(x)
                if i < self.levels - 1:
                    x = F.max_pool3d(x, 2)
            for i in range(self.levels - 2, -1, -1):
                up = F.interpolate(x, scale_factor=2, mode="nearest")
                x = self.r_conv[0][i](torch.cat([crop(left[i], up.shape[2:]), up], 1))
            return x

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.unet = Core()
            self.final_conv_pass = Final(32, 2)

        def forward(self, x):
            return self.final_conv_pass(self.unet(x))

    return Net()


def load_model(path: Path, device: str):
    import torch

    state = torch.load(path, map_location="cpu", weights_only=True)["model_state_dict"]
    model = build_model()
    model.load_state_dict(state)
    return model.eval().to(device)


def in_size(k):          # input edge that gives an output edge of 8k - 30
    return 8 * k + 60


def out_size(k):
    return 8 * k - 30


# ── reading a cell's box at 4 nm isotropic from the OpenOrganelle volume ──

def open_volume(loc):
    import zarr
    from zarr.n5 import N5FSStore

    root = zarr.open(N5FSStore(loc["store"], anon=True), mode="r")
    return root[f"{loc['array']}/s0"]


def read_box(arr, loc, lo, hi):
    """float32 [lo, hi) of the cell's 4 nm isotropic grid; indices outside the volume are reflected."""
    zstep = loc["s0_voxel_nm_zyx"][0]
    z_off16, y_off, x_off = loc["offset_16nm_zyx"][0] * 4, loc["offset_s0_zyx"][1], loc["offset_s0_zyx"][2]
    shape = arr.shape
    zs = (np.arange(lo[0], hi[0]) + z_off16) * 4.0 / zstep          # position on the stored z axis
    ys = np.arange(lo[1], hi[1]) + y_off
    xs = np.arange(lo[2], hi[2]) + x_off

    def reflect(i, n):
        i = np.abs(i)
        i = np.where(i >= n, 2 * (n - 1) - i, i)
        return np.clip(i, 0, n - 1)

    z0 = np.floor(zs).astype(int)
    w = (zs - z0).astype(np.float32)
    z1 = z0 + 1
    z0, z1, ys, xs = reflect(z0, shape[0]), reflect(z1, shape[0]), reflect(ys, shape[1]), reflect(xs, shape[2])
    zl, zh = int(min(z0.min(), z1.min())), int(max(z0.max(), z1.max())) + 1
    yl, yh, xl, xh = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    slab = np.asarray(arr[zl:zh, yl:yh, xl:xh]).astype(np.float32)
    slab = slab[:, ys - yl][:, :, xs - xl]
    out = slab[z0 - zl] * (1 - w)[:, None, None] + slab[z1 - zl] * w[:, None, None]
    return out


def contrast_range(arr, loc, shape4, invert, rng):
    """1st and 99th percentile of the cell's intensities, from random 64^3 patches."""
    vals = []
    for _ in range(40):
        lo = [int(rng.integers(0, max(s - 64, 1))) for s in shape4]
        vals.append(read_box(arr, loc, lo, [l + 64 for l in lo]).ravel()[::7])
    v = np.concatenate(vals)
    v = -v if invert else v
    return np.percentile(v, [0.5, 99.5])


def equalise(block, lo, hi, invert):
    from skimage.exposure import equalize_adapthist

    v = -block if invert else block
    u8 = (np.clip((v - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    return (equalize_adapthist(u8, kernel_size=128, clip_limit=0.02, nbins=256) * 255).astype(np.uint8)


# ── one cell ──

def run_cell(cell, loc, data_dir: Path, out_dir: Path, model, device, k, half, max_blocks):
    import tifffile
    import torch
    import zarr

    group = cell.split("_")[0]
    base = data_dir / group / cell / cell
    # Where the cell is: the published membrane mask is the cell's filled outline; it is in the
    # local orientation, whose y runs opposite to the volume's.
    inside = (tifffile.imread(f"{base}_membrane_mask.tif") > 0)[:, ::-1, :]
    nz, ny, nx = loc["size_16nm_zyx"]
    shape4 = (nz * 4, ny * 4, nx * 4)
    arr = open_volume(loc)
    invert = bool(loc["volume_contrast_inverted"])
    rng = np.random.default_rng(0)
    lo_v, hi_v = contrast_range(arr, loc, shape4, invert, rng)

    n, o = in_size(k), out_size(k)
    ctx = (n - o) // 2
    store = zarr.open(str(out_dir / f"{cell}_er_4nm.zarr"), mode="w", shape=shape4, chunks=(128, 128, 128),
                      dtype="u1")
    done = skipped = 0
    t0 = time.time()
    total = int(np.prod([-(-s // o) for s in shape4]))
    for z in range(0, shape4[0], o):
        for y in range(0, shape4[1], o):
            for x in range(0, shape4[2], o):
                sl = tuple(slice(a, min(a + o, s)) for a, s in zip((z, y, x), shape4))
                m = inside[sl[0].start // 4:-(-sl[0].stop // 4), sl[1].start // 4:-(-sl[1].stop // 4),
                           sl[2].start // 4:-(-sl[2].stop // 4)]
                if not m.any():
                    skipped += 1
                    continue
                lo = (z - ctx, y - ctx, x - ctx)
                block = read_box(arr, loc, lo, [a + n for a in lo])
                eq = equalise(block, lo_v, hi_v, invert)
                t = torch.from_numpy(eq.astype(np.float32) / 255 * 2 - 1)[None, None].to(device)
                with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=half):
                    p = torch.softmax(model(t).float(), 1)[0, 1].cpu().numpy()
                store[sl] = (p[:sl[0].stop - sl[0].start, :sl[1].stop - sl[1].start, :sl[2].stop - sl[2].start] > 0.5
                             ).astype(np.uint8)
                done += 1
                if done % 20 == 0:
                    print(f"{cell}: {done} blocks, {skipped} skipped of {total}, {time.time() - t0:.0f} s", flush=True)
                if max_blocks and done >= max_blocks:
                    break
            if max_blocks and done >= max_blocks:
                break
        if max_blocks and done >= max_blocks:
            break
    print(f"{cell}: {done} blocks done, {skipped} outside the cell, {time.time() - t0:.0f} s", flush=True)

    # 16 nm in the published masks' own orientation: a voxel is ER when most of its 4 nm voxels are.
    er16 = np.zeros((nz, ny, nx), np.uint8)
    for z in range(0, nz, 32):
        part = np.asarray(store[z * 4:min(z + 32, nz) * 4])
        zz = part.shape[0] // 4
        er16[z:z + zz] = part[:zz * 4].reshape(zz, 4, ny, 4, nx, 4).mean((1, 3, 5)) > 0.5
    tifffile.imwrite(out_dir / f"{cell}_er_mask.tif", er16[:, ::-1, :].copy(), compression="zlib")
    print(f"{cell}: ER is {100 * er16[inside].mean():.2f} % of the cell volume", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path, help="the betacells folder holding low/ and high/")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--locations", type=Path, default=Path(__file__).with_name("betacells_locations.json"))
    ap.add_argument("--cells", default="", help="comma list; default: all seven")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--block-k", type=int, default=24, help="block size: input 8k+60 voxels, output 8k-30 (12 fits 4 GB)")
    ap.add_argument("--half", action="store_true", help="float16 inference")
    ap.add_argument("--weights", type=Path, default=Path("model_checkpoint_1669_er_HPF.pt"))
    ap.add_argument("--max-blocks", type=int, default=0, help="stop after this many blocks (smoke test)")
    args = ap.parse_args()

    if not args.weights.exists():
        print("downloading ASEM ER weights ->", args.weights)
        urllib.request.urlretrieve(WEIGHTS, args.weights)
    locations = json.loads(args.locations.read_text())
    cells = [c for c in args.cells.split(",") if c] or list(locations)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    model = load_model(args.weights, args.device)
    for cell in cells:
        run_cell(cell, locations[cell], args.data_dir, args.out_dir, model, args.device,
                 args.block_k, args.half, args.max_blocks)


if __name__ == "__main__":
    main()
