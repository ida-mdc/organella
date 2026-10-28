#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas", "pyarrow", "scipy", "fast-simplification"]
# ///
"""A landing page for a set of cells in 3D: every cell of a report, in one HTML file, standing where it stands in its volume.

    uv run examples/landing/build.py PROFILE REPORT MESHES CELLS [-o landing.html]

PROFILE is `liver` or `hela`, which says what to draw and how finely; REPORT is the report parquet the cells were
measured into (it supplies the counts and volumes in the side panel); MESHES is the `<report>_meshes` folder that
`organella process --with-mesh` wrote (one `<cell>/geometry.parquet` each); CELLS is the folder, or the folders,
that hold the cell folders with their `source.json`, which says where each cell's crop starts in its volume.

The page is one HTML file: the geometry is inside it, three.js comes from a CDN. Each volume is a group; the cells of
a group stand where they stand in it, and the groups stand side by side. A mesh's points are in µm from the corner of
the cell's crop, so a cell's place is that corner plus the point. The geometry is thinned to what a page can carry: a
mesh keeps a budget of vertices, and the many small structures are drawn as spheres, the largest ones only.
"""
from __future__ import annotations

import argparse
import base64
import json
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

# A structure is drawn as a surface ("mesh", thinned to `budget` vertices) or as spheres of the same volume ("spheres":
# those of at least `min_um3`, the largest `cap` of them, centred on the instance's own centre).
PROFILES = {
    "liver": {
        "title": "Mouse liver cells in 3D",
        "sub": "Hepatocytes of two FIB-SEM volumes of mouse liver, jrc_mus-liver-6 and jrc_mus-liver-7 (Parlakgül et "
               "al., <i>Nature</i> 2022), cut out of the published cell labels and measured at 64 nm. The cells stand "
               "where they stand in their volume; a cell the volume's edge cuts is cut here too. Each structure is "
               "the published mask. Only its largest pieces are drawn, each as it is, and the smallest are left out "
               "to keep the page light. The endoplasmic reticulum is left out, since its thin tubes are lost at "
               "this resolution.",
        "groups": [("liver-6", "liver6", "#0077C1"), ("liver-7", "liver7", "#FF7E00")],
        "folders": ["liver"],
        "structures": {
            "cell": dict(label="Cell", colour="#656565", kind="mesh", budget=4000),
            "nuc_mem": dict(label="Nuclear membrane", colour="#FFCC66", kind="mesh", budget=3000),
            "mito": dict(label="Mitochondria", colour="#00A1FF", kind="mesh", budget=20000),
            "ld": dict(label="Lipid droplets", colour="#FF8C73", kind="mesh", budget=10000),
        },
        "hidden": [],
    },
    "hela": {
        "title": "HeLa cells in 3D",
        "sub": "One HeLa cell from each of two FIB-SEM volumes, jrc_hela-2 and jrc_hela-3 (Heinrich et al., <i>Nature</i> "
               "2021), cut out with the cell derived from the plasma membrane, and measured at 32 nm. Endosomes are "
               "drawn as spheres of the same volume, and are off until you switch them on.",
        "groups": [("hela-2", "hela2", "#0077C1"), ("hela-3", "hela3", "#FF7E00")],
        "folders": ["hela2_v2", "hela3_v2"],
        "structures": {
            "cell": dict(label="Cell", colour="#656565", kind="mesh", budget=12000),
            "nucleus": dict(label="Nucleus", colour="#FFCC66", kind="mesh", budget=8000),
            # A network of thin sheets: thinned to a budget its volume moves by a fifth, and kept whole it is most of the page.
            "er": dict(label="Endoplasmic reticulum", colour="#C63929", kind="mesh", budget=60000, tolerance=0.3),
            "mito": dict(label="Mitochondria", colour="#00A1FF", kind="mesh", budget=25000),
            "golgi": dict(label="Golgi", colour="#2DB98A", kind="mesh", budget=6000),
            "lyso": dict(label="Lysosomes", colour="#531C87", kind="spheres", min_um3=0.0, cap=3000),
            "ld": dict(label="Lipid droplets", colour="#FF8C73", kind="spheres", min_um3=0.0, cap=3000),
            "endo": dict(label="Endosomes", colour="#9A9A44", kind="spheres", min_um3=0.0, cap=3000),
        },
        "hidden": ["endo"],
    },
}


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


def decode_ellipsoid(payload: bytes):
    """Centre (xyz, µm) and the radius of the sphere with the same volume, of a 60-byte ellipsoid."""
    v = np.frombuffer(payload, "<f4", 15)
    return v[:3].astype(np.float64), float(np.cbrt(np.prod(v[3:6])))


# ── geometry ───────────────────────────────────────────────────────────────

def signed_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    return abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6))


def pieces_of(verts: np.ndarray, faces: np.ndarray):
    """Each connected piece of a mesh, as (vertices, faces), the one with the most vertices first."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(verts)
    graph = coo_matrix((np.ones(len(faces) * 3), (faces.ravel(), np.roll(faces, 1, 1).ravel())), shape=(n, n))
    count, label = connected_components(graph, directed=False)
    sizes = np.bincount(label, minlength=count)
    by_face = np.argsort(label[faces[:, 0]], kind="stable")
    ends = np.cumsum(np.bincount(label[faces[:, 0]], minlength=count))
    starts = ends - np.bincount(label[faces[:, 0]], minlength=count)
    for piece in np.argsort(-sizes, kind="stable"):
        piece_faces = faces[by_face[starts[piece]:ends[piece]]]
        used = np.unique(piece_faces)
        yield verts[used], np.searchsorted(used, piece_faces)


def decimate_piece(verts: np.ndarray, faces: np.ndarray, keep: float, floor: int, tolerance: float = 0.1):
    """One piece with fewer vertices, or as it was where thinning it would change its volume by more than `tolerance`.

    The piece is thinned on its own: meshes thinned together collapse their thin parts into one another. Starting at
    `keep` of its vertices and never below `floor`, the share doubles until the volume is within `tolerance`.
    """
    import fast_simplification

    n = len(verts)
    whole = signed_volume(verts, faces)
    while n > floor * 2 and keep < 1:
        target = max(floor, int(n * keep))
        if target >= n * 0.9:
            break
        v, f = fast_simplification.simplify(verts.astype(np.float32), faces.astype(np.int32),
                                            target_reduction=1.0 - target / n, agg=5)
        if len(f) >= 4 and abs(signed_volume(v, f.astype(np.int64)) - whole) <= tolerance * whole:
            return v, f.astype(np.int64)
        keep *= 2
    return verts, faces


def thin_pieces(verts: np.ndarray, faces: np.ndarray, budget: int, floor: int = 300, tolerance: float = 0.1):
    """The largest pieces of a mesh, each its own surface, up to `budget` vertices; the smaller ones are left out.

    Returns the vertices and faces, and how many pieces there were and how many are drawn.
    """
    keep = min(1.0, budget / max(len(verts), 1))
    out_v, out_f, base, total, drawn = [], [], 0, 0, 0
    for v, f in pieces_of(verts, faces):
        total += 1
        if len(v) > floor * 2:
            v, f = decimate_piece(v, f, keep, floor, tolerance)
        if base + len(v) > budget and drawn:
            continue
        out_v.append(v)
        out_f.append(f + base)
        base += len(v)
        drawn += 1
    return np.vstack(out_v), np.vstack(out_f), total, drawn


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


def crop_corner_um(source: dict) -> np.ndarray:
    """Where the cell's crop starts in its volume, as x, y, z in µm.

    A manifest that does not carry the voxel size (the store records it) gives no place: the cell stands at its own
    origin, which is right for a volume with one cell in it.
    """
    if "voxel_size_um" not in source:
        return np.zeros(3)
    lows = np.array([int(part.split(":")[0]) for part in source["crop"].split(",")], float)       # z, y, x
    voxel = np.array(source["voxel_size_um"], float)                                               # z, y, x
    return (lows * voxel)[::-1]


def spheres_of(rows, cap: int, min_um3: float, shift: np.ndarray):
    """Centres and radii of the largest instances, whichever way each one was stored."""
    found = []
    for kind, payload, volume in zip(rows.surface_kind, rows.surface, rows.volume_um3):
        if not payload:
            continue
        if kind == "ellipsoid" and len(payload) == 60:
            centre, radius = decode_ellipsoid(payload)
        elif kind == "mesh":
            vertices, _ = decode_mesh(payload)
            centre = vertices.mean(0)
            radius = float(np.cbrt(3 * volume / (4 * np.pi))) if volume == volume else 0.0
        else:
            continue
        size = 4 / 3 * np.pi * radius ** 3
        if size >= min_um3:
            found.append((size, centre, radius))
    found.sort(key=lambda t: -t[0])
    found = found[:cap]
    if not found:
        return None, None
    return shift + np.array([c for _, c, _ in found]), np.array([r for _, _, r in found])


def build_cell(cell: str, group: str, profile: dict, mesh_dir: Path, source: dict, entities, blob: Blob):
    import pyarrow.parquet as pq

    table = pq.read_table(mesh_dir / cell / "geometry.parquet",
                          columns=["entity_name", "row_type", "surface_kind", "surface", "volume_um3"]).to_pandas()
    table = table[table.row_type.isin(["file", "instance"])]
    shift = crop_corner_um(source)

    structures, cloud = {}, []
    for name, spec in profile["structures"].items():
        rows = table[(table.entity_name == name) & table.surface.notna()]
        if not len(rows):
            continue
        counted = entities.get(name, {}).get("count")
        if spec["kind"] == "mesh":
            rows = rows[rows.surface_kind == "mesh"]
            pieces, base = [], 0
            for payload in rows.surface:
                if payload:
                    v, f = decode_mesh(payload)
                    pieces.append((v, f + base))
                    base += len(v)
            if not pieces:
                continue
            v, f, found, drawn = thin_pieces(np.vstack([p[0] for p in pieces]), np.vstack([p[1] for p in pieces]),
                                             spec["budget"], spec.get("floor", 300), spec.get("tolerance", 0.1))
            v = shift + v
            cloud.append(v)
            if drawn < found:
                print(f"  {name}: {drawn} of {found} pieces drawn", flush=True)
            structures[name] = {"kind": "mesh", "instances": counted or int(len(rows)), "_v": v, "_f": f}
        else:
            centres, radii = spheres_of(rows, spec["cap"], spec["min_um3"], shift)
            if centres is None:
                continue
            structures[name] = {"kind": "spheres", "instances": counted or len(radii), "_c": centres, "_r": radii}
            cloud.append(centres)

    allp = np.vstack(cloud)
    # The extent is the cell's own surface where there is one: a stray sphere must not stretch the footprint.
    outline = structures["cell"]["_v"] if "cell" in structures else allp
    lo, hi = outline.min(0), outline.max(0)
    centre = (lo + hi) / 2
    out = {}
    for name, s in structures.items():
        spec = profile["structures"][name]
        entry = {"kind": s["kind"], "instances": s["instances"], "colour": spec["colour"], "label": spec["label"]}
        if s["kind"] == "mesh":
            q, mn, sp = quantise(s["_v"] - centre)
            entry.update(pos=blob.add(q, "<u2"), min=mn.tolist(), span=sp.tolist(),
                         index=blob.add(s["_f"], "<u2" if len(q) < 65536 else "<u4"), vertices=int(len(q)))
        else:
            entry.update(centres=blob.add((s["_c"] - centre).astype(np.float32), "<f4"),
                         radii=blob.add(s["_r"].astype(np.float32), "<f4"))
        out[name] = entry

    stats = [[spec["label"], describe(entities.get(name))] for name, spec in profile["structures"].items()
             if name in entities]
    derived = source.get("derived", {})
    edge = derived.get("touches_volume_edge")
    short = cell.replace("liver", "").replace("hela", "").replace("_", " · ")
    return {"id": cell, "group": group, "short": short, "title": f"{group} · cell {cell.split('_')[-1]}",
            "centre": centre.tolist(), "extent": (hi - lo).tolist(),
            "stats": [row for row in stats if row[1]],
            "note": "Cut by the edge of its volume." if edge else "", "structures": out}


def describe(entity):
    if not entity:
        return ""
    volume = f"{entity['volume']:,.0f} µm³" if entity["volume"] is not None else ""
    count = f"{entity['count']:,} · " if entity.get("count") else ""
    return count + volume


def read_entities(report: Path):
    """cell -> structure -> {count, volume}, from the report's entity rows."""
    import pyarrow.parquet as pq

    wanted = ["row_type", "object_id", "entity_name", "instance_count", "total_volume_um3"]
    # A report of masks only has no instance counts.
    rows = pq.read_table(report, columns=[c for c in wanted if c in pq.read_schema(report).names]).to_pandas()
    rows = rows[rows.row_type == "entity"]
    found: dict = {}
    for r in rows.itertuples():
        counted = getattr(r, "instance_count", None)
        count = None if counted is None or counted != counted else int(counted)
        volume = None if r.total_volume_um3 != r.total_volume_um3 else float(r.total_volume_um3)
        found.setdefault(r.object_id, {})[r.entity_name] = {"count": count, "volume": volume}
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", choices=sorted(PROFILES))
    ap.add_argument("report", type=Path, help="the report parquet")
    ap.add_argument("meshes", type=Path, help="the <report>_meshes folder")
    ap.add_argument("cells", type=Path, help="the folder the profile's cell folders are in, e.g. lowres/")
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--report-url", default="", help="where 'Open the full report' goes; empty leaves the button out")
    args = ap.parse_args()
    profile = PROFILES[args.profile]
    out = args.out or Path(f"{args.profile}_landing.html")

    entities = read_entities(args.report)
    blob, scene, groups_present = Blob(), [], set()
    for group, prefix, _ in profile["groups"]:
        for folder in sorted(p.name for p in args.meshes.iterdir() if p.is_dir() and p.name.startswith(prefix)):
            sources = [args.cells / f / folder / "source.json" for f in profile["folders"]
                       if (args.cells / f / folder / "source.json").is_file()]
            if not sources:
                print(f"{folder}: no source.json, left out")
                continue
            print(folder, flush=True)
            scene.append(build_cell(folder, group, profile, args.meshes, json.loads(sources[0].read_text()),
                                    entities.get(folder, {}), blob))
            groups_present.add(group)
    groups = [{"id": g, "name": g, "colour": c} for g, _, c in profile["groups"] if g in groups_present]
    raw = b"".join(blob.parts)
    packed = base64.b64encode(zlib.compress(raw, 9)).decode()
    description = {"cells": scene, "groups": groups, "order": list(profile["structures"]),
                   "hidden": profile["hidden"]}
    page = ((HERE / "template.html").read_text()
            .replace("__SCENE__", json.dumps(description, separators=(",", ":")))
            .replace("__BLOB__", packed).replace("__TITLE__", profile["title"]).replace("__SUB__", profile["sub"])
            .replace("__REPORT_URL__", args.report_url))
    out.write_text(page)
    print(f"{out}: {len(page) / 1e6:.1f} MB ({len(raw) / 1e6:.1f} MB of geometry before packing)")


if __name__ == "__main__":
    main()
