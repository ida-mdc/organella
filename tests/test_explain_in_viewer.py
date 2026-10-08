"""A chart can hand the viewer a description of itself, and the viewer draws it.

The drawing is WebGL and only a browser runs it, so what is pinned here is everything the
drawing stands on: the spec a chart hands over, the centre and axis an arrow is drawn from,
and the distance a per-voxel view colours by. The synthetic ring batch is the one the
polarity tests read: four mitochondria at 90° intervals about the centre, a nucleus off to
one side - so the answer is known before the page is asked.
"""

import math

import pytest
from conftest import _run
from conftest import (report_column_help as help_of, report_rows as rows_of,
                      run_report_page as run_page)
from synthetic import make_object, make_object_2d


def _ring(root, builder):
    for group, names in (("control", "ab"), ("treated", "cd")):
        for name in names:
            builder(root / group / ("object_" + name), prefix="sample_" + name, n_mito=4)
    return root


@pytest.fixture(scope="module")
def ring_rows(tmp_path_factory):
    root = _ring(tmp_path_factory.mktemp("explain_ring"), make_object)
    report = _run(root, root.parent / "explain_ring.parquet")
    return rows_of(report), help_of(report)


@pytest.fixture(scope="module")
def ring_rows_2d(tmp_path_factory):
    root = _ring(tmp_path_factory.mktemp("explain_flat"), make_object_2d)
    report = _run(root, root.parent / "explain_flat.parquet")
    return rows_of(report), help_of(report)


def _explained(rows_and_help, centre, **extra):
    rows, help_ = rows_and_help
    first = next(r["object_id"] for r in rows if r.get("row_type") == "object")
    return run_page({"rows": rows, "columnHelp": help_, "structure": "mito",
                     "objectNoun": "cell",
                     "explain": {"frame": {"object": str(first), "structure": "mito",
                                           "reference": "nucleus", "centre": centre,
                                           **extra}}}
                    )["explain"]


def _frame(rows_and_help, centre):
    return _explained(rows_and_help, centre)["frame"]


# ── the centre and the axis the arrows are drawn from ─────────────────────────


def test_the_centre_is_found_again_from_where_the_meshes_sit(ring_rows):
    """A geometry file does not carry the centre, so the viewer works it out.

    Each mesh sits at the centre plus its own direction x distance, so every one of them
    implies the centre, and the median of those is where the arrows start. Put the meshes
    anywhere and the centre has to follow them there.
    """
    for centre in ([0, 0, 0], [5.5, -3.25, 12.0]):
        frame = _frame(ring_rows, centre)
        assert frame["centre"] == pytest.approx(centre, abs=1e-4)


def test_the_axis_points_at_the_reference_and_the_angles_are_the_charts(ring_rows):
    """0° towards the reference: the same four at 45° and 135° the charts say."""
    frame = _frame(ring_rows, [1, 2, 3])
    assert math.hypot(*frame["axis"]) == pytest.approx(1, abs=1e-6)
    assert sorted(round(a) for a in frame["angles"]) == [45, 45, 135, 135]
    # Every direction was matched to a mesh, so every line can be coloured by the cone.
    assert frame["matched"] == 4
    # The nucleus sits 0.12·√2 µm off centre, and the arrow reaches at least that far.
    assert frame["refDist"] == pytest.approx(0.12 * math.sqrt(2), abs=0.02)
    assert frame["reach"] >= frame["refDist"]


def test_directions_that_cancel_have_no_mean_arrow(ring_rows):
    """R and V are the charts' own: both 0 here, and no direction to draw an arrow along."""
    frame = _frame(ring_rows, [0, 0, 0])
    assert frame["R"] == pytest.approx(0, abs=0.02)
    assert frame["V"] == pytest.approx(0, abs=0.02)


def test_a_plane_is_a_frame_with_no_third_axis(ring_rows_2d):
    """In 2D the meshes are flat, so the centre and the axis are too."""
    frame = _frame(ring_rows_2d, [4, 7, 0])
    assert frame["centre"] == pytest.approx([4, 7, 0], abs=1e-4)
    assert frame["axis"][2] == 0
    assert sorted(round(a) for a in frame["angles"]) == [45, 45, 135, 135]


# ── turning the cell into the chart ──────────────────────────────────────────


@pytest.fixture(scope="module")
def morph(ring_rows):
    return _explained(ring_rows, [2, -1, 4], morph=True)["morph"]


def test_the_frame_a_chart_is_drawn_in_is_orthonormal(morph):
    """The axis, the direction across it that the circles are drawn in, and the third."""
    frame = morph["frame"]
    for key in ("uw", "au", "aw"):
        assert frame[key] == pytest.approx(0, abs=1e-9)
    assert frame["un"] == pytest.approx(1) and frame["wn"] == pytest.approx(1)


def test_every_layout_begins_with_the_cell_as_it_is(morph):
    """At 0 nothing has moved: an instance is where its direction and distance put it."""
    for layout, moments in morph["layouts"].items():
        for got, here in zip(moments[0], morph["here"]):
            for axis in ("a", "u", "w"):
                assert got[axis] == pytest.approx(here[axis], abs=1e-4), layout


def test_the_half_plot_keeps_distance_and_angle_and_drops_the_side(morph):
    """Turned about the axis into one half-plane: all the plot leaves out, and nothing else."""
    moments = morph["layouts"]["half"]
    for moment in moments:
        for got, chart in zip(moment, morph["chart"]):
            assert got["norm"] == pytest.approx(chart["dist"], abs=1e-4)
            assert math.degrees(math.acos(max(-1, min(1, got["a"] / got["norm"])))) \
                == pytest.approx(chart["cone"], abs=1e-2)
    for got in moments[-1]:
        assert got["w"] == pytest.approx(0, abs=1e-4), "lying in the plane of the plot"
        assert got["u"] >= -1e-4, "on one side of the axis"


def test_the_circle_puts_each_instance_where_the_full_circle_chart_does(morph):
    """Its angle round the circle is the signed angle the chart computes, its radius the distance."""
    for got, chart in zip(morph["layouts"]["circle"][-1], morph["chart"]):
        assert got["w"] == pytest.approx(0, abs=1e-4)
        assert got["norm"] == pytest.approx(chart["dist"], abs=1e-4)
        assert math.degrees(math.atan2(got["u"], got["a"])) == pytest.approx(chart["signed"], abs=1e-2)


def test_the_directions_only_view_moves_each_instance_along_its_own_direction(morph):
    """Out to one common distance and no further: what is left once distance is set aside."""
    reach = morph["frame"]["reach"]
    for got, here in zip(morph["layouts"]["unit"][-1], morph["here"]):
        assert got["norm"] == pytest.approx(reach * 1.1, rel=1e-4)
        # The same direction it began in: the components keep their proportions.
        start = math.sqrt(here["a"] ** 2 + here["u"] ** 2 + here["w"] ** 2)
        for axis in ("a", "u", "w"):
            assert got[axis] / got["norm"] == pytest.approx(here[axis] / start, abs=1e-4)


def test_the_scatter_puts_angle_across_and_distance_up(morph):
    span = morph["frame"]["plotReach"]
    for got, chart in zip(morph["layouts"]["cart"][-1], morph["chart"]):
        assert got["u"] == pytest.approx(chart["dist"], abs=1e-4)
        assert got["a"] == pytest.approx(chart["cone"] / 180 * 2 * span - span, abs=1e-3)
        assert got["w"] == pytest.approx(0, abs=1e-4)


# ── averages count by size ─────────────────────────────────────────────────────


def test_a_mean_direction_counts_each_instance_by_its_size():
    """Three labels of one nucleus - one real, two specks - are the nucleus, not a third each."""
    units = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    axis = [1, 0, 0]
    sizes = [100.0, 1.0, 1.0]
    got = run_page({"explain": {"weights": {"units": units, "axis": axis, "weights": sizes,
                                            "values": [1, 2, 3], "sizes": sizes,
                                            "quantiles": [0.5]}}})["explain"]["weights"]
    total = sum(sizes)
    assert got["indices"]["V"] == pytest.approx(100 / total)
    mean = [100 / total, 1 / total, 1 / total]
    assert got["indices"]["R"] == pytest.approx(math.sqrt(sum(m * m for m in mean)))
    # Unweighted, the specks are two thirds of the vote, and V is a third.
    assert got["plain"]["V"] == pytest.approx(1 / 3)


def test_a_weighted_median_is_where_half_the_size_lies():
    sizes = [1.0, 1.0, 10.0, 1.0]
    values = [1, 2, 3, 4]
    got = run_page({"explain": {"weights": {"units": [[1, 0, 0]], "axis": [1, 0, 0],
                                            "weights": [1.0], "values": values, "sizes": sizes,
                                            "quantiles": [0.5, 0.05, 0.99]}}})["explain"]["weights"]
    assert got["quantiles"] == [3, 1, 4]


def test_a_size_is_a_volume_or_an_area_and_nothing_else_counts_as_one():
    got = run_page({"explain": {"weights": {"units": [[1, 0, 0]], "axis": [1, 0, 0],
                                            "weights": [1.0], "values": [1], "sizes": [1.0],
                                            "quantiles": [0.5]}}})["explain"]["weights"]
    assert got["sized"] == [4, 5, None, None]
    # Sized and unsized mixed give every one the same say, not the sized ones all of it.
    assert got["partial"] == [1, 1, 1]
    assert got["full"] == [2, 3]


def test_the_reference_centre_is_the_sized_mean_of_its_instances(ring_rows):
    """One big label and three specks put the structure where the big one is."""
    rows, help_ = ring_rows
    first = next(r["object_id"] for r in rows if r.get("row_type") == "object")
    got = run_page({"rows": rows, "columnHelp": help_, "structure": "mito",
                    "objectNoun": "cell",
                    "explain": {"offset": {"object": str(first), "structure": "mito",
                                           "sizes": {"even": [1, 1, 1, 1],
                                                     "one_big": [1000, 1, 1, 1]}}}}
                   )["explain"]["offset"]
    vectors = [[c * r["dist"] for c in r["unit"]] for r in got["rows"]]
    # The four sit at 90° intervals about the centre, so evenly weighted they cancel.
    assert got["even"] is None
    big = got["one_big"]
    expected = [sum(w * v[k] for w, v in zip([1000, 1, 1, 1], vectors)) / 1003 for k in range(3)]
    length = math.sqrt(sum(c * c for c in expected))
    assert big["dist"] == pytest.approx(length, rel=1e-4)
    assert big["unit"] == pytest.approx([c / length for c in expected], abs=1e-4)


# ── what a chart says about itself ────────────────────────────────────────────


@pytest.fixture(scope="module")
def specs(ring_rows):
    rows, help_ = ring_rows
    calls = [
        {"fn": "metricSpec", "args": ["mito", "volume_um3"]},
        {"fn": "distanceSpec", "args": ["mito", "distance_to_nucleus_um"]},
        {"fn": "distanceSpec", "args": ["mito", "distance_to_closest_same_type_um"]},
        {"fn": "bothSpec", "args": ["mito", "distance_to_nucleus_mean_um",
                                    "distance_to_closest_same_type_um"]},
        {"fn": "groupSpec", "args": ["mito"]},
        {"fn": "structureSpec", "args": ["nucleus"]},
        {"fn": "polaritySpec", "args": ["mito", "nucleus", "angle"]},
        {"fn": "polaritySpec", "args": ["mito", "nucleus", "index"]},
        {"fn": "polaritySpec", "args": ["mito", "nucleus", "map", "a"]},
        {"fn": "polaritySpec", "args": ["mito", "nucleus", "map", "b"]},
    ]
    return run_page({"rows": rows, "columnHelp": help_, "structure": "mito",
                     "objectNoun": "cell", "explain": {"specs": calls}})["explain"]["specs"]


def test_two_charts_about_different_things_are_not_the_same_pin(specs):
    """Pressing a chart's button twice lets go, so a key must tell charts apart."""
    keys = [s["key"] for s in specs]
    assert len(set(keys)) == len(keys)


def test_a_chart_asks_for_what_it_measures_against_and_nothing_else(specs):
    by_kind = {}
    for spec in specs:
        by_kind.setdefault(spec["kind"], []).append(spec)
    assert set(by_kind["distance"][0]["entities"]) >= {"mito", "nucleus"}
    assert by_kind["distance"][0]["shown"] == ["mito"]
    # The structure's own kind is a target of itself, and is not asked for twice.
    own = by_kind["distance"][1]
    assert own["entities"].count("mito") == 1
    assert set(by_kind["polarity"][0]["entities"]) >= {"mito", "nucleus"}
    assert set(by_kind["polarity"][0]["shown"]) == {"mito", "nucleus"}


def test_what_a_chart_says_about_itself_has_no_figures_in_it(specs):
    """The words say what is drawn; the viewer's readout is where counts are.

    The one number is the zero of an angle, which is a definition and not a measurement.
    """
    for spec in specs:
        assert spec["title"] and spec["text"]
        assert not any(ch.isdigit() for ch in spec["text"].replace("0°", "")), spec["text"]
