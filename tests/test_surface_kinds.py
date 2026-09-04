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
