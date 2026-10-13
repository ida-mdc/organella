"""The geometry helpers of the beta-cell landing page's builder: reading the report's payloads and placing a cell."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from organella.analysis.meshes import quantised_payload
from organella.analysis.primitives import ellipsoid_payload

BUILD = Path(__file__).resolve().parents[1] / "examples" / "betacells_landing" / "build.py"
spec = importlib.util.spec_from_file_location("betacells_landing_build", BUILD)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_a_surface_payload_reads_back_to_its_vertices_within_the_quantisation():
    verts = np.random.default_rng(0).uniform(0, 10, (40, 3)).astype(np.float32)
    faces = np.array([[0, 1, 2], [3, 4, 5]])

    v, f = build.decode_mesh(quantised_payload(verts, faces))

    assert np.abs(v - verts).max() < 10 / 65535 * 1.01
    assert (f == faces).all()


def test_an_ellipsoid_is_read_as_the_sphere_of_the_same_volume():
    payload = ellipsoid_payload((1.0, 2.0, 3.0), (2.0, 4.0, 8.0), np.eye(3).ravel())

    centre, radius = build.decode_ellipsoid(payload)

    assert np.allclose(centre, (1.0, 2.0, 3.0))
    assert np.isclose(radius, np.cbrt(1.0 * 2.0 * 4.0))


def test_thinning_a_centre_line_keeps_its_ends_and_branches():
    # A line of 21 nodes with a side branch off node 10.
    verts = np.arange(24 * 3, dtype=np.float32).reshape(24, 3)
    edges = [(i, i + 1) for i in range(20)] + [(10, 21), (21, 22), (22, 23)]

    v, e = build.thin_lines(verts, np.array(edges), 8)

    kept = {tuple(row) for row in v}
    for end in (0, 20, 23):
        assert tuple(verts[end]) in kept, "an end of a line is never dropped"
    assert tuple(verts[10]) in kept, "nor is the node a branch leaves from"
    assert len(v) < len(verts)
    assert e.max() < len(v) and (e[:, 0] != e[:, 1]).all()


def test_the_blob_puts_every_array_on_a_four_byte_boundary():
    blob = build.Blob()

    first = blob.add(np.arange(3, dtype=np.uint16), "<u2")
    second = blob.add(np.arange(2, dtype=np.float32), "<f4")

    assert first["at"] == 0 and second["at"] % 4 == 0
    data = b"".join(blob.parts)
    assert np.frombuffer(data, "<f4", 2, second["at"]).tolist() == [0.0, 1.0]
