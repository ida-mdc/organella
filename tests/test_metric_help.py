import pyarrow.parquet as pq
from conftest import report_column_help, run_report_page

from organella import report_io
from organella.column_schema import describe


def test_every_column_of_a_real_report_is_described(report_path):
    columns = [field.name for field in pq.read_schema(report_path)]

    undescribed = [column for column in columns if not describe(column)]

    assert not undescribed, (
        "these columns reach a report with nothing saying what they are; add them to the "
        f"measurer that writes them, or to column_schema: {undescribed}")


def test_the_2d_columns_are_described_too(report_path_2d):
    columns = [field.name for field in pq.read_schema(report_path_2d)]

    assert not [column for column in columns if not describe(column)]


def test_the_descriptions_travel_in_the_file(report_path):
    """Both copies, because they answer to different readers."""
    in_footer = report_column_help(report_path)
    on_fields = {field.name: (field.metadata or {}).get(b"description", b"").decode()
                 for field in pq.read_schema(report_path)}

    assert in_footer, "the footer carries the map the page reads"
    for column, description in in_footer.items():
        assert on_fields[column] == description, column


def test_recolouring_a_report_keeps_its_descriptions(report_path, tmp_path):
    """`colours` rewrites the file, and a rewrite that dropped them would be silent."""
    import shutil

    copy = tmp_path / "recoloured.parquet"
    shutil.copy(report_path, copy)
    before = report_column_help(copy)

    report_io.recolour(copy, {"mito": "#123456"})

    assert report_column_help(copy) == before


def test_the_footer_survives_a_recolour(report_path, tmp_path):
    import shutil

    copy = tmp_path / "recoloured.parquet"
    shutil.copy(report_path, copy)
    _, before = report_io.read(copy)

    report_io.recolour(copy, {"mito": "#123456"})
    _, after = report_io.read(copy)

    # The provenance is the only record of how the run was made; a rewrite keeps it.
    assert after["organella_created_at"] == before["organella_created_at"]
    assert after["organella_processing_stats"] == before["organella_processing_stats"]


# ── what the run says the data is ────────────────────────────────────────────

def test_a_report_nobody_described_says_nothing(report_path):
    """None rather than an empty string, so a caller can ask `if description_of(...)`."""
    _, footer = report_io.read(report_path)

    assert report_io.description_of(footer) is None


def test_a_description_travels_in_the_report(report_path, tmp_path):
    """Credits belong to the file: the folder it was measured in does not travel with it."""
    import shutil

    copy = tmp_path / "described.parquet"
    shutil.copy(report_path, copy)
    credits = "Alpha cells, Schmidt et al. 2026 (doi:10.0000/xyz).\nSegmentation CC BY 4.0."

    report_io.describe(copy, credits)
    _, footer = report_io.read(copy)

    # Line breaks and all: a citation is not a single line, and the page shows it pre-wrapped.
    assert report_io.description_of(footer) == credits


def test_describing_a_report_keeps_everything_else(report_path, tmp_path):
    """The same rewrite `colours` does, and the same thing it must not lose."""
    import shutil

    copy = tmp_path / "described.parquet"
    shutil.copy(report_path, copy)
    rows_before, before = report_io.read(copy)
    help_before = report_column_help(copy)

    report_io.describe(copy, "Some credits.")
    rows_after, after = report_io.read(copy)

    assert rows_after.height == rows_before.height
    assert report_column_help(copy) == help_before
    assert report_io.noun_of(after) == report_io.noun_of(before)
    for key in ("organella_created_at", "organella_processing_stats",
                "organella_paths"):
        assert after[key] == before[key], key


# ── what the page makes of them ──────────────────────────────────────────────

def test_the_page_explains_a_metric_in_the_reports_own_words(report_path):
    help_map = report_column_help(report_path)
    asked = ["sphericity", "volume_um3", "branches", "tortuosity", "polar_dist_um"]

    got = run_report_page({
        "columnHelp": help_map,
        "helpFor": [[column, "instance_"] for column in asked],
    })["helpFor"]

    for column, text in zip(asked, got):
        assert text, f"the page has nothing to say about {column}"
    # And it is the file's wording, not a second one written beside it.
    assert help_map["instance_sphericity"][:40] in got[0]


def test_a_whole_structure_and_one_instance_of_it_are_described_apart(report_path):
    """`volume_um3` is a mask's volume on an entity row and an instance's on an instance
    row, and the report describes both. A panel about instances that took the first match
    was captioned about the mask."""
    help_map = report_column_help(report_path)

    got = run_report_page({"columnHelp": help_map,
                           "helpFor": [["volume_um3", "instance_"], "volume_um3"]})["helpFor"]

    assert help_map["instance_volume_um3"] in got[0]
    assert help_map["volume_um3"] in got[1]
    assert got[0] != got[1]


def test_the_page_finds_the_description_through_the_reshaping_it_does(report_path):
    """An instance metric loses its prefix on the way in, and the long distance rows become
    one wide column per target. Both still have to find the column they came from."""
    help_map = report_column_help(report_path)

    got = run_report_page({
        "columnHelp": help_map,
        # `volume_um3` is `instance_volume_um3` in the file when a panel is about
        # instances; the distance columns are built by the page and not in the file at all.
        "helpFor": [["volume_um3", "instance_"], "distance_to_pm_um",
                    "distance_to_nucleus_mean_um"],
    })["helpFor"]

    assert help_map["instance_volume_um3"] in got[0]
    assert help_map["distance_um"] in got[1]
    assert "pm" in got[1], "it says which structure it was measured to"
    assert help_map["distance_mean_um"] in got[2]


def test_a_report_written_before_the_descriptions_simply_has_none(report_path):
    """No explanation is the honest answer; an invented one is not."""
    got = run_report_page({"columnHelp": {}, "helpFor": ["sphericity", "volume_um3"]})["helpFor"]

    assert got == ["", ""]


def test_sphericity_says_what_a_value_over_one_means(report_path):
    """The question every reader asks, so the answer travels with the number."""
    text = report_column_help(report_path)["instance_sphericity"]

    assert "1 is a perfect sphere" in text
    assert "equal-volume sphere" in text
    # And it does not repeat the claim it used to make, which the code contradicts: the
    # boundary is ITK's Crofton estimator, not a count of voxel faces.
    assert "voxel face" not in text.lower()
