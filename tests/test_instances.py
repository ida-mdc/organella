import numpy as np
import pytest

from label_anatomy.measure.instances import InstanceMeasurer

from conftest import object_stack

SHAPE = (10, 20, 20)
VOXEL = (0.1, 0.02, 0.02)


def _object(**entities: tuple[np.ndarray, str]):
    return object_stack(entities, voxel_size=VOXEL)


def _measure(stack):
    """One object's instance and distance columns, plus what landed on the object row.

    The processor hands back columnar tables - one list per column, one element per row -
    which is the shape these assertions were always written against; what changed is that
    those columns are now rows of the report rather than lists on the object row.
    """
    measured = InstanceMeasurer().measure(stack)
    columns = {name: values for table in measured.tables.values()
               for name, values in table.items()}
    return {**columns, **measured.columns}


def _blocks(*specs: tuple[int, tuple[int, int, int], tuple[int, int, int]]) -> np.ndarray:
    vol = np.zeros(SHAPE, dtype=np.int32)
    for label, origin, size in specs:
        vol[tuple(slice(o, o + s) for o, s in zip(origin, size))] = label
    return vol


def _by_instance(row, entity, label):
    """The row's instance_* values for one instance, as a dict."""
    idx = [
        i for i, (e, l) in enumerate(zip(row["instance_entity"], row["instance_label"]))
        if e == entity and l == label
    ]
    assert len(idx) == 1
    return {k: v[idx[0]] for k, v in row.items() if k.startswith("instance_") and v is not None}


def test_one_element_per_instance_across_all_entities():
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 10), (3, 3, 3)))
    er = _blocks((1, (6, 6, 6), (2, 2, 2)))
    row = _measure(_object(mito=(mito, "label"), er=(er, "label")))

    assert row["instance_entity"] == ["mito", "mito", "er"]
    assert row["instance_label"] == [1, 2, 1]
    voxel_um3 = np.prod(VOXEL)
    assert row["instance_volume_um3"] == pytest.approx([27 * voxel_um3, 27 * voxel_um3, 8 * voxel_um3])
    for col in ("instance_sphericity", "instance_surface_area_um2", "instance_branches"):
        assert len(row[col]) == 3


def test_masks_contribute_no_instances_but_are_distance_targets():
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)))
    nucleus = _blocks((1, (2, 2, 8), (3, 3, 3)))
    row = _measure(_object(mito=(mito, "label"), nucleus=(nucleus, "mask")))

    assert row["instance_entity"] == ["mito"]          # the mask is one structure
    assert row["distance_target"] == ["nucleus"]        # but it can still be measured to
    # Voxel centre to voxel centre: 3 empty voxels along X means 4 steps of 0.02 µm.
    # Same convention as contact_gap_um, and as the original standalone script.
    assert row["distance_um"] == pytest.approx([4 * VOXEL[2]], abs=1e-6)


def test_distances_are_one_element_per_instance_and_target():
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 10), (3, 3, 3)))
    er = _blocks((1, (6, 6, 6), (2, 2, 2)))
    row = _measure(_object(mito=(mito, "label"), er=(er, "label")))

    # 3 instances, each measured to the one entity that is not its own. Grouped by
    # target, because only one distance transform is alive at a time.
    assert len(row["distance_um"]) == 3
    assert list(zip(row["distance_entity"], row["distance_target"])) == [
        ("er", "mito"), ("mito", "er"), ("mito", "er"),
    ]


def test_distance_to_the_object_mask_is_distance_to_the_boundary():
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1               # the object interior
    mito = _blocks((1, (4, 4, 4), (2, 2, 2)))
    row = _measure(_object(pm=(pm, "mask"), mito=(mito, "label")))

    # Measured from inside to the boundary, so it is > 0 for an instance in the middle.
    # The instance starts at x=4 and the first voxel outside the membrane is x=0.
    [(target, distance)] = list(zip(row["distance_target"], row["distance_um"]))
    assert target == "pm"
    assert distance == pytest.approx(4 * VOXEL[2], abs=1e-6)


def test_closest_same_type_is_null_for_a_lone_instance():
    row = _measure(_object(mito=(_blocks((1, (2, 2, 2), (3, 3, 3))), "label")))

    # NULL, not NaN: DuckDB's STDDEV raises on a column holding a NaN, so an unmeasured
    # instance would take down every widget plotting that metric.
    assert row["instance_distance_to_closest_same_type_um"] == [None]


def test_closest_same_type_measures_centroid_distance_within_the_entity():
    mito = _blocks((1, (2, 2, 2), (2, 2, 2)), (2, (2, 2, 12), (2, 2, 2)))
    er = _blocks((1, (2, 2, 6), (2, 2, 2)))   # closer, but a different entity
    row = _measure(_object(mito=(mito, "label"), er=(er, "label")))

    mito_1 = _by_instance(row, "mito", 1)
    # Centroids are 10 voxels apart along X; the nearer 'er' instance does not count.
    assert mito_1["instance_distance_to_closest_same_type_um"] == pytest.approx(10 * VOXEL[2], abs=1e-6)


def test_polarity_is_measured_from_the_object_centre():
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1
    centre = _blocks((1, (4, 9, 9), (2, 2, 2)))          # at the object centre
    off_centre = _blocks((2, (4, 9, 16), (2, 2, 2)))     # displaced along +X
    row = _measure(
        _object(pm=(pm, "mask"), mito=((centre + off_centre), "label"))
    )

    inner = _by_instance(row, "mito", 1)
    outer = _by_instance(row, "mito", 2)
    assert outer["instance_polar_dist_um"] > inner["instance_polar_dist_um"]
    assert outer["instance_polar_az_deg"] == pytest.approx(0.0, abs=1.0)   # +X is azimuth 0


def test_polarity_is_null_without_an_object_mask():
    row = _measure(_object(mito=(_blocks((1, (2, 2, 2), (3, 3, 3))), "label")))

    assert row["instance_polar_dist_um"] == [None]
    assert "object_volume_um3" not in row


def test_object_volume_comes_from_the_object_mask():
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1
    row = _measure(_object(pm=(pm, "mask")))

    assert row["object_volume_um3"] == pytest.approx(8 * 18 * 18 * np.prod(VOXEL))


def test_an_object_with_no_labelled_instances_measures_nothing():
    pm = np.ones(SHAPE, dtype=np.int32)
    row = _measure(_object(pm=(pm, "mask")))

    # No instances, so no instance rows and no distance rows - not a row of nulls each.
    assert row["instance_volume_um3"] == []
    assert row["distance_um"] == []


def test_polarity_spread_is_off_unless_asked_for():
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1
    row = _measure(
        _object(pm=(pm, "mask"), mito=(_blocks((1, (4, 4, 4), (2, 2, 2))), "label"))
    )

    # It walks every voxel of every instance, so it is opt-in like the original flag.
    assert row["instance_polar_spread_deg"] == [None]


def test_polarity_spread_grows_with_the_directions_an_instance_covers(monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_POLARITY_SPREAD", "1")
    pm = np.zeros(SHAPE, dtype=np.int32)
    pm[1:9, 1:19, 1:19] = 1
    compact = _blocks((1, (4, 9, 15), (2, 2, 2)))                 # a blob off to one side
    sprawling = np.zeros(SHAPE, dtype=np.int32)
    sprawling[4:6, 2:18, 5:7] = 2                                 # a strand along Y, elsewhere
    row = _measure(
        _object(pm=(pm, "mask"), mito=((compact + sprawling), "label"))
    )

    spreads = dict(zip(row["instance_label"], row["instance_polar_spread_deg"]))
    assert spreads[2] > spreads[1] > 0


def test_distance_histograms_are_off_unless_asked_for():
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)))
    nucleus = _blocks((1, (2, 2, 8), (3, 3, 3)))
    row = _measure(_object(mito=(mito, "label"), nucleus=(nucleus, "mask")))

    assert "distance_hist_counts" not in row
    assert "distance_mean_um" not in row


def test_distance_histograms_share_their_bins_across_an_entity(monkeypatch):
    import json

    monkeypatch.setenv("LABEL_ANATOMY_DISTANCE_HISTOGRAMS", "1")
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 14), (3, 3, 3)))
    nucleus = _blocks((1, (2, 2, 8), (3, 3, 3)))
    row = _measure(_object(mito=(mito, "label"), nucleus=(nucleus, "mask")))

    # Shared bounds per entity/target pair, unlike the original standalone script's per-instance range:
    # shared bins are what makes two instances' distributions comparable.
    assert len(set(row["distance_hist_min_um"])) == 1
    assert len(set(row["distance_hist_max_um"])) == 1
    for counts, mean, minimum in zip(row["distance_hist_counts"], row["distance_mean_um"],
                                     row["distance_um"]):
        bins = json.loads(counts)
        assert len(bins) == 20
        assert sum(bins) == 27          # every voxel of the instance is counted
        assert mean >= minimum          # the mean cannot beat the closest voxel
