#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas", "pyarrow", "fast-simplification"]
# ///
"""An overview of the reports in a data folder: a card each, with one whole object in 3D and a link that opens it.

    uv run examples/overview/build.py DATA [-o overview.html]

DATA holds the reports named in reports.json (paths relative to it). Each card shows one object (the one with the
median-sized geometry, if the report has several) with every structure of it, taken from the report's own geometry
and thinned to what a card can carry; the object's own outline is drawn see-through. A report with no geometry gets
a card with no 3D. The links are `/r/<id>/...` on the server `serve.py` runs, which is what makes them work: a link
to a local file cannot start the viewer.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MESH_BUDGET = 10000                   # vertices a card's structures may keep between them
OUTLINE_BUDGET = 2500                 # the object's own outline, drawn see-through
MAX_SPHERES = 2500                    # granules, vesicles and the like are spheres; a card draws this many at most
MAX_LINE_EDGES = 1500
MAX_SPHERE_RADIUS = 0.03             # of the object's longest side
MAX_PIECES = 400                      # instances of one structure a card keeps, the largest
# One colour per structure, in name order; a report with more structures than colours reuses them.
PALETTE = ["#2db98a", "#ff8c73", "#00a1ff", "#ffcc66", "#c63929", "#531c87", "#e377c2", "#7f7f7f", "#17becf", "#bcbd22"]


def decode_mesh(payload: bytes):
    """Vertices (xyz) and triangles of a surface payload; see organella.analysis.meshes.quantised_payload."""
    nv, nf = (int(v) for v in np.frombuffer(payload, "<u4", 2))
    lo = np.frombuffer(payload, "<f4", 3, 8)
    span = np.frombuffer(payload, "<f4", 3, 20)
    q = np.frombuffer(payload, "<u2", nv * 3, 32).reshape(nv, 3).astype(np.float32)
    index = np.dtype("<u2") if nv < 65536 else np.dtype("<u4")
    faces = np.frombuffer(payload, index, nf * 3, 32 + nv * 6).reshape(nf, 3).astype(np.int64)
    return lo + q / 65535.0 * span, faces


def simplify(verts, faces, budget):
    if len(verts) <= budget or len(faces) < 100:
        return verts, faces
    import fast_simplification
    v, f = fast_simplification.simplify(verts.astype(np.float32), faces.astype(np.int32),
                                        target_reduction=1.0 - budget / len(verts), agg=5)
    f = f.astype(np.int64)
    # The simplifier stops where a collapse would cost too much; a surface it leaves far over budget is clustered.
    return cluster(v, f, budget) if len(v) > 2 * budget else (v, f)


def cluster(verts, faces, budget: int):
    """Merge vertices that share a cell of a grid, the grid coarsened until about `budget` cells are occupied."""
    lo, span = verts.min(0), max(float((verts.max(0) - verts.min(0)).max()), 1e-9)
    cells = int(np.cbrt(budget * 4)) + 1
    while True:
        key = np.floor((verts - lo) / span * cells).astype(np.int64)
        _, first, inverse = np.unique(key[:, 0] * cells * cells + key[:, 1] * cells + key[:, 2],
                                      return_index=True, return_inverse=True)
        if len(first) <= budget * 1.5 or cells <= 2:
            break
        cells = max(2, int(cells * 0.85))
    out = np.zeros((len(first), 3))
    np.add.at(out, inverse, verts)
    out /= np.bincount(inverse, minlength=len(first))[:, None]
    f = inverse[faces]
    return out.astype(np.float32), f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]


def decode_tube(payload: bytes):
    """Centre line (vertices xyz, edges) of a tube payload."""
    nv, ne = (int(v) for v in np.frombuffer(payload, "<u4", 2))
    lo = np.frombuffer(payload, "<f4", 3, 8)
    span = np.frombuffer(payload, "<f4", 3, 20)
    q = np.frombuffer(payload, "<u2", nv * 3, 32).reshape(nv, 3).astype(np.float32)
    index = np.dtype("<u2") if nv < 65536 else np.dtype("<u4")
    edges = np.frombuffer(payload, index, ne * 2, 32 + nv * 6).reshape(ne, 2).astype(np.int64)
    return lo + q / 65535.0 * span, edges


def decode_ellipsoid(payload: bytes):
    """Centre (xyz) and the radius of the sphere with the same volume, of a 60-byte ellipsoid."""
    v = np.frombuffer(payload, "<f4", 15)
    return v[:3], float(np.cbrt(np.prod(v[3:6])))


def geometry_files(report: Path) -> list[Path]:
    """The report's geometry, read off the report as `organella view` does."""
    import pyarrow.parquet as pq
    if "mesh_geometry_file" not in pq.read_schema(report).names:
        return []
    paths = pq.read_table(report, columns=["mesh_geometry_file"]).column(0).drop_null().unique().to_pylist()
    return [Path(p) for p in paths if Path(p).is_file()]


def describe(report: Path) -> dict:
    """What the card says: the structures, how many objects, and which structure is the object's own outline."""
    import pyarrow.parquet as pq
    names = pq.read_schema(report).names
    t = pq.read_table(report, columns=["object_id", "entity_name"]
                      + (["object_mask_name"] if "object_mask_name" in names else [])).to_pandas()
    masks = t.get("object_mask_name")
    return {"objects": int(t.object_id.nunique()),
            "structures": sorted(t.entity_name.dropna().unique().tolist()),
            "outline": next((m for m in masks.dropna().unique() if m), None) if masks is not None else None}


def one_object(files: list[Path]) -> Path | None:
    """The object with the median-sized geometry file, so the card shows a typical one, not the first."""
    files = sorted(files, key=lambda f: f.stat().st_size)
    return files[len(files) // 2] if files else None


def spread(budget: int, sizes: dict) -> dict:
    """Vertices each structure may keep: shares grow with the square root of its size, at least 150 each."""
    weights = {k: v ** 0.5 for k, v in sizes.items()}
    total = sum(weights.values()) or 1.0
    return {k: max(150, int(budget * w / total)) for k, w in weights.items()}


def thin(pieces, budget: int):
    """The pieces of one structure as one surface of about `budget` vertices, each piece cut to its share."""
    each = max(24, budget // len(pieces))
    out_v, out_f, base = [], [], 0
    for v, f in pieces:
        v, f = simplify(v, f, each)
        out_v.append(v)
        out_f.append(f + base)
        base += len(v)
    return np.vstack(out_v), np.vstack(out_f)


def cell_layers(file: Path, outline: str | None) -> list[dict]:
    """Every structure of one object as a layer: a surface, spheres, or lines, in the object's own frame (µm)."""
    import pyarrow.parquet as pq
    names = pq.read_schema(file).names
    column = "surface" if "surface" in names else "mesh"
    kind = "surface_kind" if "surface_kind" in names else None
    t = pq.read_table(file, columns=["entity_name", "row_type", column] + ([kind] if kind else [])).to_pandas()
    t = t[t.row_type.isin(["instance", "file"]) & t[column].notna()]
    t = t[t[column].map(lambda s: bool(s) and len(s) > 32)]
    t["kind"] = t[kind] if kind else "mesh"
    surfaces, spheres, lines = {}, {}, {}
    for (name, k), rows in t.groupby(["entity_name", "kind"]):
        payloads = list(rows[column])
        if k == "mesh":
            # Large instances first: they are what a card can show; thousands of specks are not.
            decoded = sorted((decode_mesh(p) for p in payloads), key=lambda m: -len(m[0]))[:MAX_PIECES]
            surfaces[name] = decoded
        elif k == "ellipsoid":
            found = [decode_ellipsoid(p) for p in payloads if len(p) == 60]
            keep = found[::max(1, len(found) // MAX_SPHERES + 1)]
            spheres[name] = (np.array([c for c, _ in keep]), np.array([r for _, r in keep]))
        elif k == "tube":
            verts, edges, base = [], [], 0
            for p in payloads:
                v, e = decode_tube(p)
                verts.append(v)
                edges.append(e + base)
                base += len(v)
            lines[name] = (np.vstack(verts), np.vstack(edges))
    allpoints = np.vstack([v for pieces in surfaces.values() for v, _ in pieces] + [c for c, _ in spheres.values()]
                          + [v for v, _ in lines.values()])
    centre = (allpoints.min(0) + allpoints.max(0)) / 2
    scale = float((allpoints.max(0) - allpoints.min(0)).max()) or 1.0      # the longest side becomes 1
    share = spread(MESH_BUDGET, {n: sum(len(v) for v, _ in p) for n, p in surfaces.items() if n != outline})
    layers = []
    order = sorted(set(surfaces) | set(spheres) | set(lines))
    for n in order:
        colour = "#8d8a80" if n == outline else PALETTE[sorted(order).index(n) % len(PALETTE)]
        if n in surfaces:
            v, f = thin(surfaces[n], OUTLINE_BUDGET if n == outline else share[n])
            layers.append({"name": n, "colour": colour, "type": "mesh", "outline": n == outline,
                           "v": b64((v - centre) / scale, "<f4"), "f": b64(f, "<u4")})
        if n in spheres:
            c, r = spheres[n]
            small = r < MAX_SPHERE_RADIUS * scale          # a big ellipsoid stands in for a network, not a vesicle
            c, r = c[small], r[small]
            if not len(r):
                continue
            layers.append({"name": n, "colour": colour, "type": "spheres",
                           "c": b64((c - centre) / scale, "<f4"), "r": b64(r / scale, "<f4")})
        if n in lines:
            v, e = lines[n]
            e = e[::max(1, len(e) // MAX_LINE_EDGES + 1)]
            layers.append({"name": n, "colour": colour, "type": "lines",
                           "v": b64((v - centre) / scale, "<f4"), "e": b64(e, "<u4")})
    return layers


def b64(array, dtype):
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode()


def build(data: Path, out: Path) -> None:
    cards = []
    for entry in json.loads((HERE / "reports.json").read_text()):
        report = data / entry["report"]
        if not report.is_file():
            print(f"skip {entry['id']}: {report} is missing")
            continue
        info = describe(report)
        card = {**entry, **info, "stem": report.name, "layers": [], "preview": None}
        file = one_object(geometry_files(report))
        if file:
            card["layers"] = cell_layers(file, info["outline"])
            card["preview"] = file.parent.name
        print(f"{entry['id']}: {len(info['structures'])} structures, preview "
              f"{card['preview'] and card['preview'] + ' with ' + str(len(card['layers'])) + ' layers' or 'none (no geometry)'}")
        cards.append(card)
    page = (HERE / "template.html").read_text().replace("__CARDS__", json.dumps(cards))
    out.write_text(page)
    print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=HERE / "overview.html")
    args = ap.parse_args()
    build(args.data.resolve(), args.output)
