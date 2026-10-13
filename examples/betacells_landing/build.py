#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas", "pyarrow", "tifffile", "fast-simplification"]
# ///
"""A landing page for the beta-cell dataset: the seven cells of Müller et al. (J Cell Biol 2021) in 3D, together.

    uv run examples/betacells_landing/build.py MESHES DATA [-o betacells_landing.html]

MESHES is the `<report>_meshes` folder that `organella process --with-mesh` wrote for the seven cells
(one `<cell>/geometry.parquet` each); DATA is the betacells folder with the published masks, which give the
corner of each cell's crop. Where each cell sits in its OpenOrganelle volume is in
`examples/betacells_locations.json`. The page is one HTML file: the geometry is inside it, three.js comes from
a CDN, and the cells stand where they stand in the tissue, the four high-glucose cells in one volume and the
three low-glucose cells in the other.

The published crops are flipped in y relative to the volume. The page uses the volume's own frame, so the
arrangement of the cells is the real one; every mesh is mirrored in y and its faces turned to match.
"""
from __future__ import annotations

import argparse
import base64
import json
import zlib
from pathlib import Path

import numpy as np

VOXEL_UM = 0.016                      # the published masks, and the volumes at the scale the cells were placed at
HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent

# The paper's own colours: the palette of Fig. 12a in "Organelle-specific segmentation, spatial analysis, and
# visualization of volume electron microscopy datasets" (the protocol that goes with Müller et al., J Cell Biol
# 2021), as HTML codes. The palette has no colour for the MTOC and cilium, which are the centrioles' structures, so
# they take the centrioles' purple; it splits the vesicles by association with microtubules, which the report does
# not, so every granule takes the associated colour.
COLOURS = {"membrane": "#656565", "nucleus": "#FFCC66", "mitochondria": "#00A1FF", "golgi": "#2DB98A",
           "granules": "#FF8C73", "microtubules": "#C63929", "centrioles": "#531C87", "mtoc_cilium": "#531C87"}
# The conditions, as the blue and orange of "low glucose" and "high glucose" in Fig. 1A of the J Cell Biol paper.
CONDITIONS = {"low": "#0077C1", "high": "#FF7E00"}
LABELS = {"membrane": "Cell", "nucleus": "Nucleus", "mitochondria": "Mitochondria", "golgi": "Golgi",
          "granules": "Insulin granules", "microtubules": "Microtubules", "centrioles": "Centrioles",
          "mtoc_cilium": "MTOC and cilium"}
# Vertices a mesh may keep; the report's own meshes are far finer than a page of seven cells can carry.
BUDGET = {"membrane": 14000, "nucleus": 7000, "golgi": 7000, "mitochondria": 30000, "centrioles": 1500,
          "mtoc_cilium": 2500}
MESH_NAMES = tuple(BUDGET)
MICROTUBULE_STRIDE = 8                # keep one in this many nodes of a centre line that does not branch


# ── the report's payloads ──────────────────────────────────────────────────

def decode_mesh(payload: bytes):
    """Vertices (xyz, µm) and triangles of a surface payload; see organella.analysis.meshes.quantised_payload."""
    nv, nf = (int(v) for v in np.frombuffer(payload, "<u4", 2))
    lo = np.frombuffer(payload, "<f4", 3, 8)
    span = np.frombuffer(payload, "<f4", 3, 20)
    q = np.frombuffer(payload, "<u2", nv * 3, 32).reshape(nv, 3).astype(np.float32)
    index = np.dtype("<u2") if nv < 65536 else np.dtype("<u4")
    faces = np.frombuffer(payload, index, nf * 3, 32 + nv * 6).reshape(nf, 3).astype(np.int64)
    return lo + q / 65535.0 * span, faces


def decode_tube(payload: bytes):
    """Centre line (vertices xyz in µm, edges) and radii of a tube payload."""
    nv, ne = (int(v) for v in np.frombuffer(payload, "<u4", 2))
    lo = np.frombuffer(payload, "<f4", 3, 8)
    span = np.frombuffer(payload, "<f4", 3, 20)
    q = np.frombuffer(payload, "<u2", nv * 3, 32).reshape(nv, 3).astype(np.float32)
    index = np.dtype("<u2") if nv < 65536 else np.dtype("<u4")
    edges = np.frombuffer(payload, index, ne * 2, 32 + nv * 6).reshape(ne, 2).astype(np.int64)
    return lo + q / 65535.0 * span, edges


def decode_ellipsoid(payload: bytes):
    """Centre (xyz, µm) and the radius of the sphere with the same volume, of a 60-byte ellipsoid."""
    v = np.frombuffer(payload, "<f4", 15)
    return v[:3], float(np.cbrt(np.prod(v[3:6])))


# ── geometry ───────────────────────────────────────────────────────────────

def simplify(verts: np.ndarray, faces: np.ndarray, budget: int):
    if len(verts) <= budget or len(faces) < 100:
        return verts, faces
    import fast_simplification

    v, f = fast_simplification.simplify(verts.astype(np.float32), faces.astype(np.int32),
                                        target_reduction=1.0 - budget / len(verts), agg=5)
    return v, f.astype(np.int64)


def thin_lines(verts: np.ndarray, edges: np.ndarray, stride: int):
    """Keep one in `stride` of the nodes that only continue a line, and every end and branch."""
    nbrs: dict[int, set[int]] = {}
    for a, b in edges:
        nbrs.setdefault(int(a), set()).add(int(b))
        nbrs.setdefault(int(b), set()).add(int(a))
    for v in sorted(nbrs):
        if len(nbrs[v]) == 2 and v % stride:
            a, b = nbrs.pop(v)
            nbrs[a].discard(v)
            nbrs[b].discard(v)
            if a != b:
                nbrs[a].add(b)
                nbrs[b].add(a)
    keep = sorted(nbrs)
    remap = {v: i for i, v in enumerate(keep)}
    pairs = sorted({(remap[a], remap[b]) for a, bs in nbrs.items() for b in bs if a < b})
    return verts[keep], np.array(pairs, np.int64).reshape(-1, 2)


def quantise(points: np.ndarray):
    lo = points.min(0)
    span = np.maximum(points.max(0) - lo, 1e-9)
    return np.clip((points - lo) / span * 65535 + 0.5, 0, 65535).astype(np.uint16), lo, span


class Blob:
    """Binary buffers laid end to end; `add` returns where one went, for the page to find it again."""

    def __init__(self):
        self.parts, self.size = [], 0

    def add(self, array: np.ndarray, dtype: str):
        data = np.ascontiguousarray(array, dtype=dtype).tobytes()
        pad = (-self.size) % 4
        self.parts.append(b"\0" * pad + data)
        at = self.size + pad
        self.size = at + len(data)
        return {"at": at, "n": int(array.size), "type": dtype}


def build_cell(cell: str, mesh_dir: Path, data_dir: Path, loc: dict, blob: Blob):
    import pyarrow.parquet as pq
    import tifffile

    table = pq.read_table(mesh_dir / cell / "geometry.parquet",
                          columns=["entity_name", "row_type", "label_id", "surface_kind", "surface", "volume_um3",
                                   "length_um"]).to_pandas()
    table = table[table.row_type.isin(["file", "instance"])]
    # Where the cell's crop starts in the published mask, and so where the meshes' origin is.
    group = cell.split("_")[0]
    mask = tifffile.imread(data_dir / group / cell / f"{cell}_membrane_mask.tif") > 0
    occupied = np.argwhere(mask)
    lo = occupied.min(0)
    ny = mask.shape[1]
    off_z, off_y, off_x = loc["offset_16nm_zyx"]
    # Mesh point (x, y, z) in µm -> the volume's frame: x and z as they are, y mirrored.
    shift = np.array([(off_x + lo[2]) * VOXEL_UM, (off_y + ny - 1 - lo[1]) * VOXEL_UM, (off_z + lo[0]) * VOXEL_UM])
    sign = np.array([1.0, -1.0, 1.0])

    def world(points):
        return shift + sign * np.asarray(points, np.float64)

    structures, cloud = {}, []
    for name in MESH_NAMES:
        rows = table[(table.entity_name == name) & (table.surface_kind == "mesh") & table.surface.notna()]
        pieces, base = [], 0
        for payload in rows.surface:
            if not payload:
                continue
            v, f = decode_mesh(payload)
            pieces.append((v, f + base))
            base += len(v)
        if not pieces:
            continue
        v, f = simplify(np.vstack([p[0] for p in pieces]), np.vstack([p[1] for p in pieces]), BUDGET[name])
        v = world(v)
        f = f[:, ::-1]                                # the mirror turns every triangle inside out
        cloud.append(v)
        structures[name] = {"kind": "mesh", "instances": int(len(rows)), "_v": v, "_f": f}

    rows = table[(table.entity_name == "granules") & (table.surface_kind == "ellipsoid") & table.surface.notna()]
    if len(rows):
        spheres = [decode_ellipsoid(p) for p in rows.surface if len(p) == 60]
        centres = world(np.array([c for c, _ in spheres]))
        structures["granules"] = {"kind": "spheres", "instances": len(spheres), "_c": centres,
                                  "_r": np.array([r for _, r in spheres])}
        cloud.append(centres)

    rows = table[(table.entity_name == "microtubules") & (table.surface_kind == "tube") & table.surface.notna()]
    if len(rows):
        verts, edges, base = [], [], 0
        for payload in rows.surface:
            if not payload:
                continue
            v, e = decode_tube(payload)
            verts.append(v)
            edges.append(e + base)
            base += len(v)
        v, e = thin_lines(np.vstack(verts), np.vstack(edges), MICROTUBULE_STRIDE)
        v = world(v)
        structures["microtubules"] = {"kind": "lines", "instances": int(len(rows)), "_v": v, "_e": e,
                                      "length_um": float(table[table.entity_name == "microtubules"].length_um.sum())}
        cloud.append(v)

    allp = np.vstack(cloud)
    centre = (allp.min(0) + allp.max(0)) / 2
    out = {}
    for name, s in structures.items():
        entry = {"kind": s["kind"], "instances": s["instances"], "colour": COLOURS[name], "label": LABELS[name]}
        if s["kind"] == "mesh":
            q, mn, sp = quantise(s["_v"] - centre)
            entry.update(pos=blob.add(q, "<u2"), min=mn.tolist(), span=sp.tolist(),
                         index=blob.add(s["_f"], "<u2" if len(q) < 65536 else "<u4"), vertices=int(len(q)))
        elif s["kind"] == "spheres":
            entry.update(centres=blob.add((s["_c"] - centre).astype(np.float32), "<f4"),
                         radii=blob.add(s["_r"].astype(np.float32), "<f4"))
        else:
            q, mn, sp = quantise(s["_v"] - centre)
            entry.update(pos=blob.add(q, "<u2"), min=mn.tolist(), span=sp.tolist(),
                         index=blob.add(s["_e"], "<u2" if len(q) < 65536 else "<u4"), vertices=int(len(q)),
                         length_um=s["length_um"])
        out[name] = entry

    def volume(name):
        r = table[(table.entity_name == name) & (table.row_type == "file")].volume_um3.dropna()
        return float(r.iloc[0]) if len(r) else None

    return {"id": cell, "group": group, "centre": centre.tolist(), "extent": (allp.max(0) - allp.min(0)).tolist(),
            "cell_um3": volume("membrane"), "nucleus_um3": volume("nucleus"), "structures": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("meshes", type=Path, help="the <report>_meshes folder")
    ap.add_argument("data", type=Path, help="the betacells folder with low/ and high/")
    ap.add_argument("-o", "--out", type=Path, default=Path("betacells_landing.html"))
    ap.add_argument("--locations", type=Path, default=EXAMPLES / "betacells_locations.json")
    ap.add_argument("--report-url", default="https://ida-mdc.github.io/organella/")
    ap.add_argument("--cells", default="", help="comma list; default: every cell in the locations file")
    ap.add_argument("--reuse", type=Path, help="a page built before: keep its geometry and only put it in the current template")
    args = ap.parse_args()
    if args.reuse:
        old = args.reuse.read_text()
        described = json.loads(old.split("const SCENE = ", 1)[1].split(";\nconst PACKED", 1)[0])
        for cell in described["cells"]:
            for name, structure in cell["structures"].items():
                structure["colour"] = COLOURS[name]
        described["conditions"] = CONDITIONS
        scene = json.dumps(described, separators=(",", ":"))
        packed = old.split('const PACKED = "', 1)[1].split('";', 1)[0]
        page = (( HERE / "template.html").read_text().replace("__SCENE__", scene).replace("__BLOB__", packed)
                .replace("__REPORT_URL__", args.report_url))
        args.out.write_text(page)
        print(f"{args.out}: {len(page) / 1e6:.1f} MB")
        return

    locations = json.loads(args.locations.read_text())
    cells = [c for c in args.cells.split(",") if c] or [c for c in locations if (args.meshes / c).is_dir()]
    blob, scene = Blob(), []
    for cell in cells:
        print(cell, flush=True)
        scene.append(build_cell(cell, args.meshes, args.data, locations[cell], blob))
    raw = b"".join(blob.parts)
    packed = base64.b64encode(zlib.compress(raw, 9)).decode()
    template = (HERE / "template.html").read_text()
    page = (template.replace("__SCENE__", json.dumps({"cells": scene, "conditions": CONDITIONS}, separators=(",", ":")))
            .replace("__BLOB__", packed).replace("__REPORT_URL__", args.report_url))
    args.out.write_text(page)
    print(f"{args.out}: {len(page) / 1e6:.1f} MB ({len(raw) / 1e6:.1f} MB of geometry before packing)")


if __name__ == "__main__":
    main()
