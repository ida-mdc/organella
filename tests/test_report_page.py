"""Run the report page's own code against a real report.

The page is a standalone HTML file, which is what makes it droppable and also what makes it
easy to break silently: nothing imports it, so a rename or a typo only shows up in somebody's
browser console. ``report_page_check.mjs`` lifts its ``<script>`` out and runs it in node, so
the shape a report is read into, the maths the panels draw, the binary container the meshes
are in and the SQL the geometry sections build are all pinned here instead.
"""

import duckdb
import pytest
from conftest import (report_column_help as help_of, report_rows as rows_of,
                      run_report_page as run_page)


@pytest.fixture(scope="module")
def page_of(report_path):
    return run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path), "structure": "mito"})


# ── the report, taken apart ──────────────────────────────────────────────────


def test_every_row_kind_lands_in_the_population_it_belongs_to(page_of, report_path):
    """One typed table becomes four populations, and none of them loses a row."""
    con = duckdb.connect()
    counts = dict(con.execute(
        f"SELECT row_type, COUNT(*) FROM read_parquet('{report_path}') GROUP BY 1").fetchall())
    adapt = page_of["adapt"]
    assert adapt["objects"] == counts["object"]
    assert adapt["ents"] == counts["entity"]
    assert adapt["instances"] == counts["instance"]
    assert adapt["contacts"] == counts["contact"]


def test_the_columns_a_deep_row_is_asked_for_are_columns_a_run_writes(report_path):
    """The page asks for deep rows by name; a rename in the writer would empty a section.

    95% of a real report is contacts and distances, and the page reads five columns of one
    and nine of the other - so it names them rather than taking `SELECT *` and building a
    seventy-key object per row. Named columns can go stale: DuckDB is handed only the names
    the file has, so a renamed one is quietly left out and the section it fed goes empty.
    """
    deep = run_page({})["constants"]["deepColumns"]
    written = set(duckdb.connect().execute(
        f"SELECT * FROM read_parquet('{report_path}') LIMIT 0").df().columns)

    assert set(deep) == {"distance", "contact"}
    for row_type, columns in deep.items():
        missing = [name for name in columns if name not in written]
        assert not missing, f"a {row_type} row is asked for {missing}, which no run writes"


def test_a_deep_row_narrowed_to_those_columns_reads_the_same(report_path):
    """And that they are *all* of them: the read has to survive the narrowing.

    This is the other half of asking by name. The test above says every name is real; this
    one says the names are enough - adapt over deep rows carrying only DEEP_COLUMNS has to
    produce the populations it produces over the whole row, or the page is dropping a
    reading nobody thought to add to the list.
    """
    rows = rows_of(report_path)
    deep = run_page({})["constants"]["deepColumns"]
    narrowed = [
        {key: value for key, value in row.items()
         if key == "row_type" or key in deep[row["row_type"]]}
        if row["row_type"] in deep else row
        for row in rows
    ]

    whole = run_page({"rows": rows, "structure": "mito"})
    narrow = run_page({"rows": narrowed, "structure": "mito"})

    assert narrow["adapt"] == whole["adapt"]
    assert narrow["overview"] == whole["overview"]


def test_a_deep_row_picks_up_the_group_of_the_object_above_it(page_of):
    """A deep row carries only object_id, and every chart colours by group."""
    assert page_of["adapt"]["instancesWithoutGroup"] == 0
    assert page_of["adapt"]["contactsWithoutGroup"] == 0
    assert page_of["adapt"]["groups"] == ["control", "treated"]


def test_a_deep_row_picks_up_the_kind_of_its_structure(page_of):
    """entity_kind lives on the entity row; an instance is of a label entity."""
    assert page_of["adapt"]["instanceKinds"] == ["label"]


def test_every_distance_row_widens_onto_the_instance_it_was_measured_from(page_of, report_path):
    """The long distance rows are the join this page rests on.

    They arrive one per instance x target, and every question here reads two targets of one
    instance at once ("close to the nucleus AND the membrane"). If the join misses, a panel
    is silently empty rather than wrong.
    """
    con = duckdb.connect()
    expected = dict(con.execute(
        f"""SELECT distance_target, COUNT(*) FROM read_parquet('{report_path}')
            WHERE row_type = 'distance' AND distance_um IS NOT NULL GROUP BY 1""").fetchall())
    assert page_of["adapt"]["distanceCoverage"] == expected
    assert sorted(page_of["adapt"]["distTargets"]) == sorted(expected)


def test_a_distance_target_becomes_a_column_of_its_own(page_of):
    targets = page_of["adapt"]["distTargets"]
    assert targets, "the fixture measures distances"
    for target in targets:
        assert f"distance_to_{target}_um" in page_of["adapt"]["cols"]
        assert f"distance_to_{target}_um" in page_of["adapt"]["distCols"]


def test_the_nearest_same_structure_is_a_distance_and_not_a_shape_metric(page_of):
    """It was drawn twice while it counted as both: once as a box, once as a distance."""
    adapt = page_of["adapt"]
    assert "distance_to_closest_same_type_um" in adapt["distCols"]
    assert "distance_to_closest_same_type_um" not in adapt["metricCols"]


def test_no_column_is_offered_as_both_a_shape_metric_and_a_distance(page_of):
    adapt = page_of["adapt"]
    assert not set(adapt["metricCols"]) & set(adapt["distCols"])


def test_the_object_mask_leads_the_structure_pair_matrix(page_of):
    """The matrix is read as a grid, so "how deep inside" has to be the first axis.

    Which reading of that distance it is depends on the run: with histograms on, the mean
    over the instance's body is recorded too and is the better one to trend against.
    """
    dims = page_of["adapt"]["orderedDims"]
    assert dims[0].startswith("distance_to_pm")
    # ...and the same-structure distance last: it answers a different question.
    assert dims[-1] == "distance_to_closest_same_type_um"


def test_a_3d_report_is_read_in_volumes(page_of):
    terms = page_of["adapt"]["extentTerms"]
    assert terms["total"] == "total_volume_um3"
    assert terms["round"] == "sphericity"
    assert page_of["adapt"]["planar"] is False
    assert "volume_um3" in page_of["adapt"]["geometryMetrics"]
    assert "area_um2" not in page_of["adapt"]["geometryMetrics"]


def test_a_2d_report_is_read_in_areas(report_path_2d):
    """The other dimensionality, through the same code: a plane has no volume to offer."""
    page = run_page({"rows": rows_of(report_path_2d), "columnHelp": help_of(report_path_2d), "structure": "mito"})
    terms = page["adapt"]["extentTerms"]
    assert page["adapt"]["planar"] is True
    assert terms["total"] == "total_area_um2"
    assert terms["boundary"] == "perimeter_um"
    assert terms["round"] == "circularity"
    metrics = page["adapt"]["geometryMetrics"]
    assert "area_um2" in metrics and "circularity" in metrics
    assert "volume_um3" not in metrics and "sphericity" not in metrics


def test_a_report_holding_both_dimensionalities_is_called_out(report_path, report_path_2d):
    """Extents are not comparable across them, and the reader has to be told."""
    rows = rows_of(report_path) + rows_of(report_path_2d)
    page = run_page({"rows": rows, "structure": "mito"})
    assert page["adapt"]["mixedDims"] is True


# ── the maths the panels draw ────────────────────────────────────────────────


def test_a_share_curve_reads_the_same_at_any_n(page_of):
    """The pair matrix uses this because it has no minimum sample size."""
    curves = page_of["curves"]
    assert curves["monotone"], "a cumulative share cannot go down"
    assert curves["last"] == 100
    assert curves["first"] == [0, 0]
    # Quantiles, not one vertex per instance: 8k instances would otherwise be 8k nodes.
    assert curves["vertices"] <= curves["n"] + 1


def test_per_voxel_histograms_are_rebinned_onto_one_shared_grid(page_of):
    """Each instance ships bins over its own range; a population needs one grid.

    Never finer than the sources, either: asking for 30 bins from 20-bin sources leaves
    output bins no source bin can reach, and the curve combs into spikes that read as real.
    """
    hist = page_of["histogram"]
    assert hist["bins"] > 1
    assert hist["total"] > 0
    # The grid is capped at what the source resolution can carry, asked for 30 bins or not.
    supported = round(hist["span"] / hist["srcWidth"])
    assert hist["bins"] == max(4, min(30, supported))
    # A density integrates to one over the grid it was built on.
    assert hist["densitySums"] == pytest.approx(1.0, abs=1e-6)


def test_facet_labels_keep_only_what_differs_and_stay_distinct(page_of):
    """Object ids are long shared paths; as axis ticks they cost more room than the plots.

    Two names truncated to the same label would become ONE Plotly category, silently
    merging their bars, so the budget widens until they are distinct again.
    """
    short = run_page({"facetLabels": ["run_27-10-25_1_segmentations",
                                      "run_27-10-25_2_segmentations"]})["facetLabels"]
    assert short == ["1", "2"]
    collide = run_page({"facetLabels": ["object_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_x",
                                        "object_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_y"]})["facetLabels"]
    assert len(set(collide)) == 2


# ── the clustering the contacts section counts by ───────────────────────────


def test_an_instance_touching_nothing_is_still_counted(report_path):
    """"In contact" is a share of every instance, which is what makes it a share."""
    page = run_page({
        "rows": rows_of(report_path), "structure": "mito",
        "clusters": [{"object": "object_a", "entity": "mito", "gap": 0.0}],
    })
    con = duckdb.connect()
    total = con.execute(
        f"""SELECT COUNT(*) FROM read_parquet('{report_path}')
            WHERE row_type = 'instance' AND instance_entity = 'mito'
              AND object_id = 'object_a'""").fetchone()[0]
    found = page["clusters"][0]
    assert found["instances"] == total
    assert found["sizes"] == [], "nothing touches at a gap of zero"


def test_a_chain_of_contacts_becomes_one_group(report_path):
    """Union-find, not pair counting: A-B and B-C is one group of three."""
    page = run_page({
        "rows": rows_of(report_path), "structure": "mito",
        "clusters": [{"object": "object_a", "entity": "mito", "gap": 10.0}],
    })
    found = page["clusters"][0]
    # Every mito in the object is within 10 µm of the chain, so they are all one group.
    assert found["sizes"] == [found["instances"]]
    assert found["grouped"] == found["instances"]


def test_a_wider_gap_never_makes_fewer_instances_touch(report_path):
    """Monotone in the slider: opening it can only add edges."""
    rows = rows_of(report_path)
    grouped = []
    for gap in (0.0, 0.05, 0.1, 0.5, 10.0):
        page = run_page({"rows": rows, "structure": "mito",
                         "clusters": [{"object": "object_a", "entity": "mito", "gap": gap}]})
        grouped.append(page["clusters"][0]["grouped"])
    assert grouped == sorted(grouped)


def test_the_gap_slider_is_sized_to_the_gaps_that_were_recorded(page_of, report_path):
    """--contact-max-um can be well below the 0.5 µm the slider used to assume."""
    con = duckdb.connect()
    widest = con.execute(
        f"SELECT MAX(contact_gap_um) FROM read_parquet('{report_path}')").fetchone()[0]
    assert page_of["adapt"]["contactMaxGap"] == pytest.approx(widest, rel=1e-6)


def test_the_contacts_section_offers_only_structures_that_have_contacts(page_of, report_path):
    con = duckdb.connect()
    expected = sorted(r[0] for r in con.execute(
        f"""SELECT DISTINCT contact_entity FROM read_parquet('{report_path}')
            WHERE row_type = 'contact'""").fetchall())
    assert page_of["adapt"]["contactStructures"] == expected


# ── every section, actually drawn ────────────────────────────────────────────


@pytest.fixture(scope="module")
def drawn(report_path):
    """The whole page rendered against a stub DOM, with Plotly recording what it was asked
    to draw.

    Nothing imports this page and JavaScript has no compiler, so a typo in the drawing code
    used to reach a browser and nowhere else - and one in the overview emptied the entire
    report while reporting it as a failure to load the parquet.
    """
    return run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path), "structure": "mito", "render": True})["render"]


def test_every_section_draws(drawn):
    assert drawn["failed"] == [], "a section threw while being drawn"


def test_the_sections_draw_the_charts_they_promise(drawn):
    """Boxes for distributions, bars for one-per-object values, curves for shares."""
    assert drawn["plots"] > 10
    assert set(drawn["traceTypes"]) == {"bar", "box", "scatter"}


def test_the_question_headings_name_the_structure_they_are_about(drawn):
    """A chart titled about nothing is how an unset selector used to read."""
    for title in drawn["titles"]:
        assert title.strip(), "a chart was given an empty title"
        assert " ," not in title and "  " not in title, title


def test_the_shape_metrics_of_the_structure_each_get_a_chart(drawn):
    titles = " | ".join(drawn["titles"])
    for metric in ("volume", "sphericity", "branches", "tortuosity"):
        assert metric in titles, metric


def test_the_proximity_of_a_structure_to_each_target_gets_a_chart(drawn):
    titles = " | ".join(drawn["titles"])
    # One per target, plus the per-voxel view of the same pair.
    assert "mito body to pm, every voxel" in titles
    assert "nearest of the same kind" in titles


def test_every_chart_names_the_structure_it_is_about(drawn):
    """"volume" is only readable if you already know which structure is selected.

    It matters most when a panel is screenshotted into a slide, away from the selector.
    """
    about_mito = [t for t in drawn["titles"] if t.startswith("mito")]
    assert len(about_mito) >= 8, drawn["titles"]


def test_every_metric_chart_carries_the_explanation_from_the_report(drawn):
    """The report says what each column means; the page shows it under the chart of it.

    Read out of the footer rather than kept here, so there is one wording and it is the
    one in the file.
    """
    assert drawn["helps"], "no panel explained what it was showing"
    joined = " ".join(drawn["helps"])
    # The sphericity answer in particular: a value over 1 is the question everyone asks.
    assert "1 is a perfect ball" in joined
    assert "equal-volume sphere" in joined


def test_a_section_that_cannot_be_drawn_says_so_and_the_others_survive(report_path):
    """One section failing must not take the report with it.

    Reported as a failure to *load*, which is what it used to be, the reader was shown an
    empty page and told the parquet was broken.
    """
    rows = rows_of(report_path)
    # A contact naming a structure that has no instance rows: the contacts section has
    # nothing to seed its clustering with.
    broken = [dict(r) for r in rows]
    for row in broken:
        if row.get("row_type") == "contact":
            row["contact_entity"] = None

    drawn = run_page({"rows": broken, "structure": "mito", "render": True})["render"]

    assert drawn["plots"] > 0, "the other sections still drew"


# ── how a population is drawn, and what the reader can change about it ───────


@pytest.fixture(scope="module")
def by_group(report_path):
    """The same report, faceted by experimental group, with the brackets on."""
    return run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path), "structure": "mito", "render": True,
                     "groupBy": "group", "significance": True})["render"]


@pytest.fixture(scope="module")
def as_histograms(report_path):
    """The same again, drawn as histograms instead of boxes."""
    return run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path), "structure": "mito", "render": True,
                     "groupBy": "group", "significance": True,
                     "plotStyle": "histogram"})["render"]


def test_brackets_are_drawn_between_groups(by_group):
    assert by_group["bracketedPanels"] > 0
    for bracket in by_group["brackets"]:
        assert bracket["stars"] in ("*", "**", "***", "****", "ns")
        assert "Mann-Whitney U" in bracket["detail"]
        assert "control vs treated" in bracket["detail"]


def test_a_bracket_carries_the_p_value_and_the_n_it_used(by_group):
    detail = by_group["brackets"][0]["detail"]
    assert "p = " in detail or "p < " in detail
    assert "n = " in detail


def test_the_comparison_is_offered_whatever_the_facets_are(drawn):
    """On or off with the checkbox and nothing else.

    Faceted by object the facets are two samples of one condition rather than two
    conditions, which is worth knowing and is not a result - so the comparison is still
    shown when asked for, and it names the facets it compared so it reads as what it is.
    """
    assert drawn["bracketedPanels"] > 0
    named = {tuple(sorted(b["detail"].split(":")[0].split(" vs "))) for b in drawn["brackets"]}
    assert named, "a bracket says which two facets it compared"
    for pair in named:
        assert all(name.startswith("object_") for name in pair), pair


def test_turning_significance_off_removes_them(report_path):
    off = run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path), "structure": "mito", "render": True,
                    "groupBy": "group", "significance": False})["render"]
    assert off["brackets"] == []
    assert off["notes"] == []


def test_histograms_carry_the_same_comparison_as_text(as_histograms):
    """A histogram's facets share one x axis, so there is no position to bracket between."""
    assert as_histograms["brackets"] == []
    assert as_histograms["notes"], "the comparison has to survive the switch"
    assert any("control vs treated" in note for note in as_histograms["notes"])


def test_both_chart_styles_draw_the_same_panels(drawn, as_histograms):
    """The switch changes how a population is drawn, not which questions are asked."""
    assert as_histograms["failed"] == []
    assert as_histograms["titles"] == drawn["titles"]


def test_a_histogram_is_a_binned_shape_and_a_distribution_is_a_box(drawn, as_histograms):
    boxes = drawn["traceTypes"].count("box")
    assert "box" in drawn["traceTypes"]
    assert "scatter" in as_histograms["traceTypes"]
    # A panel whose every value is identical has no shape to bin, and falls back to the box
    # rather than drawing nothing - so a box surviving the switch is expected, but not many.
    assert as_histograms["boxPanels"] < drawn["boxPanels"]


def test_a_histogram_bins_every_facet_on_one_grid():
    """Per-facet grids put the same value in differently placed bins, and then the shapes
    are not comparable - which is the whole reason to draw them together."""
    binned = run_page({"histogram": [
        {"series": [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [6, 7, 8, 9, 10, 11, 12]]},
    ]})["histogram"][0]

    assert binned["min"] == 1 and binned["max"] == 12
    assert len(binned["centers"]) == binned["bins"]
    assert len(binned["edges"]) == binned["bins"] + 1
    # Every facet is a share of its own population, so a condition with twice the instances
    # does not simply draw twice as tall.
    for facet in binned["facets"]:
        assert sum(facet["percent"]) == pytest.approx(100.0)


def test_a_histogram_is_drawn_on_bin_edges_and_closes_at_zero():
    """Plotted against bin centres, every bar sat half a bin to the left of its data."""
    binned = run_page({"histogram": [{"series": [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]]}]})["histogram"][0]
    facet = binned["facets"][0]

    # The outline runs edge to edge, with a zero at each end so it reads as a shape.
    assert facet["x"][0] == binned["edges"][0]
    assert facet["x"][-1] == binned["edges"][-1]
    assert facet["y"][0] == 0 and facet["y"][-1] == 0
    assert len(facet["x"]) == len(facet["y"])
    # An hv step from (edge_i, height_i) holds the height across the bin it belongs to.
    assert facet["x"][1:-1] == binned["edges"]


def test_a_histogram_of_one_value_has_no_shape_to_bin():
    assert run_page({"histogram": [{"series": [[3, 3, 3], [3, 3]]}]})["histogram"][0] is None


# ── one panel per structure pair, side by side ──────────────────────────────


def test_every_pair_of_structures_gets_a_panel_of_its_own(drawn):
    """It was a lower-triangle subplot matrix, which is compact and unreadable: which pair a
    panel was about had to be worked out from where it sat, and each was 148px wide with no
    axis of its own."""
    titles = drawn["titles"]
    pairs = [t for t in titles if " + " in t]
    assert pairs == ["pm + nucleus", "pm + nearest of the same kind",
                     "nucleus + nearest of the same kind"]


def test_the_panels_are_every_combination_and_no_repeats():
    counts = run_page({"dimPairs": [["a"], ["a", "b"], ["a", "b", "c"],
                                    ["a", "b", "c", "d"]]})["dimPairs"]
    assert [len(c) for c in counts] == [0, 1, 3, 6]
    for pairs in counts:
        assert len({tuple(sorted(p)) for p in pairs}) == len(pairs)


# ── the structure's name, marked as coming from the data ────────────────────


def test_a_structure_name_is_marked_as_a_name(report_path):
    """"How far is each mito from other structures?" reads like a typo until you know that
    mito is a folder's own word for a structure and not this page's."""
    marked = run_page({"dataNames": ["mito", "pm", "a & b"]})["dataNames"]
    assert marked[0] == '<span class="data-name">mito</span>'
    # ...and it is still escaped: a structure name comes out of a file name.
    assert marked[2] == '<span class="data-name">a &amp; b</span>'


def test_a_measurement_this_run_skipped_says_so_rather_than_going_quiet(report_path):
    """Silence reads as "the report cannot answer this", which is the wrong conclusion.

    The per-voxel distance distributions are what say whether a structure *hugs* another
    along its length or only touches it at one point - the closest-point charts see only
    the single nearest point. They are opt-in, so a run without them has to say that.
    """
    without = []
    for row in rows_of(report_path):
        kept = {k: v for k, v in row.items() if "hist" not in k}
        without.append(kept)

    drawn = run_page({"rows": without, "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True})["render"]

    prose = " ".join(drawn["prose"])
    assert "Not measured in this run" in prose
    assert "--distance-histograms" in prose
    # ...and the question is still asked, so the reader knows it is a question worth asking.
    assert "lies near a structure" in prose


def test_the_measurement_is_explained_where_it_is_present(drawn):
    prose = " ".join(drawn["prose"])
    assert "Not measured in this run" not in prose
    assert "lies near a structure" in prose


# ── a report this page cannot read ───────────────────────────────────────────


def test_a_report_from_before_the_typed_rows_is_named_as_that(report_path):
    """Its instances and contacts were list columns on the object row.

    Told "this is not an Anatomy report", a reader goes looking for the wrong problem -
    they have a report, from a version this page predates.
    """
    older = [{"object_id": "object_a", "n_entities": 3}]

    failure = run_page({"loadFailure": older})["loadFailure"]

    assert "older version" in failure
    assert "list columns" in failure


def test_a_parquet_that_is_not_a_report_at_all_says_how_to_make_one(report_path):
    failure = run_page({"loadFailure": [{"something": 1, "else": 2}]})["loadFailure"]

    assert "not an Anatomy report" in failure
    assert "label-anatomy process" in failure


def test_a_report_with_no_rows_says_so(report_path):
    """A run where every object failed used to write one. It no longer can, but the files
    it already wrote are still out there."""
    failure = run_page({"loadFailure": []})["loadFailure"]

    assert "no rows" in failure


# ── geometry this page cannot read ───────────────────────────────────────────


MIXED_GLOB = (
    'Invalid Input Error: Failed to read file "geom_url_a.parquet": schema mismatch in '
    'glob: column "surface" was read from the original file "geom_url_b.parquet", but '
    'could not be found in file "geom_url_a.parquet"'
)
ALL_OLD = (
    'Binder Error: Referenced column "surface" not found in FROM clause!\n'
    'Candidate bindings: "surface_area_um2", "mesh_faces"'
)


def test_geometry_from_before_the_surface_kinds_says_to_mesh_it_again():
    """DuckDB says a column is missing; only this page knows which run wrote it."""
    told = run_page({"geometryFailure": [ALL_OLD]})["geometryFailure"][0]

    assert "before the surface kinds" in told
    assert "label-anatomy mesh" in told


def test_geometry_from_two_runs_at_once_says_to_reload_past_the_cache_first():
    """The likeliest cause is not on disk at all.

    `label-anatomy view` serves every report on the same port at the same paths, so one
    object's geometry.parquet has the same URL whichever run wrote it, and a browser that
    kept the old body hands DuckDB a glob of two schemas. Told only "schema mismatch in
    glob", a reader goes looking through their folders for a file that is not there.
    """
    told = run_page({"geometryFailure": [MIXED_GLOB]})["geometryFailure"][0]

    assert "not all written by the same run" in told
    assert "Ctrl-Shift-R" in told
    assert "mesh the batch again" in told


def test_a_geometry_error_nobody_wrote_a_message_for_is_passed_through():
    told = run_page({"geometryFailure": ["IO Error: No files found that match the pattern"]})
    assert told["geometryFailure"][0] == "IO Error: No files found that match the pattern"


# ── how much of the region is which structure ────────────────────────────────


def test_the_overview_draws_one_stacked_composition_bar(drawn):
    """Absolute totals on a scale each cannot be compared; shares of one region can.

    Eight panels with eight axes answered "how much of each structure is there" for a
    single object with eight single bars, where a nucleus and a lipid droplet drew the same
    height. One stack on one percentage scale is the same numbers, comparable.
    """
    stacks = drawn["stacks"]

    assert len(stacks) == 1, "the overview should draw exactly one composition bar"
    assert "%" in stacks[0]["xTitle"] or "of" in stacks[0]["xTitle"]


def test_every_composition_stack_fills_its_axis_exactly_once(drawn):
    """The segments and the unsegmented remainder are a partition, so they sum to 100%."""
    series = drawn["stacks"][0]["series"]
    n_objects = len(series[0]["x"])

    for i in range(n_objects):
        total = sum(s["x"][i] for s in series if s["x"] is not None)
        assert total == pytest.approx(100.0, abs=0.5), f"object {i} sums to {total}%"


def test_the_remainder_is_named_rather_than_left_as_a_gap(drawn):
    """A stack that stops short of the axis has to say what the rest of the region is."""
    names = [s["name"] for s in drawn["stacks"][0]["series"]]

    assert names[-1] == "not in any structure"
    assert len(names) > 1


def test_a_structure_missing_from_an_object_is_a_zero_and_not_a_hole(drawn):
    """Every series spans every object, so the stacks stay aligned when one lacks a mask."""
    series = drawn["stacks"][0]["series"]
    widths = {len(s["x"]) for s in series if s["x"] is not None}

    assert len(widths) == 1
