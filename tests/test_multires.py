import json
import zlib

import numpy as np
import polars as pl
import pytest
from skimage.measure import marching_cubes

from organella.geometry import multires
from organella.geometry.convert import convert_file, convert_folder
from organella.geometry.payloads import decode_quantised, payload_counts, quantised_payload
from organella.geometry.writer import write_parquet


def blob_mesh(size=56):
    """A bumpy closed surface of tens of thousands of vertices, in µm."""
    grid = np.mgrid[-1:1:size * 1j, -1:1:size * 1j, -1:1:size * 1j]
    field = np.sqrt((grid ** 2).sum(axis=0)) - 0.6 - 0.1 * np.sin(6 * grid[0]) * np.cos(5 * grid[1])
    vertices, faces, _, _ = marching_cubes(field, level=0.0)
    return (vertices * 0.2).astype(np.float32), faces.astype(np.int64)


@pytest.fixture(scope="module")
def mesh():
    return blob_mesh()


def test_a_big_mesh_gets_levels_each_coarser_than_the_one_before(mesh):
    xyz, faces = mesh
    levels, (coarse_xyz, _) = multires.build_levels(xyz, faces, fragment_vertices=400, coarsest_vertices=300)
    counts = [sum(f.vertices for f in level.fragments) for level in levels]
    assert len(levels) >= 3 and counts == sorted(counts, reverse=True)
    errors = [level.error_um for level in levels]
    assert errors == sorted(errors) and errors[-1] > errors[0]      # coarser means a longer edge
    assert len(coarse_xyz) < len(xyz) / 4
    assert len(levels[-1].fragments) < len(levels[0].fragments)     # a coarser level is cut into fewer, wider cells


def test_the_grids_are_nested_so_every_coarse_cell_is_the_union_of_finer_ones(mesh):
    xyz, faces = mesh
    levels, _ = multires.build_levels(xyz, faces, fragment_vertices=400, coarsest_vertices=300)
    assert len({level.origin for level in levels}) == 1
    for finer, coarser in zip(levels, levels[1:]):
        assert coarser.cell_um[0] == pytest.approx(2 * finer.cell_um[0])
    # A level's triangles are its own after decimation, so what nests is the grid: the cell of a coarse fragment is
    # the cell of its eight children, whichever of them hold anything.
    for level in levels[1:]:
        for fragment in level.fragments:
            assert all(c >= 0 for c in fragment.cell)
    finest, next_level = levels[0], levels[1]
    parents = {tuple(c // 2 for c in f.cell) for f in finest.fragments}
    assert len(parents) <= len(finest.fragments) and parents & {f.cell for f in next_level.fragments}


def test_the_fragments_of_a_level_together_are_the_whole_surface(mesh):
    xyz, faces = mesh
    levels, _ = multires.build_levels(xyz, faces, fragment_vertices=400, coarsest_vertices=300)
    finest = levels[0]
    assert len(finest.fragments) > 4
    assert sum(f.triangles for f in finest.fragments) == len(faces)          # every triangle in exactly one fragment
    area = 0.0
    for fragment in finest.fragments:
        vertices, tri = decode_quantised(zlib.decompress(fragment.payload, -zlib.MAX_WBITS))
        assert len(vertices) == fragment.vertices and len(tri) == fragment.triangles and tri.max() < len(vertices)
        corners = vertices[tri]
        area += 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    whole = 0.5 * np.linalg.norm(np.cross(xyz[faces][:, 1] - xyz[faces][:, 0], xyz[faces][:, 2] - xyz[faces][:, 0]), axis=1).sum()
    assert area == pytest.approx(whole, rel=1e-3)


def test_the_manifest_points_at_bytes_that_decode_to_the_fragment_it_describes(mesh, tmp_path):
    xyz, faces = mesh
    levels, _ = multires.build_levels(xyz, faces, fragment_vertices=400, coarsest_vertices=300)
    with multires.MeshFile(tmp_path / multires.FILENAME) as meshes:
        manifest = json.loads(meshes.add(levels))
        second = json.loads(meshes.add(levels))      # a second mesh lands after the first, not over it
    assert manifest["v"] == multires.MANIFEST_VERSION and manifest["file"] == multires.FILENAME
    assert second["levels"][0]["fragments"][0]["off"] > manifest["levels"][-1]["fragments"][-1]["off"]
    for level, described in zip(levels, manifest["levels"]):
        assert described["error"] == pytest.approx(level.error_um) and len(described["fragments"]) == len(level.fragments)
    entry = manifest["levels"][0]["fragments"][3]
    vertices, tri = multires.read_fragment(tmp_path / multires.FILENAME, entry)
    assert len(vertices) == entry["nv"] and len(tri) == entry["nt"]
    cell, origin = np.array(entry["ijk"]), np.array(manifest["levels"][0]["origin"])
    side = manifest["levels"][0]["cell"][0]
    centroids = vertices[tri].mean(axis=1)
    assert np.all(centroids >= origin + cell * side - 2e-4) and np.all(centroids <= origin + (cell + 1) * side + 2e-4)


def geometry_rows(mesh):
    xyz, faces = mesh
    big = quantised_payload(xyz, faces.astype(np.uint32))
    small = quantised_payload(xyz[:40], np.array([[0, 1, 2], [1, 2, 3]], np.uint32))
    return [{"object_id": "a", "entity_name": "er", "row_type": "instance", "label_id": 1, "surface_kind": "mesh", "surface": big},
            {"object_id": "a", "entity_name": "er", "row_type": "instance", "label_id": 2, "surface_kind": "mesh", "surface": small},
            {"object_id": "a", "entity_name": "vesicle", "row_type": "instance", "label_id": 3, "surface_kind": "ellipsoid",
             "surface": b"\0" * 60}]


def test_converting_geometry_changes_only_the_big_meshes_and_leaves_the_source_alone(mesh, tmp_path):
    rows = geometry_rows(mesh)
    source = write_parquet(tmp_path / "in" / "a" / "geometry.parquet", rows)
    before = source.read_bytes()
    destination = tmp_path / "out" / "a" / "geometry.parquet"
    assert convert_file(source, destination, min_vertices=5000, fragment_vertices=400, coarsest_vertices=300) == 1
    assert source.read_bytes() == before
    converted = pl.read_parquet(destination).sort("label_id")
    big, small, ellipsoid = converted.to_dicts()
    manifest = json.loads(big["multires"])
    assert payload_counts(big["surface"])[0] < payload_counts(rows[0]["surface"])[0] / 4     # the coarsest level, not the mesh
    assert big["surface_vertices"] == payload_counts(big["surface"])[0]
    assert len(manifest["levels"][-1]["fragments"]) < len(manifest["levels"][0]["fragments"])
    assert small["multires"] is None and small["surface"] == rows[1]["surface"]
    assert ellipsoid["multires"] is None and ellipsoid["surface"] == rows[2]["surface"]
    assert (destination.parent / multires.FILENAME).stat().st_size > 0
    assert destination.stat().st_size < source.stat().st_size


def test_a_folder_is_converted_object_by_object_and_never_onto_itself(mesh, tmp_path):
    write_parquet(tmp_path / "in" / "a" / "geometry.parquet", geometry_rows(mesh))
    write_parquet(tmp_path / "in" / "b" / "geometry.parquet", geometry_rows(mesh)[1:])
    made = convert_folder(tmp_path / "in", tmp_path / "out", min_vertices=5000, fragment_vertices=400, coarsest_vertices=300)
    assert made == {"a": 1, "b": 0}
    assert (tmp_path / "out" / "b" / "geometry.parquet").exists()
    with pytest.raises(ValueError, match="somewhere else"):
        convert_folder(tmp_path / "in", tmp_path / "in")


def test_a_run_writes_levels_for_its_big_meshes_and_none_when_asked_not_to(tmp_path):
    import dask.array as da
    from test_geometry import OneSample
    from organella.app.process import process
    from organella.app.settings import Settings, parse_geometry_as
    from organella.core import Kind, Sample, Structure, VoxelSize
    from organella.geometry.writer import FILENAME, GeometryWriter
    from organella.measure import DEFAULT
    from organella.sinks import ParquetSink

    z, y, x = np.indices((70, 70, 70))
    ball = ((z - 35) ** 2 + (y - 35) ** 2 + (x - 35) ** 2 <= 30 ** 2) * (1 + 0.15 * np.sin(z / 2.0) * np.cos(y / 2.5) > 0)
    labels = ball.astype(np.uint16)
    sample = Sample("blob", None, VoxelSize((0.1, 0.1, 0.1)), {"m": Structure("m", Kind.LABEL, da.from_array(labels, chunks=35))}, None)
    for name, minimum in (("on", 3000), ("off", 0)):
        settings = Settings(reach_um=0.5, chunk_edge=35, contacts=False, geometry_as=parse_geometry_as("m=mesh"),
                            mesh_target_reduction=0.0, multires_min_vertices=minimum, multires_fragment_vertices=1500)
        assert process(OneSample(sample), DEFAULT, ParquetSink(tmp_path / f"{name}.parquet"), settings, GeometryWriter(tmp_path / name)) == {}
        rows = pl.read_parquet(tmp_path / name / "blob" / FILENAME).filter(pl.col("row_type") == "instance").to_dicts()
        assert len(rows) == 1
        if minimum:
            manifest = json.loads(rows[0]["multires"])
            assert len(manifest["levels"]) >= 2 and (tmp_path / name / "blob" / multires.FILENAME).stat().st_size > 0
            assert payload_counts(rows[0]["surface"])[0] < multires_finest(manifest)
        else:
            assert rows[0]["multires"] is None and not (tmp_path / name / "blob" / multires.FILENAME).exists()


def multires_finest(manifest):
    """Vertices of the finest level: its fragments' counts added, so duplicated edge vertices count once per fragment."""
    return sum(f["nv"] for f in manifest["levels"][0]["fragments"])


def test_a_simplifier_that_stalls_ends_the_ladder_and_does_not_pad_it_with_wrecked_levels(monkeypatch, mesh):
    xyz, faces = mesh
    monkeypatch.setattr(multires, "_decimated", lambda x, f, r, **o: (x, f))     # what it does on a network of thin pieces
    levels, (coarse_xyz, coarse_faces) = multires.build_levels(xyz, faces, fragment_vertices=400, coarsest_vertices=300)
    assert len(levels) == 1 and len(coarse_xyz) == len(xyz)       # nothing coarser it could honestly offer


def test_no_level_has_folded_over_triangles():
    xyz, faces = blob_mesh()
    assert multires._folded(xyz, faces) < 0.001
    levels, (coarse_xyz, coarse_faces) = multires.build_levels(xyz, faces, fragment_vertices=1500, coarsest_vertices=300)
    assert len(levels) >= 2 and multires._folded(coarse_xyz, coarse_faces) <= multires.MAX_FOLDED
    scrambled = np.random.default_rng(0).permutation(len(xyz))      # a mesh whose triangles point every way is folded all over
    assert multires._folded(xyz, scrambled[faces]) > 0.3


def sheet_mesh():
    """A wavy sheet two voxels thick with holes in it, like ER: closed, but its two sides are a voxel or two apart."""
    from scipy import ndimage
    grid = np.mgrid[0:100, 0:100, 0:36].astype(np.float32)
    middle = 16 + 6 * np.sin(grid[0] / 7) * np.cos(grid[1] / 9)
    holes = ndimage.gaussian_filter(np.random.default_rng(2).normal(size=(100, 100, 36)).astype(np.float32), 4) > -0.012
    band = (np.abs(grid[2] - middle) < 1.4) & holes
    vertices, faces, _, _ = marching_cubes(ndimage.gaussian_filter(band.astype(np.float32), 0.7), level=0.5)
    return (vertices * 0.02).astype(np.float32), faces.astype(np.int64)


def surface_area(xyz, faces):
    corners = xyz[faces]
    return 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()


def pieces(xyz, faces):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(xyz)
    graph = coo_matrix((np.ones(len(faces) * 3), (np.r_[faces[:, 0], faces[:, 1], faces[:, 2]], np.r_[faces[:, 1], faces[:, 2], faces[:, 0]])), shape=(n, n))
    labels = connected_components(graph, directed=False)[1]
    return len(np.unique(labels[np.unique(faces)]))


def test_no_level_of_a_thin_sheet_surface_is_a_wreck_of_the_one_before_it():
    xyz, faces = sheet_mesh()
    assert len(xyz) > 20_000
    levels, (coarse_xyz, coarse_faces) = multires.build_levels(xyz, faces, fragment_vertices=1500, coarsest_vertices=300)
    assert len(levels) >= 2                                              # it still gets coarser, where it can
    assert surface_area(coarse_xyz, coarse_faces) > 0.7 * surface_area(xyz, faces)   # a sheet merged with its other side loses its area
    assert pieces(coarse_xyz, coarse_faces) <= 1.1 * pieces(xyz, faces) + 5          # and a level never has more pieces than the finest


def sheet_mask():
    """The mask ``sheet_mesh`` is the surface of: a wavy sheet about three voxels thick with holes in it."""
    from scipy import ndimage
    grid = np.mgrid[0:100, 0:100, 0:36].astype(np.float32)
    middle = 16 + 6 * np.sin(grid[0] / 7) * np.cos(grid[1] / 9)
    holes = ndimage.gaussian_filter(np.random.default_rng(2).normal(size=(100, 100, 36)).astype(np.float32), 4) > -0.012
    return ((np.abs(grid[2] - middle) < 1.4) & holes).transpose(2, 1, 0)


def test_a_thin_sheet_mask_gets_coarse_levels_sampled_from_its_field_that_still_cover_it():
    from organella.geometry.surfaces import generate_mesh_levels
    payload, coarser = generate_mesh_levels(sheet_mask(), (0, 0, 0), (0.02,) * 3, coarser=4, coarsest_vertices=500, min_vertices=1000)
    xyz, faces = decode_quantised(payload)
    assert len(coarser) >= 2
    sizes = [len(xyz)] + [len(c[0]) for c in coarser]
    assert all(b < a for a, b in zip(sizes, sizes[1:])) and sizes[-1] < sizes[0] / 8   # each far smaller: decimation could not
    first = surface_area(xyz, faces.astype(np.int64))
    for cx, cf in coarser:                      # none of them lost the sheet or fell apart into more pieces
        assert surface_area(cx, cf.astype(np.int64)) > 0.5 * first
        assert pieces(cx, cf.astype(np.int64)) <= 1.1 * pieces(xyz, faces.astype(np.int64)) + 5
    levels, _ = multires.build_levels(xyz, faces, fragment_vertices=1500, coarsest_vertices=500, coarser=coarser)
    assert len(levels) >= 1 + len(coarser)      # the volume's meshes are the levels after the first


def open_edges(faces):
    """How many edges belong to one triangle only: zero for a closed surface."""
    faces = faces.astype(np.int64)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    return int((np.unique(edges, axis=0, return_counts=True)[1] == 1).sum())


def test_every_coarse_level_of_a_mask_that_fills_its_box_is_a_closed_surface():
    from organella.geometry.surfaces import generate_mesh_levels
    mask = sheet_mask()
    mask[:, :, 0] = True            # touching the box on every side, as a structure cut off by the volume does
    mask[0], mask[-1], mask[:, 0], mask[:, -1] = True, True, True, True
    payload, coarser = generate_mesh_levels(mask, (0, 0, 0), (0.02,) * 3, coarser=4, coarsest_vertices=500, min_vertices=1000)
    assert len(coarser) >= 2 and open_edges(decode_quantised(payload)[1]) == 0
    assert [open_edges(cf) for _, cf in coarser] == [0] * len(coarser)


def test_a_small_mask_gets_no_coarser_meshes():
    from organella.geometry.surfaces import generate_mesh_levels
    ball = np.zeros((20, 20, 20), bool)
    ball[5:15, 5:15, 5:15] = True
    assert generate_mesh_levels(ball, (0, 0, 0), (0.1,) * 3, coarser=4)[1] == []


def test_the_stored_surface_is_the_finest_level_within_the_vertex_limit(mesh):
    levels, coarsest = multires.build_levels(*mesh, fragment_vertices=1500, coarsest_vertices=300)
    sizes = [sum(f.vertices for f in level.fragments) for level in levels]
    assert len(levels) >= 3
    _, kept = multires.build_levels(*mesh, fragment_vertices=1500, coarsest_vertices=300, stored_vertices=len(mesh[0]) // 3)
    assert len(coarsest[0]) < len(kept[0]) <= len(mesh[0]) // 3      # not the coarsest, and the finest that fits
    assert multires.build_levels(*mesh, fragment_vertices=1500, coarsest_vertices=300, stored_vertices=1)[1][0].shape == coarsest[0].shape
