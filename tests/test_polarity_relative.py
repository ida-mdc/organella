"""Direction, made comparable between objects.

Polarity is measured from the object mask's own centroid, which is the right origin but
points wherever the volume happened to be oriented: an azimuth of 40° means nothing from one
object to the next. Measured against the direction to a *structure* it does, and that is a
rotation of what the run already wrote - so these tests are about the page, and the numbers
they check come out of the report as it stands.

The synthetic batch is built for this by accident and is worth reading before the
assertions: four mitochondria sit at 90° intervals on a ring around the centre, and the
nucleus sits off-centre along -y -x. So the mitochondria are two at 45° to the nucleus axis
and two at 135°, their mean direction cancels exactly, and the nucleus is the only structure
in the batch that can serve as a reference at all.
"""

import math

import pytest
# _run is the suite's one real pipeline pass; these need a batch of their own, because the
# shared one gives its two groups three mitochondria and four, and the geometry with a
# known answer is the four.
from conftest import _run
from conftest import (report_column_help as help_of, report_rows as rows_of,
                      run_report_page as run_page)
from synthetic import make_object, make_object_2d


def _ring(root, builder):
    """Two groups of two objects, every one with four mitochondria on a ring."""
    for group, names in (("control", "ab"), ("treated", "cd")):
        for name in names:
            builder(root / group / ("object_" + name), prefix="sample_" + name, n_mito=4)
    return root


@pytest.fixture(scope="module")
def ring_report(tmp_path_factory):
    root = _ring(tmp_path_factory.mktemp("ring"), make_object)
    return _run(root, root.parent / "ring.parquet")


@pytest.fixture(scope="module")
def ring_report_2d(tmp_path_factory):
    root = _ring(tmp_path_factory.mktemp("ring_flat"), make_object_2d)
    return _run(root, root.parent / "ring_2d.parquet")


@pytest.fixture(scope="module")
def polar(ring_report):
    return run_page({"rows": rows_of(ring_report), "columnHelp": help_of(ring_report),
                     "structure": "mito", "objectNoun": "cell",
                     "polarity": {"structure": "mito"}})["polarity"]


@pytest.fixture(scope="module")
def polar_2d(ring_report_2d):
    return run_page({"rows": rows_of(ring_report_2d), "columnHelp": help_of(ring_report_2d),
                     "structure": "mito", "objectNoun": "cell",
                     "polarity": {"structure": "mito"}})["polarity"]


def test_a_structure_on_the_centre_is_no_reference(polar):
    """An axis needs a structure that sits off the centre; the ring of mitochondria does not.

    Their mean offset cancels to nothing, so there is no direction to measure against and
    the structure is not offered - where the nucleus, sitting to one side, is.
    """
    assert [r["name"] for r in polar["references"]] == ["nucleus"]
    assert polar["chosen"] == "nucleus"


def test_the_reference_axis_is_the_offset_the_run_measured(polar):
    """Built in the page out of the report's own columns, so it has to come back exact.

    The synthetic nucleus sits six voxels along -y and six along -x of a 0.02 µm grid, so
    its centre is 0.12·√2 µm off the object centre.
    """
    for axis in polar["axes"].values():
        assert axis["of"] == "centre"          # a mask has one centre and no instances
        assert axis["dist"] == pytest.approx(0.12 * math.sqrt(2), abs=0.02)


def test_references_are_offered_by_coverage_before_distance(polar):
    """A reference only some objects have takes the rest of the batch out of the section.

    Ordering by distance alone once put a structure that sat in one cell of seven first,
    which answered the question for that cell and called it the answer. Both numbers travel
    with every choice so a one-object reference stays available and stays visible as one.
    """
    references = polar["references"]
    for r in references:
        assert r["objects"] <= r["of"]
    covered = [(-r["objects"], -r["dist"]) for r in references]
    assert covered == sorted(covered)


def test_the_angle_is_measured_against_that_axis(polar):
    """0° towards the reference, 180° away - and this batch puts its four at 45° and 135°."""
    for per_object in polar["perObject"]:
        low, high = per_object["coneRange"]
        assert low == pytest.approx(45, abs=1.5)
        assert high == pytest.approx(135, abs=1.5)


def test_directions_that_cancel_have_no_polarity_and_no_mean(polar):
    """R and V of a population spread evenly about the axis, which is what R is for.

    Polarity-JaM's two numbers: R is 0 when the directions cancel and 1 when they agree, V
    is the same strength signed towards the reference. Four instances at 45, 45, 135 and
    135° cancel in both.
    """
    for per_object in polar["perObject"]:
        assert per_object["n"] == 4
        assert per_object["R"] == pytest.approx(0, abs=0.02)
        assert per_object["V"] == pytest.approx(0, abs=0.02)
        # No mean direction to take an angle of, rather than an arbitrary one.
        assert per_object["meanCone"] is None


def test_the_frame_a_circular_plot_is_drawn_in_is_orthonormal(polar):
    """The plot maps a direction onto one plane: the axis, and one direction across it."""
    for per_object in polar["perObject"]:
        frame = per_object["frame"]
        assert frame["axisNorm"] == pytest.approx(1, abs=1e-6)
        assert frame["acrossNorm"] == pytest.approx(1, abs=1e-6)
        assert frame["dot"] == pytest.approx(0, abs=1e-9)
        # And the signed angles it produces are angles.
        for angle in per_object["signed"]:
            assert -180 <= angle <= 180


def test_a_plane_has_no_arbitrary_rotation_to_choose(polar_2d):
    """In 2D the axis and its perpendicular are the whole frame, so the angles are exact.

    Same four-on-a-ring layout, so the same reading: the angles come out at 45° and 135° to
    the nucleus axis and cancel.
    """
    assert polar_2d["chosen"] == "nucleus"
    for per_object in polar_2d["perObject"]:
        low, high = per_object["coneRange"]
        assert low == pytest.approx(45, abs=2)
        assert high == pytest.approx(135, abs=2)
        assert per_object["R"] == pytest.approx(0, abs=0.05)
        frame = per_object["frame"]
        assert frame["dot"] == pytest.approx(0, abs=1e-9)
        assert len(per_object["signed"]) == per_object["n"]


# ── the section, drawn ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def drawn_polarity(ring_report):
    return run_page({"rows": rows_of(ring_report), "columnHelp": help_of(ring_report),
                     "structure": "mito", "objectNoun": "cell", "render": True,
                     "groupBy": "group"})["render"]


def test_the_section_draws_all_four_readings(drawn_polarity):
    """The angle, the indices, the two together, and one circle per object."""
    titles = drawn_polarity["titles"]

    assert "mito angle to nucleus" in titles
    assert "mito V towards nucleus" in titles
    assert "mito distance by angle" in titles
    # One circular map per object, titled by the object it is of.
    assert len([t for t in titles if t.startswith("object_")]) == 4
    assert "s-polarity" in drawn_polarity["shown"]
    assert drawn_polarity["failed"] == []


def test_the_isotropic_reference_is_drawn_on_every_angle_panel(drawn_polarity):
    """90°: half of a direction-blind population sits beyond it, whatever the shape.

    That is geometry rather than a measurement, which is why it needs no baseline measured
    against the object - and why it is the one reference these panels can carry.
    """
    angle_panels = [r for r in drawn_polarity["references"] if "angle to" in r["title"]]

    assert angle_panels
    for panel in angle_panels:
        assert panel["at"] == [90] * len(panel["at"])


def test_the_reader_is_told_which_way_is_arbitrary(drawn_polarity):
    """The rotation about the axis is not in the data, and a reader must not read it."""
    said = " ".join(drawn_polarity["prose"])
    assert "arbitrary" in said


def test_a_single_value_panel_still_shows_its_reference(report_path):
    """A structure only one object has is drawn as a bar, and the line has to be on it.

    The isotropic reference is at 90° whatever the data does, so on a bar reaching 20° it
    sits off an autoranged panel entirely - and a reader sees a bar and a dotted line at the
    very top edge, with nothing saying it is 90.
    """
    drawn = run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path),
                      "structure": "mito", "objectNoun": "cell", "render": True,
                      "groupBy": "group"})["render"]
    referenced = [r for r in drawn["references"] if "angle to" in r["title"]]

    assert referenced
    for panel in referenced:
        assert panel["at"] == [90] * len(panel["at"])
        # On a panel whose own values stop short of the line, the axis was widened to hold
        # it; where the data already spans it, Plotly is left to range the panel itself.
        if panel["yRange"]:
            low, high = panel["yRange"]
            assert low <= 90 <= high, panel["title"]
    # And at least one panel in this batch is that case: mtoc_cilium is in one object of
    # four, is drawn as a single bar, and reaches nowhere near 90°.
    assert [p for p in referenced if p["yRange"] and p["yRange"][1] >= 90]


# ── the radius, and which group an object is in ─────────────────────────────


def test_each_circular_map_says_which_group_its_object_is_in(drawn_polarity):
    """The panels are titled by the object; the comparison they are for is by group."""
    said = " ".join(drawn_polarity["prose"])

    assert "control" in said or "treated" in said


def test_the_radius_can_be_read_to_the_boundary_instead(report_path):
    """Cells differ in size, so "0.4 µm inside the membrane" travels where "5 µm out" does not.

    The depth is a distance row, which only instances have, so this view drops the masks -
    and says so rather than quietly drawing fewer structures.
    """
    page = open("src/organella/report/organella_report.html", encoding="utf-8").read()

    # Reversed on purpose: the boundary belongs at the rim, with deeper further in.
    assert "range: toMask ? [reach * 1.02, 0] : [0, reach * 1.02]" in page
    assert "Structures measured as one whole mask are left out of this view" in page
    # And the choice is only offered where there is a depth to read.
    assert "if (hasDepth) radiusChoices.push" in page
