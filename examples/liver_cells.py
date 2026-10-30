#!/usr/bin/env -S uv run python
"""Cut the cells of an OpenOrganelle mouse-liver volume apart, one object folder each, from the published cell labels.

    uv run python examples/liver_cells.py jrc_mus-liver-6 OUT --scale s3 --mask-scale s4

`jrc_mus-liver-6` and `-7` ship a `cell` array in which every cell has its own id (the segmentation of Parlakgül,
Arruda et al., Nature 2022), so unlike `organella cells` nothing is derived: each id is carried to --scale and
written as a manifest and a mask that `organella process` reads. The other structures are read from the store at
--scale; they are binary (0 and 255) masks, not labelled instances, and every one is declared as a mask: one
region per structure and cell, with a volume and a surface but no per-droplet or per-mitochondrion numbers (touching
droplets or mitochondria are one connected piece in the published arrays, so cutting them apart would count clusters).
Cells under --min-um3 are the slivers of cells the volume cuts off, and are left out; the ones that reach a volume face
are kept and marked.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from organella.cells.derive import cells_at_scale
from organella.cells.folders import write_cell_folder
from organella.sources.arrays import open_store_array

BUCKET = "janelia-cosem-datasets"
SEG = "recon-1/labels/inference/segmentations"
EM = "recon-1/em/fibsem-uint8"


def voxel_um(store: str, array: str, scale: str) -> tuple[float, ...]:
    """The array's multiscale metadata gives nanometres per voxel at each scale, in array order."""
    url = f"https://{BUCKET}.s3.amazonaws.com/{store.split('/', 3)[3]}/{array}/.zattrs"
    with urllib.request.urlopen(url) as response:
        attrs = json.load(response)
    for level in attrs["multiscales"][0]["datasets"]:
        if level["path"] == scale:
            return tuple(v / 1000.0 for v in level["coordinateTransformations"][0]["scale"])
    raise SystemExit(f"no scale {scale} in {array}")


def present(store: str, names: list[str], scale: str) -> list[str]:
    found = []
    for name in names:
        try:
            open_store_array(store, f"{SEG}/{name}/{scale}")
            found.append(name)
        except FileNotFoundError:
            continue
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset")
    ap.add_argument("out", type=Path)
    ap.add_argument("--scale", default="s3")
    ap.add_argument("--mask-scale", default="s4")
    ap.add_argument("--min-um3", type=float, default=300.0)
    ap.add_argument("--entities", default="mito,er,ld,nuc_mem,er-sheets,er-tubules")
    args = ap.parse_args()

    store = f"s3://{BUCKET}/{args.dataset}/{args.dataset}.zarr"
    names = present(store, args.entities.split(","), args.scale)
    cell_array = f"{SEG}/cell"
    coarse_um = voxel_um(store, cell_array, args.mask_scale)
    work_um = voxel_um(store, cell_array, args.scale)
    labels = open_store_array(store, f"{cell_array}/{args.mask_scale}").compute()
    work_shape = open_store_array(store, f"{cell_array}/{args.scale}").shape
    print(f"{args.dataset}: {labels.shape} at {args.mask_scale}, {len(set(labels.ravel().tolist())) - 1} cells, entities {names}")
    found = cells_at_scale(labels, coarse_um, work_um, work_shape)
    # The published arrays are binary, so every structure is a mask; there is no label array to take instances from.
    entities = {n: {"array": f"{SEG}/{n}", "kind": "mask"} for n in names}
    prefix = args.dataset.replace("jrc_mus-", "").replace("-", "")
    kept = 0
    for c in found:
        if c.volume_um3 < args.min_um3:
            continue
        kept += 1
        folder = write_cell_folder(
            args.out / f"{prefix}_{c.number:02d}", c, store=store, scale=args.scale, source=EM, entities=entities,
            object_name="cell", derived_from={"method": "published cell labels", "array": cell_array, "mask_scale": args.mask_scale})
        # The store records no voxel size on these arrays themselves, so the manifest gives it.
        manifest = folder / "source.json"
        data = json.loads(manifest.read_text())
        data["voxel_size_um"] = list(work_um)
        manifest.write_text(json.dumps(data, indent=2) + "\n")
        print(f"{folder.name}: {c.volume_um3:8.0f} um3  {'cut off at ' + c.touches_edge if c.touches_edge else ''}")
    print(f"{kept} of {len(found)} cells kept (>= {args.min_um3:g} um3) -> {args.out}")


if __name__ == "__main__":
    main()
