"""How a shape is stored for drawing, and why it is not always a mesh.

A mesh per instance does not scale: one 512³ crop of a HeLa cell gave 2,698 meshes and
4.8 million vertices, and a whole macrophage holds 34,110 instances. Most of them are not
shapes that need a mesh - a vesicle at sphericity 1.0 *is* an ellipsoid - so a round
instance is stored as the ellipsoid of its own second moments, which ITK has already
computed, and tessellated by the viewer.

No measurement depends on any of this: volume, surface area and sphericity all come from
the voxels, and the surface is only ever drawn.
"""

import base64
import struct

import numpy as np
import pytest
from conftest import run_report_page as run_page

from label_anatomy.analysis.meshes import MeshOptions, mesh_rows_for_object
from label_anatomy.analysis.primitives import (
    ELLIPSOID_MAX_ASPECT,
    ELLIPSOID_MIN_SPHERICITY,
    SURFACE_KINDS,
    choose_surface,
    ellipsoid_payload,
)
from label_anatomy.config import forced_surface, parse_geometry_as
from synthetic import VOXEL_SIZE_UM

SHAPE = (40, 48, 48)


def _round_and_long():
    """Two physically round instances and one long rod, in anisotropic voxels.

    Round *in µm*, not in indices: a voxel ball under 0.1 x 0.02 x 0.02 µm sampling is a
    flat disc in space, and the selector is right to refuse to call it an ellipsoid.
    """
    sz, sy, sx = VOXEL_SIZE_UM
    labels = np.zeros(SHAPE, np.uint16)
    zz, yy, xx = np.ogrid[: SHAPE[0], : SHAPE[1], : SHAPE[2]]
    for i, (cz, cy, cx) in enumerate([(12, 12, 32), (28, 32, 12)], start=1):
        labels[((zz - cz) * sz) ** 2 + ((yy - cy) * sy) ** 2
               + ((xx - cx) * sx) ** 2 <= 1.2 ** 2] = i
    labels[19:21, 40:42, 4:44] = 3          # a rod: long in x, thin in y and z
    return {"blobs": labels}, {"blobs": "label"}


def _instances(geometry_as=None):
    volumes, kinds = _round_and_long()
    rows = mesh_rows_for_object(
        volumes, kinds, VOXEL_SIZE_UM, object_id="o",
        options=MeshOptions(contact_max_um=None, geometry_as=geometry_as or {}),
    )
    return [r for r in rows if r["row_type"] == "instance"]


# ── the selector, on its own ──────────────────────────────────────────────────

def test_a_round_compact_shape_is_an_ellipsoid():
    assert choose_surface(0.95, 1.2, None) == "ellipsoid"
    assert choose_surface(ELLIPSOID_MIN_SPHERICITY, 1.0, None) == "ellipsoid"


def test_a_shape_that_is_round_but_long_is_not():
    """Roundness alone cannot tell a ball from a smooth rod, and a rod drawn as the
    ellipsoid of its own moments is visibly too fat in the middle."""
    assert choose_surface(0.9, ELLIPSOID_MAX_ASPECT + 0.1, None) == "mesh"


def test_a_shape_that_is_not_round_is_a_mesh():
    # An ER sheet reads about 0.05 and a Golgi 0.14: shape is the whole point for those.
    assert choose_surface(0.05, 1.5, None) == "mesh"


def test_a_missing_metric_falls_back_to_a_mesh():
    """The fallback can only be wrong about nothing; a primitive chosen on a NaN would be
    wrong about a shape."""
    assert choose_surface(None, 1.0, None) == "mesh"
    assert choose_surface(float("nan"), 1.0, None) == "mesh"
    assert choose_surface(0.95, None, None) == "mesh"


# ── what a real object stores ─────────────────────────────────────────────────

def test_a_round_instance_is_stored_as_sixty_bytes():
    rounds = [r for r in _instances() if r["surface_kind"] == "ellipsoid"]

    assert rounds, "the round instances should not have been meshed"
    # centre, radii and a 3x3 of axes: 15 float32 and nothing else.
    assert all(len(r["surface"]) == 60 for r in rounds)


def test_an_elongated_instance_keeps_its_mesh():
    by_label = {r["label_id"]: r for r in _instances()}

    assert by_label[3]["surface_kind"] == "mesh"
    assert len(by_label[3]["surface"]) > 60


def test_the_ellipsoid_is_far_smaller_than_the_mesh_it_replaces():
    forced_mesh = {r["label_id"]: r for r in _instances({"blobs": "mesh"})}
    chosen = {r["label_id"]: r for r in _instances()}

    for label, row in chosen.items():
        if row["surface_kind"] != "ellipsoid":
            continue
        assert len(row["surface"]) * 20 < len(forced_mesh[label]["surface"])


# ── being told, rather than deciding ──────────────────────────────────────────

def test_naming_a_structure_overrides_what_its_shape_suggests():
    assert all(r["surface_kind"] == "mesh" for r in _instances({"blobs": "mesh"}))
    assert all(r["surface_kind"] == "ellipsoid"
               for r in _instances({"blobs": "ellipsoid"}))


def test_asking_for_a_skeleton_leaves_the_surface_to_the_shape():
    """`skeleton` asks for the centre line, not for a particular surface - and once a
    centre line exists, a filament can be drawn as the tube it is."""
    rows = _instances({"blobs": "skeleton"})
    by_label = {r["label_id"]: r for r in rows}

    assert any(r["skeleton"] for r in rows)
    assert by_label[3]["surface_kind"] == "tube"        # the rod
    assert by_label[1]["surface_kind"] == "ellipsoid"   # a round one


def test_a_tube_carries_a_radius_for_every_node_of_its_centre_line():
    """The radii are kimimaro's own, measured while skeletonising and previously thrown
    away, so a tube costs four bytes a node more than the skeleton already stored."""
    rows = _instances({"blobs": "tube"})
    tubes = [r for r in rows if r["surface_kind"] == "tube"]

    assert tubes
    for row in tubes:
        # skeleton payload + one float32 per vertex.
        assert len(row["surface"]) > len(row["skeleton"])
        assert (len(row["surface"]) - len(row["skeleton"])) % 4 == 0


def test_an_unknown_kind_is_an_error_naming_the_ones_there_are():
    with pytest.raises(ValueError, match="ellipsoid"):
        parse_geometry_as("mito=blancmange")


def test_a_pair_without_an_equals_sign_is_an_error():
    with pytest.raises(ValueError, match="NAME=KIND"):
        parse_geometry_as("mito")


def test_names_are_matched_however_they_were_capitalised():
    assert forced_surface("er", parse_geometry_as("ER=ellipsoid")) == "ellipsoid"


# ── the writer and the page have to agree ─────────────────────────────────────

def test_every_kind_the_writer_emits_is_one_the_page_can_draw():
    """Otherwise a kind vanishes silently in a browser, which is what nothing imports the
    page means in practice."""
    page = run_page({"rows": [], "structure": "mito"})["surfaceKinds"]

    unbuildable = sorted(set(SURFACE_KINDS) - set(page))
    assert not unbuildable, f"the page cannot draw: {unbuildable}"


def test_the_page_turns_an_ellipsoid_into_a_closed_surface_where_it_belongs():
    payload = ellipsoid_payload(
        centre_xyz=(10.0, 20.0, 30.0),
        diameters=(2.0, 4.0, 6.0),
        axes=(1, 0, 0, 0, 1, 0, 0, 0, 1),
    )
    out = run_page({"rows": [], "structure": "mito",
                    "drawables": [{"base64": base64.b64encode(payload).decode(),
                                   "kind": "ellipsoid"}]})["drawables"][0]

    assert out["vertices"] > 100 and out["indices"] % 3 == 0
    assert out["flat"] is False
    # Radii are half the diameters, so the box is the centre ± (1, 2, 3).
    box = out["bbox"]
    assert (box["x"]["min"], box["x"]["max"]) == pytest.approx((9.0, 11.0), abs=0.05)
    assert (box["y"]["min"], box["y"]["max"]) == pytest.approx((18.0, 22.0), abs=0.05)
    assert (box["z"]["min"], box["z"]["max"]) == pytest.approx((27.0, 33.0), abs=0.05)


def test_an_ellipsoid_payload_is_refused_rather_than_drawn_wrong(tmp_path):
    """A non-finite moment means no ellipsoid, and the writer falls back to a mesh."""
    assert ellipsoid_payload((0, 0, float("nan")), (1, 1, 1), (1, 0, 0, 0, 1, 0, 0, 0, 1)) == b""
    assert ellipsoid_payload((0, 0, 0), (1, 1), (1, 0, 0, 0, 1, 0, 0, 0, 1)) == b""


def test_a_collapsed_ellipsoid_is_refused_before_it_can_size_the_scene():
    """Two voxels have no moments: ITK zeroes two diameters and puts the volume in the third.

    The result is finite, so the finiteness check passes it, and it draws as a needle
    millions of µm long - one is enough to fit the camera to nothing anybody can see.
    """
    assert ellipsoid_payload((0.79, 1.3, 0.16), (0.0, 0.0, 6.9e6),
                             (0, -1, 0, 0, 0, 1, -1, 0, 0)) == b""
    assert ellipsoid_payload((0, 0, 0), (1, 0, 1), (1, 0, 0, 0, 1, 0, 0, 0, 1)) == b""
    # A thin one is still a shape, and is kept.
    assert ellipsoid_payload((0, 0, 0), (1e-4, 1, 1), (1, 0, 0, 0, 1, 0, 0, 0, 1)) != b""


def test_the_payload_is_exactly_the_numbers_it_says_it_is():
    payload = ellipsoid_payload((1, 2, 3), (4, 6, 8), tuple(range(9)))

    values = struct.unpack("<15f", payload)
    assert values[:3] == (1, 2, 3)
    assert values[3:6] == (2, 3, 4)          # radii, not diameters
    assert values[6:] == tuple(float(v) for v in range(9))


def test_the_page_sweeps_a_real_tube_into_a_surface():
    """The rod is 0.8 µm long in x and thin in y and z, so the swept tube should span its
    length and stay within a radius or so of the centre line across it."""
    rod = next(r for r in _instances({"blobs": "tube"})
               if r["label_id"] == 3 and r["surface_kind"] == "tube")
    out = run_page({"rows": [], "structure": "mito",
                    "drawables": [{"base64": base64.b64encode(rod["surface"]).decode(),
                                   "kind": "tube"}]})["drawables"][0]

    assert out is not None, "the page could not sweep the tube"
    assert out["vertices"] > 0 and out["indices"] % 3 == 0
    box = out["bbox"]
    length_x = box["x"]["max"] - box["x"]["min"]
    thickness_y = box["y"]["max"] - box["y"]["min"]
    assert length_x > thickness_y, "the tube should be longer than it is thick"


def test_a_surface_and_a_centre_line_are_asked_for_together():
    """A mesh with its skeleton inside it is the alpha-cell case, and the two are separate
    questions, so they combine rather than one implying the other."""
    rows = _instances({"blobs": "mesh+skeleton"})

    assert all(r["surface_kind"] == "mesh" for r in rows)
    assert any(r["skeleton"] for r in rows)


def test_an_instance_cannot_be_asked_to_be_two_surfaces():
    with pytest.raises(ValueError, match="one surface"):
        parse_geometry_as("mito=mesh+tube")


# ── how much a mesh costs, once it is the only thing left ─────────────────────

def test_an_index_is_two_bytes_until_it_cannot_be():
    """A triangle mesh has about twice as many faces as vertices, so the index array is
    three quarters of a payload - measured at exactly 30 bytes per vertex before this."""
    from label_anatomy.analysis.meshes import NARROW_INDEX_LIMIT, index_dtype

    assert index_dtype(NARROW_INDEX_LIMIT - 1) == np.uint16
    assert index_dtype(NARROW_INDEX_LIMIT) == np.uint32


def test_a_narrow_mesh_round_trips_through_every_reader():
    """The width is derived from the vertex count, not recorded, so writer and reader agree
    by construction - and the page has to agree too."""
    from label_anatomy.analysis.meshes import quantised_payload

    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32)
    faces = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    payload = quantised_payload(verts, faces)

    # 32-byte header, 4 vertices at 6 bytes, 2 faces at 3 narrow indices.
    assert len(payload) == 32 + 4 * 6 + 2 * 3 * 2
    out = run_page({"rows": [], "structure": "mito",
                    "drawables": [{"base64": base64.b64encode(payload).decode(),
                                   "kind": "mesh"}]})["drawables"][0]
    assert out["vertices"] == 4 and out["indices"] == 6


def test_a_surface_is_decimated_to_its_budget():
    """A decimation *fraction* bounds nothing: one ER sheet came out at 2.87 million
    vertices where a vesicle came out at 57. A budget bounds the worst case."""
    from label_anatomy.analysis.meshes import generate_mesh

    zz, yy, xx = np.ogrid[:60, :60, :60]
    blob = ((zz - 30) ** 2 + (yy - 30) ** 2 + (xx - 30) ** 2) <= 26 ** 2
    unbounded = generate_mesh(blob, (0, 0, 0), (0.02, 0.02, 0.02), step_size=1,
                              target_reduction=0.0, max_vertices=0)
    budgeted = generate_mesh(blob, (0, 0, 0), (0.02, 0.02, 0.02), step_size=1,
                             target_reduction=0.0, max_vertices=500)

    big, _ = struct.unpack_from("<II", unbounded, 0)
    small, _ = struct.unpack_from("<II", budgeted, 0)
    assert big > 2000, "the test blob should be big enough for the budget to bite"
    assert small <= 500 * 1.1, f"budget of 500 gave {small} vertices"


# ── what a swept tube has to get right ────────────────────────────────────────

def _swept(payload):
    return run_page({"rows": [], "structure": "mito",
                     "drawables": [{"base64": base64.b64encode(payload).decode(),
                                    "kind": "tube"}]})["drawables"][0]


class _Line:
    """The bits of a kimimaro skeleton a tube payload reads."""
    def __init__(self, vertices, edges, radii):
        self.vertices, self.edges, self.radii = vertices, edges, radii


def _straight_tube(n_nodes=20, radius=0.5):
    """A centre line with no branches, so ring count is the only thing being measured."""
    import numpy as np
    from label_anatomy.analysis.primitives import tube_payload

    verts = np.stack([np.zeros(n_nodes), np.zeros(n_nodes),
                      np.arange(n_nodes, dtype=float)], axis=1)   # (z, y, x)
    edges = np.stack([np.arange(n_nodes - 1), np.arange(1, n_nodes)], axis=1)
    return tube_payload(_Line(verts, edges, np.full(n_nodes, radius)))


def test_a_tube_shares_one_ring_between_the_segments_either_side():
    """An unstitched sleeve per segment leaves a seam at every node and draws twice the
    vertices to do it; one ring per node is both continuous and smaller."""
    payload = _straight_tube(n_nodes=20)
    out = _swept(payload)

    segments = 19
    per_segment_would_be = segments * 12 * 2          # two rings each, unshared
    rings_plus_two_end_balls = 20 * 12 + 2 * 7 * 9
    assert out["vertices"] == rings_plus_two_end_balls
    assert out["vertices"] < per_segment_would_be


def test_a_straight_tube_does_not_wander_off_its_axis():
    """A frame recomputed per segment turns each ring differently, which shears the tube
    apart; one propagated frame keeps every ring on the same axis."""
    payload = _straight_tube(n_nodes=20, radius=0.5)
    box = _swept(payload)["bbox"]

    # The line runs along x here; y and z should never exceed the radius.
    for axis in ("y", "z"):
        assert box[axis]["min"] == pytest.approx(-0.5, abs=0.02)
        assert box[axis]["max"] == pytest.approx(0.5, abs=0.02)


def test_a_tube_stays_within_its_own_radius_of_its_centre_line():
    """A twisting frame shears consecutive rings apart, which shows up as a surface that
    wanders further from the centre line than the radius it was given."""
    import numpy as np

    rod = next(r for r in _instances({"blobs": "tube"})
               if r["label_id"] == 3 and r["surface_kind"] == "tube")
    line, radii = _decode_tube(rod["surface"])
    out = _swept(rod["surface"])

    box = out["bbox"]
    biggest = float(np.max(radii))
    for axis, k in (("x", 0), ("y", 1), ("z", 2)):
        assert box[axis]["min"] >= float(line[:, k].min()) - biggest * 1.6
        assert box[axis]["max"] <= float(line[:, k].max()) + biggest * 1.6


def _decode_tube(payload):
    """The centre line and radii back out of a tube payload."""
    import numpy as np

    n_verts, n_edges = struct.unpack_from("<II", payload, 0)
    params = np.frombuffer(payload, dtype="<f4", count=6, offset=8)
    quantised = np.frombuffer(payload, dtype="<u2", count=n_verts * 3,
                              offset=32).reshape(n_verts, 3)
    line = quantised / 65535.0 * params[3:] + params[:3]
    radii = np.frombuffer(payload, dtype="<f4", count=n_verts,
                          offset=len(payload) - n_verts * 4)
    return line, radii


# ── impostors: what a payload becomes as instances ────────────────────────────

def test_only_shapes_with_a_closed_form_can_be_ray_cast():
    """An impostor solves the ray-surface intersection in the shader, so it needs a
    quadric. A mesh has no closed form, which is why impostors are a second path rather
    than a replacement for the first."""
    page = run_page({"rows": [], "structure": "mito"})

    assert sorted(page["impostorKinds"]) == ["ellipsoid", "tube"]
    assert "mesh" not in page["impostorKinds"]


def test_a_tube_becomes_one_capsule_per_segment():
    payload = _straight_tube(n_nodes=20, radius=0.5)
    out = run_page({"rows": [], "structure": "mito",
                    "impostors": [{"base64": base64.b64encode(payload).decode(),
                                   "kind": "tube"}]})["impostors"][0]

    assert out["capsules"] == 19                      # nodes - 1
    assert out["radius"] == pytest.approx(0.5, abs=1e-3)
    # Payload vertices are XYZ; the synthetic line runs along x.
    assert out["firstA"][0] == pytest.approx(0.0, abs=0.01)
    assert out["firstB"][0] == pytest.approx(1.0, abs=0.01)


def test_an_ellipsoid_needs_no_tessellation_at_all():
    payload = ellipsoid_payload((5.0, 6.0, 7.0), (2.0, 4.0, 8.0),
                                (1, 0, 0, 0, 1, 0, 0, 0, 1))
    out = run_page({"rows": [], "structure": "mito",
                    "impostors": [{"base64": base64.b64encode(payload).decode(),
                                   "kind": "ellipsoid"}]})["impostors"][0]

    assert out["centre"] == pytest.approx([5.0, 6.0, 7.0])
    assert out["radii"] == pytest.approx([1.0, 2.0, 4.0])


# ── the centre line a tube follows ────────────────────────────────────────────

def _median_turn(points):
    import numpy as np

    step = np.diff(points, axis=0)
    length = np.linalg.norm(step, axis=1)
    unit = step / np.maximum(length[:, None], 1e-12)
    cos = np.clip((unit[:-1] * unit[1:]).sum(1), -1, 1)
    return float(np.median(np.degrees(np.arccos(cos))))


def _lattice_walk(n=60, voxel=0.016, seed=0):
    """A straight filament as a skeleton actually walks it: quantised onto the voxel grid."""
    import numpy as np

    rng = np.random.default_rng(seed)
    jitter = np.round(rng.normal(0, 0.6, (n, 3))) * voxel
    jitter[:, 0] = 0
    line = np.stack([np.arange(n) * voxel, np.zeros(n), np.zeros(n)], 1)
    edges = np.stack([np.arange(n - 1), np.arange(1, n)], 1)
    return (line + jitter).astype("float32"), edges


def test_the_lattice_comes_out_of_a_centre_line():
    """A skeleton steps from voxel to voxel and turns about 34° at each one on real data.
    That zig-zag is what a swept tube shows as blocky, and it is in the data - no renderer
    can smooth a centre line that is not smooth."""
    from label_anatomy.analysis.primitives import smooth_centre_line
    import numpy as np

    points, edges = _lattice_walk()
    radii = np.full(len(points), 0.01, "float32")
    smoothed, _ = smooth_centre_line(points, edges, radii)

    assert _median_turn(points) > 30
    assert _median_turn(smoothed) < 8


def test_smoothing_does_not_move_an_end_or_a_junction():
    """A junction is where arms meet and an end is where the structure stops; moving either
    would pull the arms apart and shorten every filament."""
    from label_anatomy.analysis.primitives import smooth_centre_line
    import numpy as np

    points, edges = _lattice_walk(n=30)
    # A T: node 10 gains a third neighbour, so it is a junction and must not move.
    extra = np.array([[10, 30]])
    points = np.vstack([points, points[10] + np.array([0, 0.05, 0], "float32")])
    radii = np.full(len(points), 0.01, "float32")
    smoothed, _ = smooth_centre_line(points, np.vstack([edges, extra]), radii)

    assert np.allclose(smoothed[0], points[0])
    assert np.allclose(smoothed[29], points[29])
    assert np.allclose(smoothed[10], points[10]), "a junction must stay where the arms meet"


def test_a_tube_payload_stores_the_smoothed_line():
    payload = _straight_tube(n_nodes=20)
    line, _ = _decode_tube(payload)

    assert _median_turn(line) < 5


# ── how the isosurface is extracted ───────────────────────────────────────────

def _sphere_field(radius=20, size=64):
    import numpy as np
    from scipy.ndimage import distance_transform_edt

    zz, yy, xx = np.ogrid[:size, :size, :size]
    ball = ((zz - size // 2) ** 2 + (yy - size // 2) ** 2
            + (xx - size // 2) ** 2) <= radius ** 2
    padded = np.pad(ball, 2)
    return (distance_transform_edt(padded)
            - distance_transform_edt(~padded)).astype("float32")


def test_surface_nets_closes_the_surface_it_builds():
    """Every edge shared by exactly two triangles, or the mesh has holes and nothing
    downstream - decimation, normals, a Blender import - behaves."""
    import collections
    from label_anatomy.analysis.meshes import surface_nets

    _, faces = surface_nets(_sphere_field())

    edges = collections.Counter()
    for a, b, c in faces:
        for edge in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(edge))] += 1
    assert faces.size and not [n for n in edges.values() if n != 2]


def test_surface_nets_is_smoother_than_marching_cubes():
    """The reason to have it at all: one vertex per cell sits at the average of that cell's
    crossings, so a staircase boundary comes out less stepped."""
    import numpy as np
    from skimage.measure import marching_cubes
    from label_anatomy.analysis.meshes import surface_nets

    field = _sphere_field(radius=20)
    centre = np.array(field.shape) / 2.0
    mc_verts = marching_cubes(field, level=0.0, step_size=1)[0]
    sn_verts, _ = surface_nets(field)

    spread = lambda v: float(np.std(np.linalg.norm(v - centre, axis=1)))
    assert spread(sn_verts) < spread(mc_verts) * 0.85


def test_marching_cubes_stays_the_default():
    """On a real ER sheet the two came out 0.16% apart in vertices, and surface nets took
    66% longer - so it is offered, not imposed."""
    from label_anatomy.analysis.meshes import MeshOptions

    assert MeshOptions().surface_method == "marching-cubes"
