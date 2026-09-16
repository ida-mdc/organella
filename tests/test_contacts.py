import os
import numpy as np
import pytest

from organella.measure.contacts import ContactMeasurer

from conftest import object_stack

SHAPE = (10, 20, 20)
VOXEL = (0.1, 0.02, 0.02)   # anisotropic, as in real data


def _object(**entities):
    return object_stack(entities, voxel_size=VOXEL)


def _blocks(*specs: tuple[int, tuple[int, int, int], tuple[int, int, int]]) -> np.ndarray:
    """Label volume from (label, origin, size) blocks."""
    vol = np.zeros(SHAPE, dtype=np.int32)
    for label, origin, size in specs:
        vol[tuple(slice(o, o + s) for o, s in zip(origin, size))] = label
    return vol


def _measure(stack):
    """The contact rows as columns, plus what landed on the object row.

    One list per column, one element per contact row: the shape these assertions were
    written against, now that a contact is a row of the report instead of an element of a
    list column on the object row.
    """
    measured = ContactMeasurer().measure(stack)
    return {**measured.tables["contact"], **measured.columns}


def test_touching_instances_report_the_smallest_possible_gap():
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 5), (3, 3, 3)))  # share an X face
    row = _measure(_object(mito=(mito, "label")))

    assert row["contact_count"] == 1
    assert row["contact_entity"] == ["mito"]
    assert row["contact_label_a"] == [1]
    assert row["contact_label_b"] == [2]
    # One voxel step, not zero: the gap is a distance to the nearest voxel *of* the
    # other instance, and its own voxels are one step away. Matches the original standalone script.
    assert row["contact_gap_um"] == pytest.approx([VOXEL[2]], abs=1e-6)


def test_gap_grows_by_one_voxel_step_per_empty_voxel():
    # Two empty voxels along X (0.02 µm per voxel) → three steps.
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 7), (3, 3, 3)))
    row = _measure(_object(mito=(mito, "label")))

    assert row["contact_gap_um"] == pytest.approx([3 * VOXEL[2]], abs=1e-6)


def test_the_step_size_follows_the_axis_of_approach():
    """Anisotropy is respected: the same voxel distance along Z is 5× larger."""
    mito = _blocks((1, (2, 2, 2), (2, 3, 3)), (2, (5, 2, 2), (2, 3, 3)))  # one empty Z plane
    row = _measure(_object(mito=(mito, "label")))

    assert row["contact_gap_um"] == pytest.approx([2 * VOXEL[0]], abs=1e-6)


def test_pairs_beyond_the_threshold_are_not_recorded(monkeypatch):
    monkeypatch.setenv("ORGANELLA_CONTACT_MAX_UM", "0.05")
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 8), (3, 3, 3)))  # 3 empty = 0.08 µm
    row = _measure(_object(mito=(mito, "label")))

    assert row["contact_count"] == 0
    # No pair, so no contact row: nothing is written rather than a row saying nothing.
    assert row["contact_gap_um"] == []


def test_a_pair_is_always_within_one_structure():
    """Touching a different structure is a distance, not a contact, and a mask has no pairs."""
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 5), (3, 3, 3)))
    er = _blocks((1, (2, 2, 8), (3, 3, 3)))          # right beside mito 2
    nucleus = _blocks((1, (6, 2, 2), (3, 3, 3)))     # a whole structure, not instances
    row = _measure(_object(mito=(mito, "label"), er=(er, "label"),
                           nucleus=(nucleus, "mask")))

    # Only mito 1 ↔ mito 2. The mito/er pair is a distance, and the mask has no instances.
    assert row["contact_count"] == 1
    assert row["contact_entity"] == ["mito"]
    assert row["contact_label_a"] == [1]
    assert row["contact_label_b"] == [2]


def test_the_object_mask_is_not_a_contact_partner():
    pm = np.ones(SHAPE, dtype=np.int32)          # encloses everything
    mito = _blocks((1, (2, 2, 2), (3, 3, 3)), (2, (2, 2, 5), (3, 3, 3)))
    row = _measure(_object(pm=(pm, "mask"), mito=(mito, "label")))

    # Only mito 1 ↔ mito 2. Membrane proximity is a distance, not a contact.
    assert row["contact_count"] == 1
    assert "pm" not in row["contact_entity"]


def test_an_object_with_one_instance_has_no_pairs():
    row = _measure(_object(mito=(_blocks((1, (2, 2, 2), (3, 3, 3))), "label")))

    assert row["contact_count"] == 0


# ── how long it takes, and what that must not change ─────────────────────────


def test_threads_change_how_fast_the_pairs_are_found_and_nothing_else():
    """The one place in a run that walks every instance, so it is the one to thread.

    A local transform per instance, and edt holds the GIL - so the threading is inside each
    transform rather than across them, and what comes out has to be the same pairs at the
    same gaps whatever it was found with.
    """
    from organella.analysis.gaps import pairwise_instance_gaps

    labels = np.zeros((12, 40, 40), dtype=np.int32)
    for i in range(6):
        labels[4:8, 6:10, 4 + i * 6:8 + i * 6] = i + 1
    volumes, kinds = {"mito": labels}, {"mito": "label"}

    serial = pairwise_instance_gaps(volumes, kinds, VOXEL, 0.5, num_threads=1)
    threaded = pairwise_instance_gaps(volumes, kinds, VOXEL, 0.5, num_threads=4)

    assert serial, "the fixture should hold some touching pairs"
    assert serial == threaded


def test_the_two_pools_share_the_machine_between_them(monkeypatch):
    """Cores are shared out per object, for meshing and for the transforms alike.

    The object pool is sized by memory, so on objects of tens of gigabytes it is one at a
    time and the rest of the machine is free - which is why each object's own work is
    given a share rather than a single core. An explicit setting is left alone.
    """
    from organella.config import RunConfig
    from organella.pipeline.batch import _plan_the_two_pools

    monkeypatch.setattr(os, "cpu_count", lambda: 16)

    workers, planned = _plan_the_two_pools(requested=1, n_objects=7, peak_gb=30.0,
                                           config=RunConfig())
    assert workers == 1
    # One object at a time, so that object gets the whole machine for its own work.
    assert planned.edt_threads == 16
    assert planned.mesh_workers >= 1

    # The planning goes onto the config the workers are handed, and an explicit setting
    # already on it is left alone.
    _, planned = _plan_the_two_pools(requested=4, n_objects=7, peak_gb=1.0,
                                     config=RunConfig(edt_threads=3))
    assert planned.edt_threads == 3
