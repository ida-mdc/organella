"""What a measured distance is read against: the same distance from everywhere in the object.

A distance on its own says nothing. Granules 300 nm from the membrane are against it in a
cell where half the volume is a micrometre away and unremarkable in one where half of it is
within 200 nm, and what differs is the cell's shape rather than anything about the granules.
So every target gets one extra row - the distribution of its distance over the object - and
these tests pin what that region is and what comes off it.
"""

import json

import numpy as np
import pytest

from organella.analysis.distances import distance_target, distance_transform_um
from organella.measure.instances import BASELINE_HISTOGRAM_BINS, InstanceMeasurer

from conftest import object_stack

SHAPE = (10, 20, 20)
VOXEL = (0.1, 0.02, 0.02)


def _object(**entities: tuple[np.ndarray, str]):
    return object_stack(entities, voxel_size=VOXEL)


def _interior() -> np.ndarray:
    """The bounding mask the objects here are measured inside."""
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1
    return pm


def _blob(label: int, origin, size) -> np.ndarray:
    vol = np.zeros(SHAPE, dtype=np.int32)
    vol[tuple(slice(o, o + s) for o, s in zip(origin, size))] = label
    return vol


def _baseline(stack) -> dict:
    """The chance rows of one object, keyed by the target they are of."""
    table = InstanceMeasurer().measure(stack).tables["baseline"]
    return {
        target: {name: values[i] for name, values in table.items()}
        for i, target in enumerate(table["baseline_target"])
    }


def _region_distances(views: dict, target: str, kind: str, *, exclude=()) -> np.ndarray:
    """The same distances the measurer binned, gathered here to check them against."""
    transform = distance_transform_um(
        distance_target(views[target], target, kind, "pm"), VOXEL)
    keep = views["pm"] > 0
    for name in exclude:
        keep &= views[name] == 0
    values = transform[keep]
    return values[values > 0]


def test_one_chance_row_per_structure_measured_to():
    views = {"pm": _interior(), "nucleus": _blob(1, (3, 3, 3), (3, 6, 6)),
             "mito": _blob(1, (4, 10, 10), (2, 3, 3))}
    rows = _baseline(_object(pm=(views["pm"], "mask"), nucleus=(views["nucleus"], "mask"),
                             mito=(views["mito"], "label")))

    # One per target the distance rows cover - the mito target is not among them, because
    # the only instances in this object are its own.
    assert sorted(rows) == ["nucleus", "pm"]
    for target, row in rows.items():
        counts = json.loads(row["baseline_hist_counts"])
        assert len(counts) == BASELINE_HISTOGRAM_BINS
        assert sum(counts) == row["baseline_voxels"]


def test_the_region_is_the_object_and_not_the_structure_measured_to():
    """A structure is not in its own reference: its distance to itself is zero."""
    pm, nucleus = _interior(), _blob(1, (3, 3, 3), (3, 6, 6))
    mito = _blob(1, (4, 10, 10), (2, 3, 3))
    rows = _baseline(_object(pm=(pm, "mask"), nucleus=(nucleus, "mask"),
                             mito=(mito, "label")))

    inside = int((pm > 0).sum())
    assert rows["nucleus"]["baseline_voxels"] == inside - int((nucleus > 0).sum())
    # The bounding mask's target is everything outside it, and that is already out of the
    # region, so all of the object counts towards it.
    assert rows["pm"]["baseline_voxels"] == inside
    assert rows["nucleus"]["baseline_hist_min_um"] > 0


def test_named_structures_are_left_out_of_the_region(monkeypatch):
    """--baseline-exclude: ground an instance could never occupy is not a fair reference."""
    pm, nucleus = _interior(), _blob(1, (3, 3, 3), (3, 6, 6))
    mito = _blob(1, (4, 10, 10), (2, 3, 3))
    entities = dict(pm=(pm, "mask"), nucleus=(nucleus, "mask"), mito=(mito, "label"))

    monkeypatch.setenv("ORGANELLA_BASELINE_EXCLUDE", "nucleus")
    excluded = _baseline(_object(**entities))

    assert excluded["pm"]["baseline_voxels"] == int((pm > 0).sum()) - int((nucleus > 0).sum())
    assert excluded["pm"]["baseline_excluded"] == "nucleus"
    # Leaving the nucleus out takes the deepest part of this object with it, so what is
    # left sits nearer the boundary than the whole of it did.
    monkeypatch.delenv("ORGANELLA_BASELINE_EXCLUDE")
    whole = _baseline(_object(**entities))
    assert whole["pm"]["baseline_excluded"] is None
    assert excluded["pm"]["baseline_mean_um"] < whole["pm"]["baseline_mean_um"]


def test_the_mean_and_median_are_the_regions_own():
    """Measured off the counts, so they have to agree with the values behind them."""
    views = {"pm": _interior(), "nucleus": _blob(1, (3, 3, 3), (3, 6, 6)),
             "mito": _blob(1, (4, 10, 10), (2, 3, 3))}
    rows = _baseline(_object(pm=(views["pm"], "mask"), nucleus=(views["nucleus"], "mask"),
                             mito=(views["mito"], "label")))
    values = _region_distances(views, "nucleus", "mask")
    row = rows["nucleus"]

    assert row["baseline_voxels"] == len(values)
    # The mean is summed over the region, so it is exact.
    assert row["baseline_mean_um"] == pytest.approx(float(values.mean()), rel=1e-6)
    # The median comes off a 4096-bin histogram rather than a sort of every sample: near
    # enough that no reader would read it differently, and that is what is promised.
    bin_width = (row["baseline_hist_max_um"] - row["baseline_hist_min_um"]) / 4096
    assert row["baseline_median_um"] == pytest.approx(float(np.median(values)),
                                                      abs=2 * bin_width)


def test_an_unbounded_object_is_its_own_region():
    """With no bounding mask the whole analysed region is the reference."""
    nucleus, mito = _blob(1, (3, 3, 3), (3, 6, 6)), _blob(1, (4, 10, 10), (2, 3, 3))
    stack = object_stack({"nucleus": (nucleus, "mask"), "mito": (mito, "label")},
                         voxel_size=VOXEL, object_mask=None)
    rows = _baseline(stack)

    assert rows["nucleus"]["baseline_voxels"] == int(np.prod(SHAPE)) - int((nucleus > 0).sum())


def test_the_report_carries_a_chance_row_for_every_object_and_target(table):
    """One real run's rows, so the column survives the writer and the type it is given."""
    rows = table.filter(table["row_type"] == "baseline")
    objects = table.filter(table["row_type"] == "object")["object_id"].to_list()

    assert rows.height == len(objects) * 2          # nucleus and pm, per object
    assert sorted(set(rows["baseline_target"].to_list())) == ["nucleus", "pm"]
    assert rows["baseline_median_um"].is_not_null().all()
    assert rows["baseline_voxels"].min() > 0


# ── where a structure's centre sits, for the structures that have no instances ──


def test_a_structure_carries_the_depth_of_its_own_centre():
    """A mask has no instance to carry a distance, so its own row carries this.

    Off the bounding mask's transform, which the run builds anyway: the comparable way to
    say where a structure sits, since objects differ in size where "3 µm from the centre"
    is only readable against one object's own extent.
    """
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[2:8, 4:16, 4:16] = 1
    nucleus = _blob(1, (4, 5, 5), (2, 3, 3))       # tucked into a corner
    middle = _blob(1, (4, 9, 9), (2, 2, 2))        # in the middle of the object
    measured = InstanceMeasurer().measure(
        object_stack({"pm": (pm, "mask"), "nucleus": (nucleus, "mask"),
                      "middle": (middle, "label")}, voxel_size=VOXEL, object_mask="pm"))

    depths = {name: values["polar_depth_um"]
              for name, values in measured.entity_columns.items()}

    assert set(depths) == {"pm", "nucleus", "middle"}
    # The corner structure's centre is nearer the boundary than the middle one's.
    assert depths["nucleus"] < depths["middle"]
    # And the object's own centre is the deepest point there is.
    assert depths["pm"] >= depths["middle"] > 0


def test_the_depth_reaches_the_structure_row_of_the_report(table):
    """It is measured against the whole object and belongs to one structure: both halves."""
    entities = table.filter(table["row_type"] == "entity")

    assert "polar_depth_um" in table.columns
    assert entities["polar_depth_um"].is_not_null().all()
    # Instance and distance rows are not about a structure as a whole.
    deep = table.filter(table["row_type"] == "instance")
    assert deep["polar_depth_um"].is_null().all()
