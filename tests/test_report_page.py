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


def test_the_description_is_put_on_the_page_as_text_and_not_as_markup():
    """A report is a file the reader was sent, so its strings are not markup this page runs.

    The load path itself only runs in a browser, which is why this reads the source: what it
    is guarding is that nobody later reaches for innerHTML to get a link in the credits.
    """
    page = open("src/organella/report/organella_report.html", encoding="utf-8").read()
    body = page.split("function renderDescription")[1].split("\n}")[0]

    assert "report-description" in page, "the page has somewhere to put a description"
    assert "renderDescription(described, REPORT_DESCRIPTION)" in page
    # Nodes, not a string of markup, and only http(s) can become an href - so a description
    # holding `javascript:` or a `<script>` stays the inert words it looks like.
    assert "createTextNode" in body and "createElement('a')" in body
    assert "innerHTML" not in body
    assert "https?" in body


def test_the_columns_a_deep_row_is_asked_for_are_columns_a_run_writes(report_path):
    """The page asks for deep rows by name; a rename in the writer would empty a section.

    95% of a real report is contacts and distances, and the page reads five columns of one
    and nine of the other - so it names them rather than taking `SELECT *` and building a
    seventy-key object per row. Named columns can go stale: DuckDB is handed only the names
    the file has, so a renamed one is quietly left out and the section it fed goes empty.
    """
    deep = run_page({})["constants"]["deepColumns"]
    # Off the cursor's description rather than a frame: the names are all this wants, and
    # .df() would make pandas a dependency of the suite for one line.
    written = {column[0] for column in duckdb.connect().execute(
        f"SELECT * FROM read_parquet('{report_path}') LIMIT 0").description}

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
    """Boxes for distributions, bars for one-per-object values, curves for shares, and
    one circle per object for where its structures sit around it."""
    assert drawn["plots"] > 10
    assert set(drawn["traceTypes"]) == {"bar", "box", "scatter", "scatterpolar"}


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
    assert "1 is a perfect sphere" in joined
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

    Told "this is not an Organella report", a reader goes looking for the wrong problem -
    they have a report, from a version this page predates.
    """
    older = [{"object_id": "object_a", "n_entities": 3}]

    failure = run_page({"loadFailure": older})["loadFailure"]

    assert "older version" in failure
    assert "list columns" in failure


def test_a_parquet_that_is_not_a_report_at_all_says_how_to_make_one(report_path):
    failure = run_page({"loadFailure": [{"something": 1, "else": 2}]})["loadFailure"]

    assert "not an Organella report" in failure
    assert "organella process" in failure


def test_a_report_with_no_rows_says_so(report_path):
    """A run where every object failed used to write one. It no longer can, but the files
    it already wrote are still out there."""
    failure = run_page({"loadFailure": []})["loadFailure"]

    assert "no rows" in failure


# ── what the report opens on ─────────────────────────────────────────────────


def test_a_batch_with_groups_opens_grouped_by_group(report_path):
    """That is the comparison the run was set up to make.

    An object is one sample of a condition, not a thing to compare against another
    condition's, so opening on the objects puts the reader one control away from the
    question the batch was built to answer.
    """
    opened = run_page({"rows": rows_of(report_path), "structure": "mito",
                       "render": True, "initFilters": True})["initFilters"]

    assert opened["groupBy"] == "group"
    assert opened["offered"] is True
    # The select is markup and opens on its first option; a control reading "object" over
    # charts grouped by group is worse than either.
    assert opened["control"] == "group"


def test_a_batch_of_one_group_opens_on_its_objects(report_path):
    """With one group there is nothing to compare, and the control is not offered."""
    rows = [{**row, "imported_path_short": "only"} for row in rows_of(report_path)]

    opened = run_page({"rows": rows, "structure": "mito",
                       "render": True, "initFilters": True})["initFilters"]

    assert opened["groupBy"] == "object"
    assert opened["offered"] is False


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
    assert "organella mesh" in told


def test_geometry_from_two_runs_at_once_says_to_reload_past_the_cache_first():
    """The likeliest cause is not on disk at all.

    `organella view` serves every report on the same port at the same paths, so one
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


# ── what a distance is read against ─────────────────────────────────────────


def test_the_chance_rows_are_read_in_per_object_and_structure(page_of, report_path):
    """One row per object x target in the file, one entry per object x target in the page."""
    con = duckdb.connect()
    written = {}
    for object_id, target in con.execute(
        f"""SELECT object_id, baseline_target FROM read_parquet('{report_path}')
            WHERE row_type = 'baseline'""").fetchall():
        written.setdefault(object_id, []).append(target)

    assert page_of["adapt"]["chance"] == {k: sorted(v) for k, v in written.items()}


def test_one_objects_reference_is_the_one_the_run_measured(page_of, report_path):
    """Pooling is for a facet of several; one object answers with its own row.

    The run measured that median off a 4096-bin histogram of every voxel it had, which is
    finer than anything the page could recover from the 128 bins it ships - so for a single
    object the page has to hand back the stored number rather than re-derive it.
    """
    con = duckdb.connect()
    target = page_of["chance"]["target"]
    stored = dict(con.execute(
        f"""SELECT object_id, baseline_median_um FROM read_parquet('{report_path}')
            WHERE row_type = 'baseline' AND baseline_target = '{target}'""").fetchall())

    for object_id, median in page_of["chance"]["perObject"].items():
        assert median == pytest.approx(stored[object_id], rel=1e-6)


def test_a_facet_of_several_objects_is_read_against_all_of_them_pooled(page_of):
    """A group's reference is its objects' distances together, not one of them.

    Weighted by how much object each brought: pooling the histograms is what does that,
    and it is why the pooled median sits among the per-object ones rather than outside.
    """
    chance = page_of["chance"]
    medians = [v for v in chance["perObject"].values() if v is not None]

    assert chance["objects"] == len(medians) > 1
    # Among them, to the resolution of the pooled bins: the per-object numbers came off
    # the run's own 4096-bin histograms, and pooling can only get within one bin of them.
    grain = chance["pooledWidth"]
    assert min(medians) - grain <= chance["median"] <= max(medians) + grain
    assert chance["voxels"] > 0
    # A density curve over the pooled bins, which integrates to one whatever it is of.
    assert chance["curve"]["density"] == pytest.approx(1.0, rel=1e-6)


def test_the_binned_median_is_interpolated_inside_the_bin_that_holds_it():
    """Read off counts, so the reading has to be right in the middle of a bin as well."""
    got = run_page({"medianOfCounts": [
        {"counts": [0, 10, 10, 0], "min": 0.0, "width": 1.0},
        {"counts": [4, 0, 0, 0], "min": 2.0, "width": 0.5},
        {"counts": [0, 0, 0], "min": 0.0, "width": 1.0},
    ]})["medianOfCounts"]

    assert got[0] == pytest.approx(2.0)      # half of 20 falls at the top of the second bin
    assert got[1] == pytest.approx(2.25)     # all four in one bin: halfway across it
    assert got[2] is None                    # nothing to take a median of


def test_every_distance_panel_carries_its_chance_line(drawn):
    """One dotted line per facet, across the boxes: the whole of "closer than chance".

    Without it a distance panel can only say that two conditions differ, which is not the
    question - a structure can sit closer to the membrane in one condition and be further
    from it than that condition's own shape puts anything.
    """
    referenced = {r["title"]: r for r in drawn["references"]}
    # The distance row's own panels: "<structure> to <target>". The polarity section's
    # panels are angles rather than distances and are read against their own reference.
    distance_panels = [t for t in drawn["titles"] if " to " in t
                       and "every voxel" not in t and "angle to" not in t]

    assert distance_panels, "the report should draw distance panels at all"
    for title in distance_panels:
        if "nearest of the same kind" in title:
            # A distance to another instance of the same structure has no such reference:
            # the region is not made of that structure.
            assert title not in referenced
            continue
        assert title in referenced, f"{title} was drawn without a reference line"
        assert referenced[title]["axis"] == ["y"], "on the axis the distance is on"


def test_the_chance_line_follows_the_distance_onto_the_other_axis(report_path):
    """A histogram puts the distance on x, so the line that reads against it goes vertical."""
    drawn = run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True,
                      "plotStyle": "histogram"})["render"]

    axes = {a for r in drawn["references"] for a in r["axis"]}
    assert axes == {"x"}


def test_the_per_voxel_panels_draw_the_chance_distribution_beside_the_measured_one(drawn):
    """The paper-shaped reading: the measured curve against the same distances everywhere."""
    voxel_panels = [p for p in drawn["seriesNames"] if "every voxel" in (p["title"] or "")]

    assert voxel_panels, "the report should draw per-voxel panels at all"
    for panel in voxel_panels:
        measured = [n for n in panel["names"] if not n.endswith("(chance)")]
        chance = [n for n in panel["names"] if n.endswith("(chance)")]
        assert len(chance) == len(measured) > 0, panel


def test_the_reader_is_told_what_the_dotted_line_is(drawn):
    """A line nobody can name is noise - but a panel's own note is numbers, not prose.

    What the line *means* is said once, in the row's description; under each panel are that
    panel's numbers, and every number in them is labelled.
    """
    said = " ".join(drawn["footnotes"])
    prose = " ".join(drawn["prose"])

    assert "Dotted:" in said and "µm." in said
    assert "lies within" in said, "a panel says the distance its line came from"
    assert "The dotted line on each panel is chance" in prose


# ── the ends of a filament, as against the whole of it ──────────────────────


def test_the_end_distances_widen_onto_the_instance_like_any_other(page_of):
    """One column per target, and they are distances rather than shape metrics."""
    ends = page_of["adapt"]["endDistCols"]

    assert ends == ["distance_to_nucleus_end_um", "distance_to_pm_end_um"]
    # Not among the morphology metrics: drawn there they would be drawn twice, once
    # without the unit and the reference that make them readable.
    assert not [c for c in page_of["adapt"]["metricCols"] if "_end_" in c]


def test_the_ends_get_their_own_row_of_panels(drawn):
    """Their own question, because it is a different one from how close any part gets."""
    end_panels = [t for t in drawn["titles"] if "end to" in t]

    assert sorted(end_panels) == ["mito end to nucleus", "mito end to pm"]
    said = " ".join(drawn["prose"])
    assert "where its skeleton stops" in said
    assert "within one voxel" in said


def test_an_end_panel_says_how_many_reach_the_structure(drawn):
    """The share within one voxel: what "connected to it" comes down to at this voxel size."""
    reaching = [f for f in drawn["footnotes"] if "end within one voxel" in f]

    assert len(reaching) == 2
    for note in reaching:
        assert "%" in note and "µm" in note


def test_the_reference_allows_for_the_extent_of_what_is_read_against_it(page_of, drawn):
    """A point has no surface; an instance touches from one, and the line has to know.

    Measured rather than assumed: the allowance is the gap between an instance's body
    average and its closest point, which is its radius for a sphere and something quite
    different for a filament - so no shape is taken on faith and no threshold decides
    which instances qualify.
    """
    chance = page_of["chance"]

    assert chance["extentGap"] > 0
    assert chance["corrected"] == pytest.approx(chance["median"] - chance["extentGap"])
    # And that is the number the panels draw, not the raw median.
    drawn_at = [at for r in drawn["references"]
                if " to " in r["title"] and "end to" not in r["title"]
                and "angle to" not in r["title"] for at in r["at"]]
    assert drawn_at, "no reference line was drawn to check"
    for at in drawn_at:
        assert at < chance["median"] + chance["extentGap"]


def test_a_tip_is_a_point_so_its_reference_is_left_alone(report_path):
    """The end panels read a tip against the same samples the reference is made of."""
    drawn = run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True, "objectNoun": "cell"})["render"]
    by_title = {r["title"]: r for r in drawn["references"]}

    for target in ("pm", "nucleus"):
        whole = by_title["mito to " + target]["at"]
        tips = by_title["mito end to " + target]["at"]
        # The same structure, the same objects: the tip reference is the uncorrected one,
        # so it sits further out than the one the whole instance is read against.
        assert min(tips) > min(whole)
    assert "needs no allowance for extent" in " ".join(drawn["prose"])


def test_a_sparse_target_is_not_flagged_for_the_reader(drawn):
    """The count, where there is one to state, instead of a threshold on some panels.

    This used to be a title flag on any target with under twenty instances or under a
    twentieth of the population - a rule that decided for the reader what counts as too
    few. The count is said where a target has instances to count; every target in this
    batch is a whole-structure mask, which has none, so nothing is claimed about them.
    """
    assert not [t for t in drawn["titles"] if "target n=" in t]
    assert not [f for f in drawn["footnotes"] if "Nearest of" in f]


def test_without_body_averages_the_reference_says_it_is_uncorrected(report_path):
    """The allowance is measured, so a run that did not measure it must not be corrected.

    `distance_mean_um` is what --distance-histograms adds; dropping it is what the page
    sees from a run without the flag. Silence there would be the worst outcome: the line
    would sit where it sits and read as if the extent had been allowed for.
    """
    rows = [{k: v for k, v in row.items() if k != "distance_mean_um"} for row in
            rows_of(report_path)]
    drawn = run_page({"rows": rows, "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True, "objectNoun": "cell"})["render"]
    said = " ".join(f for f in drawn["footnotes"] if "Dotted" in f)

    assert "No extent allowance was measured" in said
    assert "--distance-histograms" in said
    assert "the extent allowance is" not in said


def test_an_overlapping_structure_is_an_allowance_of_zero_and_not_a_missing_one(report_path):
    """Zero is a measurement: two structures that overlap leave no gap to allow for.

    Read as "not measured" it would tell a reader to re-run with a flag they already used.
    """
    rows = [dict(row) for row in rows_of(report_path)]
    for row in rows:
        if row.get("row_type") == "distance" and row.get("distance_um") is not None:
            row["distance_mean_um"] = row["distance_um"]
    drawn = run_page({"rows": rows, "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True, "objectNoun": "cell"})["render"]
    said = " ".join(f for f in drawn["footnotes"] if "Dotted" in f)

    assert "No extent allowance was measured" not in said
    assert "the extent allowance is" not in said


# ── what a metric colours ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def coloured(report_path):
    return run_page({"rows": rows_of(report_path), "columnHelp": help_of(report_path),
                     "structure": "mito", "objectNoun": "cell",
                     "colour": {"structure": "mito"}})["colour"]


def test_the_scene_can_colour_by_a_distance_the_geometry_file_never_carried(coloured):
    """A distance belongs to a pair, so it is rows of the report, not columns of a surface.

    Which is why it was not on offer as a colouring: the scene reads its metrics out of the
    geometry file. Joined on by structure and label instead, any report can be coloured by
    them - with nothing meshed again.
    """
    assert coloured["reportOnly"] == ["distance_to_nucleus_um", "distance_to_pm_um",
                                      "distance_to_nucleus_end_um", "distance_to_pm_end_um"]
    # The nearest instance of its own kind is already in the geometry file; offering it
    # twice would put it in the list twice.
    assert "distance_to_closest_same_type_um" not in coloured["reportOnly"]
    assert coloured["labels"]["distance_to_nucleus_um"] == "Distance to nucleus (µm)"


def test_the_joined_values_are_read_per_instance(coloured, report_path):
    """One value per instance of the object on screen, keyed by structure and label."""
    con = duckdb.connect()
    first = coloured["joined"]
    expected = con.execute(
        f"""SELECT COUNT(*) FROM read_parquet('{report_path}')
            WHERE row_type = 'distance' AND distance_target = 'nucleus'
              AND object_id = (SELECT MIN(object_id) FROM read_parquet('{report_path}')
                               WHERE row_type = 'object')
              AND distance_um IS NOT NULL""").fetchone()[0]

    assert first["metric"] == "distance_to_nucleus_um"
    assert first["n"] == expected
    for key, value in first["sample"]:
        assert "|" in key and isinstance(value, (int, float))


def test_a_colouring_spans_the_population_and_not_the_cards_on_screen(coloured, report_path):
    """Ten of thirteen thousand granules are all large; scaled to each other they are not.

    The gallery draws the highest or lowest few, so a scale fitted to those few would paint
    the same colours whatever was picked. It comes off the report's own instances instead.
    """
    con = duckdb.connect()
    low, high = con.execute(
        f"""SELECT MIN(instance_volume_um3), MAX(instance_volume_um3)
            FROM read_parquet('{report_path}')
            WHERE row_type = 'instance' AND instance_entity = 'mito'""").fetchone()

    assert coloured["span"]["low"] == pytest.approx(low, rel=1e-6)
    assert coloured["span"]["high"] == pytest.approx(high, rel=1e-6)
    # A whole-structure mask has no instance rows, and one card per object is its whole
    # population, so there the cards themselves are the range.
    assert coloured["shownOnly"] == {"low": 2, "high": 7}


def test_the_scale_is_shown_in_the_colours_it_labels(coloured):
    """A legend interpolated in another space is a legend for a different colouring."""
    ramp = coloured["ramp"]

    assert len(set(ramp)) == len(ramp), "the scale has to change along its length"
    for hex_colour in ramp:
        assert len(hex_colour) == 7 and hex_colour.startswith("#")
        int(hex_colour[1:], 16)
    # Viridis: dark blue-purple at the bottom, yellow at the top.
    assert ramp[0] < ramp[-1]


# ── the landing, when the report is already on its way ──────────────────────


def test_how_to_make_a_report_is_not_shown_while_one_is_loading(report_path):
    """A link with `?data=`, or `organella view`, says a report is already coming.

    Install instructions beside the spinner answer a question nobody asked - and the page
    opens on them for seconds, since a real report is a million rows.
    """
    named = run_page({"rows": rows_of(report_path), "structure": "mito", "render": True,
                      "search": "?data=https://example.com/report.parquet"})["landing"]

    assert named["afterBoot"] is False
    # Back again once nothing is loading: the question is live for whoever is looking at it.
    assert named["afterTryAgain"] is True


def test_how_to_make_a_report_is_shown_when_the_page_is_opened_bare(report_path):
    """No report named anywhere: this is the page's front door, and that is the first need."""
    bare = run_page({"rows": rows_of(report_path), "structure": "mito",
                     "render": True})["landing"]

    assert bare["afterBoot"] is True


def test_no_line_is_drawn_where_the_allowance_swallows_the_distance(report_path):
    """An allowance larger than the distance itself leaves nothing to read against.

    A structure whose own body reaches further than the typical distance to the target
    would be touching wherever it was put, so "closer than chance" has no content - and a
    line pinned at zero reads as a measurement of something. The panel says why instead.
    """
    rows = [dict(row) for row in rows_of(report_path)]
    # Make every instance's body average sit a long way outside its closest point, which is
    # what a large, spread-out structure does.
    for row in rows:
        if row.get("row_type") == "distance" and row.get("distance_um") is not None:
            row["distance_mean_um"] = row["distance_um"] + 50.0
    drawn = run_page({"rows": rows, "columnHelp": help_of(report_path),
                      "structure": "mito", "render": True, "objectNoun": "cell"})["render"]

    notes = " ".join(f for f in drawn["footnotes"] if "No line for" in f)
    assert "is larger than the" in notes
    # And nothing was drawn at zero on those panels.
    for panel in drawn["references"]:
        if "angle to" in panel["title"]:
            continue
        assert all(at > 0 for at in panel["at"]), panel["title"]
