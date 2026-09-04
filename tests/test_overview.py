"""The overview: what the report says was segmented, and what it warns about.

Its whole point is to make an uneven batch obvious *before* anything is pooled across it,
so the cases worth pinning are the uneven ones: an object missing a structure, a mask with
no instances to count, a structure only one object has, a batch mixing planes and volumes,
and a run that measured contacts for some objects and not others. Pooled into a violin
every one of those disappears.
"""

import duckdb
import pytest
from conftest import run_report_page
from test_contacts_view import contact_row, entity_row, instance_row, object_row


def overview_of(rows) -> dict:
    return run_report_page({"rows": rows, "structure": "mito"})["overview"]


def coverage_notes(rows) -> dict:
    """The sentence each section says about what the run did not measure, or ''."""
    return run_report_page({"rows": rows, "structure": "mito"})["coverageNotes"]


def batch(*, objects=("object_a", "object_b"), entities=("pm", "nucleus", "mito"),
          instances=True, contacts=True, dims=None, drop=()):
    """A batch as rows, with pieces of it left out on purpose."""
    rows = []
    for object_id in objects:
        row = object_row(object_id)
        if dims and object_id in dims:
            row["spatial_dims"] = dims[object_id]
        rows.append(row)
        for entity in entities:
            if (object_id, entity) in drop:
                continue
            kind = "mask" if entity in ("pm", "nucleus") else "label"
            rows.append(entity_row(object_id, entity, kind=kind))
            if kind == "label" and instances:
                rows.append(instance_row(object_id, entity, 1))
                rows.append(instance_row(object_id, entity, 2))
        if contacts and instances and "mito" in entities:
            rows.append(contact_row(object_id, "mito", 1, 2))
    return rows


# ── what was segmented ────────────────────────────────────────────────────────

def test_an_even_batch_has_nothing_to_warn_about():
    summary = overview_of(batch())

    assert summary["missing"] == []
    assert summary["mixedDims"] is False
    assert all(c["present"] == c["of"] for c in summary["coverage"])


def test_a_structure_one_object_lacks_is_called_missing():
    summary = overview_of(batch(drop=[("object_b", "mito")]))

    # The column stays, so the gap is visible in the row rather than the structure
    # disappearing from the table altogether.
    assert "mito" in summary["structures"]
    assert summary["missing"] == [{"object": "object_b", "structures": ["mito"]}]


def test_a_structure_only_one_object_has_still_gets_a_column():
    rows = batch(objects=("object_a",), entities=("pm", "mito"))
    rows += batch(objects=("object_b",), entities=("pm",))

    summary = overview_of(rows)

    assert "mito" in summary["structures"]
    assert summary["missing"] == [{"object": "object_b", "structures": ["mito"]}]


def test_structures_are_ordered_masks_first_and_the_object_mask_before_them():
    """The table is read left to right, so the thing everything is measured against leads."""
    summary = overview_of(batch())

    assert summary["objectMask"] == "pm"
    assert summary["structures"] == ["pm", "nucleus", "mito"]
    assert summary["kinds"] == ["mask", "mask", "label"]


def test_a_mask_has_no_instances_to_count():
    summary = overview_of(batch())

    # Two label instances per object and no more: a whole-structure mask is one thing, and
    # counting it as one instance would put a 1 in a column of thousands.
    assert summary["instances"] == 4


# ── a batch that mixes planes and volumes ─────────────────────────────────────

def test_one_dimensionality_is_not_a_warning():
    assert overview_of(batch())["mixedDims"] is False


def test_mixing_2d_and_3d_objects_is_called_out():
    summary = overview_of(batch(objects=("object_a", "object_b", "flat_c"),
                                dims={"flat_c": 2}))

    # An area and a volume in one distribution is meaningless, and the section that exists
    # to catch an unpoolable batch has to say so rather than charting one and dropping
    # the other.
    assert summary["mixedDims"] is True
    assert summary["dims"] == {"3": 2, "2": 1}


# ── what a run did not measure ────────────────────────────────────────────────

def test_full_coverage_is_reported_as_full():
    coverage = overview_of(batch())["coverage"]

    assert {c["label"]: (c["present"], c["of"]) for c in coverage} == {
        "per-instance measurements": (2, 2), "contacts": (2, 2)}


def test_an_object_without_contacts_is_counted():
    rows = batch(objects=("object_a",))
    rows += batch(objects=("object_b",), contacts=False)

    coverage = overview_of(rows)["coverage"]

    assert {c["label"]: (c["present"], c["of"]) for c in coverage} == {
        "per-instance measurements": (2, 2), "contacts": (1, 2)}


def test_a_run_with_no_instances_at_all_is_counted(report_path):
    rows = batch(instances=False, contacts=False)

    coverage = overview_of(rows)["coverage"]

    assert {c["label"]: c["present"] for c in coverage} == {
        "per-instance measurements": 0, "contacts": 0}


# ── where the caveat is said ──────────────────────────────────────────────────
#
# A gap in coverage qualifies one section, so it is said in that section. Said at the top
# of the report instead it lands before the reader has seen anything it could be about.

def test_a_gap_is_named_where_the_section_it_qualifies_is():
    rows = batch(objects=("object_a",))
    rows += batch(objects=("object_b",), contacts=False)

    notes = coverage_notes(rows)

    assert "1 of 2" in notes["contacts"]
    assert notes["per-instance measurements"] == ""


def test_full_coverage_says_nothing_anywhere():
    notes = coverage_notes(batch())

    assert notes == {"per-instance measurements": "", "contacts": ""}


def test_the_caveat_is_drawn_in_its_section_and_not_at_the_top():
    rows = batch(objects=("object_a",))
    rows += batch(objects=("object_b",), contacts=False)

    caveats = run_report_page(
        {"rows": rows, "structure": "mito", "render": True})["render"]["caveats"]

    assert "1 of 2" in caveats["ct-coverage"]
    assert "have contacts" not in (caveats["overview-warnings"] or "")
    assert caveats["entity-coverage"] == ""


# ── colour ────────────────────────────────────────────────────────────────────

def test_a_structure_is_drawn_in_the_colour_the_report_gives_it(report_path):
    """A study says what its structures look like, and the report carries the answer.

    --colours lands it on the entity rows, so a swatch in the table, a box in a chart and a
    mesh in the 3D view are one colour with no second file to pass around.
    """
    from conftest import MITO_COLOUR, report_rows

    summary = overview_of(report_rows(report_path))

    assert summary["colours"]["mito"] == MITO_COLOUR
    # A structure the settings file did not name keeps its place in the built-in palette.
    assert summary["colours"]["nucleus"] != MITO_COLOUR
    assert summary["colours"]["nucleus"].startswith("#")


def test_the_palette_does_not_run_out():
    """A batch with more structures than hand-picked colours still gets distinct ones.

    The hand-picked list is eight long. Past it the report used to fall back to grey, so
    every structure after the eighth was drawn the same colour as every other one after
    the eighth - which reads as one series and is worse than an ugly colour.
    """
    series = run_report_page({"rows": batch()})["series"]

    assert len(series) == len(set(series)), "two series share a colour"
    assert all(c.startswith("#") and len(c) == 7 for c in series)
    # Nothing generated is a grey: a grey series is the failure this replaced.
    for colour in series:
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) - min(r, g, b) > 30, f"{colour} has no hue"


# ── against a real report ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def con(report_path):
    connection = duckdb.connect()
    connection.execute(f"CREATE VIEW report AS SELECT * FROM read_parquet('{report_path}')")
    return connection


def test_the_overview_reads_one_row_per_object_and_structure(con):
    rows = con.execute(
        """SELECT object_id, entity_name, entity_kind, instance_count, total_volume_um3
           FROM report WHERE row_type = 'entity' ORDER BY 1, 2"""
    ).fetchall()

    objects = {r[0] for r in rows}
    per_object = {c: sorted(r[1] for r in rows if r[0] == c) for c in objects}
    assert len(objects) == 4
    assert all(names == ["mito", "nucleus", "pm"] for names in per_object.values())
    # Labels carry a count, masks do not - the two kinds of table cell.
    assert all(count is not None for *_, kind, count, _v in rows if kind == "label")


def test_the_per_structure_rows_sit_a_level_below_the_object_rows(con):
    objects = con.execute(
        "SELECT COUNT(*) FROM report WHERE row_type = 'object'").fetchone()[0]
    entities = con.execute(
        "SELECT COUNT(*) FROM report WHERE row_type = 'entity'").fetchone()[0]

    # The counts and extents per structure are on the entity rows, which is why the
    # overview is built from those and not from the object rows the other sections use.
    assert objects == 4
    assert entities == 12


def test_the_overview_of_a_real_report_matches_what_is_in_it(con, report_path):
    from conftest import report_rows

    summary = overview_of(report_rows(report_path))

    assert summary["objects"] == 4
    assert summary["groups"] == 2
    assert sorted(summary["structures"]) == ["mito", "nucleus", "pm"]
    assert summary["instances"] == con.execute(
        "SELECT COUNT(*) FROM report WHERE row_type = 'instance'").fetchone()[0]
    assert summary["missing"] == []
