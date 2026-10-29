import json

import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pytest

from organella.app.process import process
from organella.app.settings import Settings
from organella.measure import DEFAULT
from organella.sinks import ParquetSink
from organella.sources import FolderSource


@pytest.fixture(scope="module")
def written(object_folder, tmp_path_factory):
    path = tmp_path_factory.mktemp("rep") / "my_report.parquet"
    sink = ParquetSink(path, noun="cell", description="Made for the test. CC-BY.", colours={"mito": "#d62728"}, paths=["control"],
                       root=object_folder.parent)
    assert process(FolderSource(object_folder.parent, object_mask="cell"), DEFAULT, sink, Settings(reach_um=0.4)) == {}
    return path


def footer(path):
    return {k.decode(): v.decode() for k, v in pq.read_metadata(path).metadata.items() if not k.startswith(b"ARROW:")}


def test_the_footer_says_what_the_report_is_and_how_it_was_made(written):
    meta = footer(written)
    assert meta["organella_project_name"] == "my_report" and meta["organella_description"] == "Made for the test. CC-BY."
    assert meta["organella_object_noun"] == "cell" and json.loads(meta["organella_paths"]) == ["control"]
    stats = json.loads(meta["organella_processing_stats"])
    assert stats["n_objects"] == 1 and stats["n_failed"] == 0 and stats["wall_s"] > 0 and "cell_a" in stats["seconds_per_object"]
    assert {"organella_version", "organella_created_at", "organella_privacy_summary", "organella_base_dir"} <= set(meta)


def test_every_column_that_has_a_meaning_carries_it_in_the_footer_and_on_the_field_and_says_cell(written):
    described = json.loads(footer(written)["organella_column_descriptions"])
    assert described["object_volume_um3"] == "Volume in µm³ enclosed by the cell mask."
    assert "distance_beyond_reach" in described and "pixel_size_z" in described
    schema = pq.read_schema(written)
    assert schema.field("instance_volume_um3").metadata[b"description"].decode() == described["instance_volume_um3"]


def test_structures_named_in_the_colour_file_are_coloured_and_the_others_are_left_to_the_page(written):
    entities = pl.read_parquet(written).filter(pl.col("row_type") == "entity")
    assert dict(zip(entities["entity_name"], entities["entity_colour"])) == {"cell": None, "mito": "#d62728", "wall": None}


def test_a_colour_file_that_is_not_colours_is_refused_with_the_name_that_is_wrong(tmp_path):
    import click
    from organella.sinks.colours import colours_from_file

    bad = tmp_path / "c.json"
    bad.write_text('{"mito": "red"}')
    with pytest.raises(click.ClickException, match="'mito'.*not a colour"):
        colours_from_file(bad)
    bad.write_text('{"mito": "#d62728", "er": "#2ca"}')
    assert colours_from_file(bad) == {"mito": "#d62728", "er": "#2ca"}


def test_a_description_can_be_added_afterwards_and_the_rest_of_the_report_is_untouched(written, tmp_path):
    from organella.report.file import describe, read_report
    copy = tmp_path / "copy.parquet"
    copy.write_bytes(written.read_bytes())
    rows_before, footer_before = read_report(copy)
    describe(copy, "Credits added later.")
    rows_after, footer_after = read_report(copy)
    assert footer_after["organella_description"] == "Credits added later."
    assert {k: v for k, v in footer_after.items() if k != "organella_description"} == {k: v for k, v in footer_before.items() if k != "organella_description"}
    assert rows_before.equals(rows_after)


def test_a_palette_can_be_tried_on_a_report_that_exists_and_names_it_does_not_have_are_ignored(written, tmp_path):
    from organella.report.file import read_report, recolour
    copy = tmp_path / "copy2.parquet"
    copy.write_bytes(written.read_bytes())
    assert recolour(copy, {"wall": "#112233", "mito": "#445566", "not-here": "#000000"}) == 2
    rows, footer = read_report(copy)
    entities = rows.filter(pl.col("row_type") == "entity")
    assert dict(zip(entities["entity_name"], entities["entity_colour"])) == {"cell": None, "mito": "#445566", "wall": "#112233"}
    assert json.loads(footer["organella_column_descriptions"])["entity_colour"]          # still self-describing


def _report_of(parent, out):
    from organella.app.process import process
    assert process(FolderSource(parent, object_mask="cell"), DEFAULT, ParquetSink(out), Settings(reach_um=0.4)) == {}
    return out


def test_reports_of_separate_runs_merge_into_the_report_one_run_over_all_of_them_would_write(object_folder, tmp_path):
    import shutil
    from organella.report.file import merge_reports, read_report
    first, second, together = tmp_path / "one", tmp_path / "two", tmp_path / "both"
    for parent, name in ((first, "cell_a"), (second, "cell_b"), (together, "cell_a"), (together, "cell_b")):
        shutil.copytree(object_folder, parent / name)
    parts = [_report_of(first, tmp_path / "a.parquet"), _report_of(second, tmp_path / "b.parquet")]
    whole, _ = read_report(_report_of(together, tmp_path / "whole.parquet"))
    merged_path = tmp_path / "merged.parquet"
    assert merge_reports(parts, merged_path) == 2
    merged, footer = read_report(merged_path)
    assert merged.equals(whole)
    stats = json.loads(footer["organella_processing_stats"])
    assert footer["organella_project_name"] == "merged" and stats["n_objects"] == 2 and set(stats["seconds_per_object"]) == {"cell_a", "cell_b"}
    assert json.loads(footer["organella_column_descriptions"])["object_volume_um3"]


def test_an_object_in_two_reports_is_refused_and_so_is_nothing_to_merge(written, tmp_path):
    from organella.report.file import merge_reports
    with pytest.raises(ValueError, match="cell_a is in both"):
        merge_reports([written, written], tmp_path / "x.parquet")
    with pytest.raises(ValueError, match="no reports"):
        merge_reports([], tmp_path / "y.parquet")
