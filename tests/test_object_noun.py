"""What one measured thing is called, and when there is no such thing to call.

The report's own word for its subject is not a preference: it depends on whether anything
bounds it. With a bounding mask there is a body with a centroid and an extent - a cell, a
specimen. Without one there is no body, only a source image with labels lying in it, and
calling that an "object" promises something that is not there.

So a run can name it (``--object-noun cell``) and otherwise it follows the data. None of it
touches a column: two reports of the same kind stay joinable whatever they call themselves.
"""

import pytest
from conftest import report_column_help, report_rows, run_report_page
from test_contacts_view import batch, entity_row, instance_row, object_row

from organella import report_io
from organella.column_schema import describe, plural_of, with_noun


def unbounded(rows):
    """The same rows as a run with no --object-mask writes them: no mask, no boundary."""
    out = []
    for row in rows:
        kept = {k: v for k, v in row.items()
                if k not in ("object_mask_name", "object_volume_um3", "object_area_um2")
                and not k.startswith("polar_") and not k.startswith("instance_polar_")}
        out.append(kept)
    return out


def noun_of(rows, given=None):
    return run_report_page({"rows": rows, "structure": "mito", "objectNoun": given})["noun"]


# ── the word ─────────────────────────────────────────────────────────────────

def test_a_bounded_thing_is_an_object_unless_the_run_says_otherwise():
    assert noun_of(batch([("object_a", "mito", 1)], []))["one"] == "object"


def test_a_run_with_no_bounding_mask_measures_datasets_not_objects():
    """There is no bounded body to name: only a source image with labels lying in it."""
    assert noun_of(unbounded(batch([("object_a", "mito", 1)], [])))["one"] == "dataset"


def test_the_run_can_name_it():
    named = noun_of(batch([("object_a", "mito", 1)], []), given="cell")

    assert (named["one"], named["many"]) == ("cell", "cells")


def test_the_run_can_name_it_even_where_nothing_bounds_it():
    """A field of view is still a thing somebody has a word for."""
    named = noun_of(unbounded(batch([("object_a", "mito", 1)], [])), given="field")

    assert named["one"] == "field"


def test_an_irregular_plural_is_given_rather_than_guessed():
    named = noun_of(batch([("object_a", "mito", 1)], []), given="nucleus/nuclei")

    assert (named["one"], named["many"]) == ("nucleus", "nuclei")
    assert plural_of("nucleus/nuclei") == "nuclei"
    assert plural_of("cell") == "cells"


def test_the_article_follows_the_word():
    """"a object" is what a hard-coded article gives you the moment the word changes."""
    assert noun_of(batch([("object_a", "mito", 1)], []))["a"] == "an object"
    assert noun_of(batch([("object_a", "mito", 1)], []), given="cell")["a"] == "a cell"
    assert noun_of(batch([("object_a", "mito", 1)], []), given="organoid")["a"] == "an organoid"


# ── what it changes, and what it must not ────────────────────────────────────

def test_naming_it_changes_no_column(report_path, tmp_path):
    """Two reports of the same kind have to stay joinable whatever they call themselves."""
    import pyarrow.parquet as pq

    plain = [f.name for f in pq.read_schema(report_path)]

    # Re-writing the same rows under a noun must not move a column.
    rows, footer = report_io.read(report_path)
    named = tmp_path / "named.parquet"
    report_io._write(rows, named, footer, noun="cell")

    assert [f.name for f in pq.read_schema(named)] == plain


def test_the_descriptions_say_the_word_the_run_chose():
    """Substituted where the file is written, so the page, pyarrow and a notebook agree."""
    assert with_noun(describe("object_volume_um3"), "cell") == (
        "Volume in µm³ enclosed by the cell mask.")
    # Whole words only: a column name inside a sentence is left alone.
    assert "object_id" in with_noun("Name of the object_id column", "cell")


def test_nothing_named_leaves_the_descriptions_alone():
    assert with_noun(describe("object_volume_um3"), None) == describe("object_volume_um3")


def test_the_word_travels_in_the_report(report_path, tmp_path):
    import shutil

    copy = tmp_path / "named.parquet"
    shutil.copy(report_path, copy)
    rows, footer = report_io.read(copy)
    footer[report_io.NOUN_KEY] = "cell"
    report_io._write(rows, copy, footer, noun="cell")

    _, read_back = report_io.read(copy)
    assert report_io.noun_of(read_back) == "cell"


# ── the sections that depend on there being a boundary ───────────────────────

def test_with_no_bounding_mask_the_report_says_so_instead_of_inventing_one(report_path):
    """It used to read "measured relative to `no bounding mask`, the mask that bounds each
    object" - a placeholder in a code chip, and three untruths in one sentence."""
    drawn = run_report_page({
        "rows": unbounded(report_rows(report_path)),
        "columnHelp": report_column_help(report_path),
        "structure": "mito", "render": True,
    })["render"]

    assert drawn["failed"] == []


def test_with_a_bounding_mask_the_report_names_it(report_path):
    drawn = run_report_page({
        "rows": report_rows(report_path), "columnHelp": report_column_help(report_path),
        "structure": "mito", "render": True,
    })["render"]

    assert drawn["failed"] == []


def test_a_name_already_plural_does_not_take_another_s():
    """A structure called `granules` gave "% of granuless" on every axis it labelled."""
    assert plural_of("granules") == "granules"
    assert plural_of("cell") == "cells"
    assert plural_of("nucleus/nuclei") == "nuclei"

    named = noun_of(batch([("object_a", "mito", 1)], []), given="specimens")
    assert named["many"] == "specimens"


def test_the_page_pluralises_a_structure_name_the_same_way():
    """Not a general pluraliser, and not trying to be: it declines to double a trailing s.

    That is the case that turns up - a structure called `granules` is already plural. It
    also leaves `nucleus` alone, which is wrong as English and better than "nucleuss";
    a run that cares says `--object-noun nucleus/nuclei`, and a structure name comes from
    the segmentation rather than from here.
    """
    got = run_report_page({"pluralise": ["granules", "mito", "er", "nucleus"]})["pluralise"]

    assert got == ["granules", "mitos", "ers", "nucleus"]
