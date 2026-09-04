"""The 3D sections of the report: the geometry they read, and the payloads they decode.

Two contracts are worth pinning here. The first is binary - the page decodes the blob
:mod:`analysis.meshes` writes, so its decoder is run through node against a payload this
suite generated, at an unaligned offset, the way it arrives from Arrow. The second is the
SQL: geometry lives in a file beside the report, and every query has to work whether it is
aimed at one object or at the whole cohort at once.

Both come out of the page itself rather than being written out again here. A mirror can be
right while the page is wrong, which is how a query naming a column its own source did not
expose once reached the browser.
"""

import base64
import os
import struct

import duckdb
import numpy as np
import pytest
from conftest import run_report_page

from label_anatomy.analysis.meshes import (
    GEOMETRY_FILENAME,
    MeshOptions,
    generate_mesh,
    mesh_rows_for_object,
    write_geometry,
)

SHAPE = (12, 24, 24)
VOXEL = (0.1, 0.02, 0.02)


def _ball(radius=4, centre=(6, 12, 12)) -> np.ndarray:
    zz, yy, xx = np.ogrid[: SHAPE[0], : SHAPE[1], : SHAPE[2]]
    return (
        ((zz - centre[0]) / radius) ** 2 + ((yy - centre[1]) / radius) ** 2
        + ((xx - centre[2]) / radius) ** 2
    ) <= 1.0


def _volumes():
    mito = np.zeros(SHAPE, dtype=np.int32)
    mito[_ball(3, (6, 6, 6))] = 1
    mito[_ball(3, (6, 18, 18))] = 2
    mito[_ball(2, (6, 7, 7))] = 3            # overlapping 1, so there is a contact to find
    return {"pm": _ball(10).astype(np.int32), "mito": mito}, {"pm": "mask", "mito": "label"}


@pytest.fixture(scope="module")
def geometry_dir(tmp_path_factory):
    """Two objects' geometry, written exactly as a run with --with-mesh would."""
    root = tmp_path_factory.mktemp("geometry")
    volumes, kinds = _volumes()
    for object_id in ("object_a", "object_b"):
        rows = mesh_rows_for_object(volumes, kinds, VOXEL, object_id=object_id,
                                    options=MeshOptions(contact_max_um=0.5))
        write_geometry(root / object_id, rows)
    return root


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


def files_in(geometry_dir, *objects) -> list:
    return [str(geometry_dir / object_id / GEOMETRY_FILENAME) for object_id in objects]


def build(fn: str, *args) -> str:
    """One query, built by the page rather than written out again here."""
    return run_report_page({"build": [{"fn": fn, "args": list(args)}]})["built"][0]


def source_of(geometry_dir, *objects) -> str:
    """The page's own way of reading a set of objects' geometry as one table."""
    return build("sourceOf", [{"path": path} for path in files_in(geometry_dir, *objects)])


def decode(payload: bytes, per_index: int = 3) -> dict:
    return run_report_page({
        "payloads": [{"base64": base64.b64encode(payload).decode(), "perIndex": per_index}],
    })["payloads"][0]


# ── the decoder, in the runtime the page actually runs in ─────────────────────

def test_the_page_decodes_a_mesh_to_the_vertices_that_were_written():
    payload = generate_mesh(_ball(), (0, 0, 0), VOXEL)
    n_verts, n_faces = struct.unpack_from("<II", payload, 0)

    decoded = decode(payload)

    assert (decoded["vertices"], decoded["elements"]) == (n_verts, n_faces)
    assert decoded["maxIndex"] < n_verts
    # Vertices are µm in XYZ: the ball spans at most the volume it was meshed from,
    # 24 voxels of 0.02 µm across X and Y, 12 of 0.1 µm through Z.
    assert 0 <= decoded["bbox"]["x"]["min"] and decoded["bbox"]["x"]["max"] <= 24 * VOXEL[2]
    assert decoded["bbox"]["z"]["max"] <= 12 * VOXEL[0]


def test_a_skeleton_decodes_as_line_segments():
    volumes, kinds = _volumes()
    rows = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                                options=MeshOptions(contact_max_um=None,
                                                    skeleton_entities=frozenset({"mito"})))
    skeleton = next(r["skeleton"] for r in rows if r.get("skeleton"))

    decoded = decode(skeleton, per_index=2)

    # Two indices per segment rather than three per face, and every one addresses a vertex.
    assert decoded["indices"] > 0
    assert decoded["maxIndex"] < decoded["vertices"]


def test_merging_two_instances_keeps_every_face_on_its_own_vertices():
    first = generate_mesh(_ball(4, (6, 6, 6)), (0, 0, 0), VOXEL)
    second = generate_mesh(_ball(3, (6, 18, 18)), (0, 0, 0), VOXEL)
    apart = decode(first), decode(second)

    merged = run_report_page({"merge": {"items": [
        {"base64": base64.b64encode(first).decode(), "rgb": [1, 0, 0]},
        {"base64": base64.b64encode(second).decode(), "rgb": [0, 0, 1]},
    ]}})["merge"]

    # One merged geometry per object, so the second instance's indices have to be shifted
    # past the first's vertices; unshifted, its faces point back into the first mesh.
    assert merged["vertices"] == apart[0]["vertices"] + apart[1]["vertices"]
    assert merged["indices"] == apart[0]["indices"] + apart[1]["indices"]
    assert merged["maxIndex"] == merged["vertices"] - 1
    # Colour rides on the vertices, which is what lets one geometry hold a whole palette.
    assert merged["colours"] == {"first": [1, 0, 0], "last": [0, 0, 1]}
    assert merged["normals"], "a merged surface needs normals to be lit at all"


def test_a_merge_past_65535_vertices_gets_an_index_type_that_can_hold_it():
    """A Uint16 index silently wraps, and the object folds in on itself."""
    one = generate_mesh(_ball(10), (0, 0, 0), VOXEL)
    small = decode(one)["vertices"]
    copies = 65535 // small + 2
    merged = run_report_page({"merge": {"items": [
        {"base64": base64.b64encode(one).decode(), "rgb": [1, 1, 1]} for _ in range(copies)
    ]}})["merge"]

    assert merged["vertices"] > 65535
    assert merged["indexType"] == "Uint32Array"
    assert merged["maxIndex"] == merged["vertices"] - 1


def test_exploding_moves_an_instance_without_reshaping_it():
    payload = generate_mesh(_ball(4, (6, 6, 6)), (0, 0, 0), VOXEL)
    alone = decode(payload)["bbox"]

    put = run_report_page({"merge": {"items": [
        {"base64": base64.b64encode(payload).decode(), "rgb": [1, 0, 0]},
        {"base64": base64.b64encode(payload).decode(), "rgb": [0, 0, 1],
         "offset": [0, 0, 5]},
    ]}})["merge"]["bbox"]

    # The explode slider offsets each instance along its own direction from the object
    # centre. Here the copy is pushed 5 µm in Z: the pair spans its own extent plus the
    # offset, and neither copy is stretched to get there.
    assert put["z"]["min"] == pytest.approx(alone["z"]["min"], abs=1e-5)
    assert put["z"]["max"] == pytest.approx(alone["z"]["max"] + 5, abs=1e-5)
    assert put["x"]["min"] == pytest.approx(alone["x"]["min"], abs=1e-5)
    assert put["x"]["max"] == pytest.approx(alone["x"]["max"], abs=1e-5)


def test_exploding_pushes_an_instance_out_the_way_it_actually_lies():
    """The offset is in mesh order (x, y, z), the same order polar_nx/ny/nz are read in.

    Reordering it to ZYX mirrored x against z, so instances flew off in directions they had
    never been in - and with the carried metrics missing from the geometry as well, the
    slider moved nothing at all.
    """
    offsets = run_report_page({"explodes": [
        {"dist": 3, "n": [1, 0, 0], "factor": 2.0},     # straight out along x
        {"dist": 3, "n": [0, 0, 1], "factor": 2.0},     # and along z
        {"dist": 3, "n": [1, 0, 0], "factor": 0},       # slider at zero
        {"dist": 0, "n": [1, 0, 0], "factor": 2.0},     # sitting on the centre
    ]})["explodes"]

    assert offsets[0] == pytest.approx([6, 0, 0])
    assert offsets[1] == pytest.approx([0, 0, 6])
    assert offsets[2] == [0, 0, 0]
    assert offsets[3] == [0, 0, 0]


# ── the SQL the page builds ───────────────────────────────────────────────────

def test_every_query_the_geometry_sections_build_runs(con, geometry_dir):
    """Every one of them, through DuckDB, against geometry a run would have written.

    A binder error is a failing test rather than a message in somebody's browser console,
    which is the reason these are built by the page and executed here.
    """
    statements = run_report_page({
        "geometry": files_in(geometry_dir, "object_a", "object_b"),
        "structure": "mito", "structures": ["mito", "pm"], "metric": "volume_um3",
    })["statements"]

    assert len(statements) >= 9, "a builder stopped being exported"
    for sql in statements:
        try:
            con.execute(sql).fetchall()
        except duckdb.Error as error:
            pytest.fail(f"the page builds SQL that does not run:\n{sql}\n\n{error}")


def test_a_geometry_file_says_which_object_it_holds(con, geometry_dir):
    """The folder name is the object id, but the id is a column too, and that is read.

    A renamed folder then still lines up with the report instead of quietly dropping out
    of the object selector.
    """
    files = files_in(geometry_dir, "object_a", "object_b")
    owners = con.execute(build("geometryOwnersSql", files)).fetchall()

    assert sorted(row[0] for row in owners) == ["object_a", "object_b"]
    assert {row[1] for row in owners} == set(files)


def test_the_object_view_summarises_an_object_without_reading_geometry(con, geometry_dir):
    rows = con.execute(build("sceneSummarySql",
                             source_of(geometry_dir, "object_a"))).fetchall()

    by_key = {(name, row_type): (n, faces) for name, row_type, n, faces in rows}
    # Three labelled instances and the membrane mask, with the face counts that let the
    # page budget its draw calls before a byte of geometry is transferred.
    assert by_key[("mito", "instance")][0] == 3
    assert by_key[("pm", "file")][0] == 1
    assert all(faces > 0 for _n, faces in by_key.values())


def test_the_object_view_reads_only_the_structures_it_draws(con, geometry_dir):
    source = source_of(geometry_dir, "object_a")
    # entity, row_type, label, then the metrics asked for, then the payloads.
    rows = con.execute(
        build("sceneGeometrySql", source, ["mito"], ["volume_um3"], 2)).fetchall()

    # The point of the sidecar: two meshes come back, not the object's worth.
    assert len(rows) == 2
    assert all(isinstance(row[4], (bytes, bytearray)) and len(row[4]) > 32 for row in rows)
    # And the biggest two, so a bounded draw shows the object rather than a random corner.
    volumes = [row[3] for row in con.execute(
        build("sceneGeometrySql", source, ["mito"], ["volume_um3"], 99)).fetchall()]
    assert volumes == sorted(volumes, reverse=True)
    assert [row[3] for row in rows] == volumes[:2]


def test_the_object_view_draws_nothing_it_was_not_asked_for(con, geometry_dir):
    source = source_of(geometry_dir, "object_a")
    names = {row[0] for row in con.execute(
        build("sceneGeometrySql", source, ["mito"], ["volume_um3"], 99)).fetchall()}

    # Turning a structure off has to actually stop it being read: the membrane mask is the
    # expensive one, and it is off by default.
    assert names == {"mito"}


def test_the_contact_edges_come_from_the_same_file(con, geometry_dir):
    source = source_of(geometry_dir, "object_a")
    widest = con.execute(build("sceneGapSql", source)).fetchone()[0]
    edges = con.execute(build("sceneEdgesSql", source, widest)).fetchall()

    # The object view colours by contact group without going back to the report: the edge
    # list rides in the geometry file, keyed the way the page keys an instance.
    assert widest is not None
    assert edges and all(a and b for a, _la, b, _lb in edges)


def test_a_narrower_gap_can_only_drop_edges(con, geometry_dir):
    source = source_of(geometry_dir, "object_a")
    widest = con.execute(build("sceneGapSql", source)).fetchone()[0]
    wide = con.execute(build("sceneEdgesSql", source, widest)).fetchall()
    tight = con.execute(build("sceneEdgesSql", source, 0)).fetchall()

    assert len(tight) <= len(wide)


def test_a_mesh_is_never_an_empty_blob(con, geometry_dir):
    empty = con.execute(
        f"""SELECT COUNT(*) FROM {source_of(geometry_dir, 'object_a')}
            WHERE "mesh" IS NOT NULL AND octet_length("mesh") = 0"""
    ).fetchone()[0]

    # "Has geometry" is one IS NOT NULL for the page; an empty blob would pass it and
    # then decode to nothing.
    assert empty == 0


def test_a_run_writes_geometry_the_explode_slider_can_use(tmp_path):
    """The whole path: the measurer writes the polarity, the page reads it and moves.

    The unit test above passes the direction in by hand. This one goes through the
    measurer, which is where it was lost: masks are measured in the same loop as labels and
    the mask branch overwrote them, so the slider had nothing to work with and quietly did
    nothing.
    """
    from label_anatomy.measure import load_object
    from label_anatomy.measure.instances import InstanceMeasurer
    from label_anatomy.measure.geometry import GeometryWriter
    from synthetic import make_object

    os.environ["LABEL_ANATOMY_MESH_DIR"] = str(tmp_path)
    folder = tmp_path / "src" / "object_a"
    make_object(folder, prefix="s", n_mito=3, mito_radii=(2.0, 3.0, 3.0))
    stack = load_object(folder)
    InstanceMeasurer().measure(stack)             # publishes the per-instance metrics
    written = GeometryWriter().measure(stack).columns["mesh_geometry_file"]

    rows = duckdb.connect().execute(
        f"""SELECT "polar_dist_um", "polar_nx", "polar_ny", "polar_nz"
            FROM read_parquet('{written}') WHERE "row_type" = 'instance'"""
    ).fetchall()

    assert rows, "the object has instances"
    # A distance to be pushed along, a direction to push in, and not the same one for all.
    assert all(distance is not None and distance > 0 for distance, *_ in rows)
    assert len({tuple(vector) for _, *vector in rows}) > 1


def test_the_palette_comes_from_the_report_not_the_geometry(report_path):
    """The geometry files carry no colour, and a structure's colour is the study's.

    One settings file, one colour per structure, the same in the table, the bars and the
    meshes - which works because the page takes it from the report's entity rows.
    """
    from conftest import MITO_COLOUR

    rows = duckdb.connect().execute(
        f"""SELECT DISTINCT "entity_name", "entity_colour" FROM read_parquet('{report_path}')
            WHERE "entity_colour" IS NOT NULL"""
    ).fetchall()

    assert rows == [("mito", MITO_COLOUR)]


# ── the gallery's sample ──────────────────────────────────────────────────────

def test_the_gallery_ranks_instances_across_every_object_in_scope(con, geometry_dir):
    source = source_of(geometry_dir, "object_a", "object_b")
    rows = con.execute(build("gallerySql", source, "mito", "sphericity", "highest", 4)).fetchall()

    # One query over both objects - a cohort's tail, not an object's, which is the whole
    # reason the gallery reads a list of files rather than one.
    assert len(rows) == 4
    assert {r[0] for r in rows} == {"object_a", "object_b"}
    assert [r[2] for r in rows] == sorted([r[2] for r in rows], reverse=True)


def test_the_gallery_can_ask_for_the_other_tail(con, geometry_dir):
    source = source_of(geometry_dir, "object_a", "object_b")
    highest = con.execute(
        build("gallerySql", source, "mito", "volume_um3", "highest", 1)).fetchone()[2]
    lowest = con.execute(
        build("gallerySql", source, "mito", "volume_um3", "lowest", 1)).fetchone()[2]

    assert lowest < highest


def test_the_gallery_shows_its_sample_in_metric_order(con, geometry_dir):
    """Both tails and a fair sample read top-down, because the order is applied last."""
    source = source_of(geometry_dir, "object_a", "object_b")
    for pick in ("highest", "lowest", "random"):
        values = [r[2] for r in
                  con.execute(build("gallerySql", source, "mito", "volume_um3", pick, 4)).fetchall()]

        assert values == sorted(values, reverse=True), pick


def test_a_random_sample_varies_between_draws(con, geometry_dir):
    source = source_of(geometry_dir, "object_a", "object_b")
    sql = build("gallerySql", source, "mito", "volume_um3", "random", 2)
    draws = {tuple((r[0], r[1]) for r in con.execute(sql).fetchall()) for _ in range(40)}

    # Not the same two instances every time, which is the whole difference from the tails.
    assert len(draws) > 1


def test_the_gallery_counts_out_of_every_instance_it_could_have_shown(con, geometry_dir):
    source = source_of(geometry_dir, "object_a", "object_b")
    total = con.execute(build("galleryCountSql", source, "mito", "volume_um3")).fetchone()[0]
    shown = con.execute(build("gallerySql", source, "mito", "volume_um3", "highest", 2)).fetchall()

    # "2 of 6" in the caption: the fraction is of the structure, not of the grid.
    assert total == 6, "three instances in each of two objects"
    assert len(shown) == 2


def test_the_gallery_shows_a_whole_structure_mask_once_per_object(con, geometry_dir):
    """A structure segmented as one piece has shapes to look at too, one per object.

    Its numbers are a bar per object and say nothing about what it looks like; six masks
    side by side is where a leaked segmentation shows itself.
    """
    source = source_of(geometry_dir, "object_a", "object_b")
    rows = con.execute(
        build("gallerySql", source, "pm", "volume_um3", "highest", 12, "file")).fetchall()
    total = con.execute(
        build("galleryCountSql", source, "pm", "volume_um3", "file")).fetchone()[0]

    assert total == 2, "one pm mask in each of two objects"
    assert {r[0] for r in rows} == {"object_a", "object_b"}
    # A mask is the whole structure, so it has no label number to caption a card with.
    assert all(r[1] is None for r in rows)
    assert all(r[2] > 0 for r in rows)


def test_the_gallery_asks_for_instances_unless_told_otherwise(con, geometry_dir):
    # The mask rows and the instance rows sit in one file, so the row type is what keeps
    # a pm mask out of the mito strip - and the default has to stay 'instance'.
    source = source_of(geometry_dir, "object_a", "object_b")
    default = con.execute(build("galleryCountSql", source, "pm", "volume_um3")).fetchone()[0]

    assert default == 0, "pm has no instances, only a mask"


def test_the_gallery_can_be_restricted_to_one_object(con, geometry_dir):
    # The filter narrows which geometry files are read at all, so the same query over one
    # object is what a filtered gallery runs.
    source = source_of(geometry_dir, "object_a")
    rows = con.execute(build("gallerySql", source, "mito", "volume_um3", "highest", 99)).fetchall()

    assert {r[0] for r in rows} == {"object_a"}


def test_the_gallery_asks_for_whole_rows_at_any_width():
    """The grid is auto-fill, so its column count belongs to the block, not to the gallery.

    A round number of thumbnails left the last row part empty at every width that did not
    happen to divide it, so it asks for rows and works the count out from the columns it got.
    """
    widths = list(range(1, 20))
    probe = run_report_page({"thumbBudget": [{"columns": c, "rows": r}
                                             for c in widths for r in (1, 2, 4)]})
    cap, offered = probe["maxThumbs"], probe["galleryRows"]
    asked = iter(probe["thumbBudget"])

    for columns in widths:
        for rows in (1, 2, 4):
            count = next(asked)
            assert count % columns == 0, f"{columns} columns x {rows} rows leaves a part row"
            assert 0 < count <= cap
    assert offered, "the gallery offers a choice of how many rows to draw"
