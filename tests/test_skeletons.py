"""Which entities get skeletonised, and computing each object's set only once."""

import math

import numpy as np
import pytest


from label_anatomy.analysis import cache as skeletons
from label_anatomy.analysis.meshes import MeshOptions, mesh_rows_for_object

from label_anatomy.measure.instances import InstanceMeasurer

from conftest import object_stack
from label_anatomy.analysis.cache import CACHE
from label_anatomy.config import forced_surface, parse_geometry_as, wants_skeletons

SHAPE = (10, 24, 24)
VOXEL = (0.1, 0.02, 0.02)
# What a run has to name for anything to be skeletonised at all.
SKELETONISE = {"mito": "skeleton"}


@pytest.fixture(autouse=True)
def clean_cache():
    CACHE.clear()
    yield
    CACHE.clear()


def _strand(label=1, y=6) -> np.ndarray:
    vol = np.zeros(SHAPE, dtype=np.int32)
    vol[4:6, y:y + 2, 4:20] = label          # a filament, worth a skeleton
    return vol


def _object(**entities):
    return object_stack(entities, voxel_size=VOXEL)


def _two_strands_of_one_structure():
    """Two instances of one entity, close enough to touch: what a contact is between."""
    volumes = {"mito": _strand(1) + _strand(2, y=9)}
    return volumes, {"mito": "label"}


# ── the filter ────────────────────────────────────────────────────────────────

def test_naming_nothing_means_no_entity():
    """Skeletonising is opt-in: branches and tortuosity mean something for a filament and
    nothing for a granule, whose skeleton is one branch the length of its diameter - and it
    is the most expensive thing in a run, so it is not done on the off chance."""
    assert wants_skeletons("granules", {}) is False


def test_names_match_however_they_were_capitalised():
    # --geometry-as ER=skeleton has to match the entity discovered as 'er'.
    allowed = parse_geometry_as("mito=skeleton, ER=tube")
    assert wants_skeletons("er", allowed)
    assert wants_skeletons("mito", allowed)
    assert not wants_skeletons("granules", allowed)


def test_an_empty_filter_means_nothing_gets_skeletonised():
    assert parse_geometry_as(None) == {}
    assert not wants_skeletons("mito", {})
    # A structure asked for as a plain mesh or an ellipsoid needs no centre line either.
    assert not wants_skeletons("mito", parse_geometry_as("mito=ellipsoid"))
    assert forced_surface("mito", parse_geometry_as("mito=ellipsoid")) == "ellipsoid"


def test_excluded_entities_report_skeleton_metrics_as_not_measured(monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_GEOMETRY_AS", "mito=skeleton")
    record = _object(mito=(_strand(), "label"), granules=(_strand(1, 12), "label"))

    instances = InstanceMeasurer().measure(record).tables["instance"]

    by_entity = dict(zip(instances["instance_entity"], instances["instance_length_um"]))
    assert by_entity["mito"] > 0                    # asked for
    assert by_entity["granules"] is None            # not measured: null, not zero


def test_nothing_is_skeletonised_unless_it_was_named(monkeypatch):
    monkeypatch.delenv("LABEL_ANATOMY_GEOMETRY_AS", raising=False)
    record = _object(mito=(_strand(), "label"), granules=(_strand(1, 12), "label"))

    instances = InstanceMeasurer().measure(record).tables["instance"]

    # No skeleton, so no skeleton metrics - left unmeasured rather than filled with a zero.
    assert all(length is None or math.isnan(length)
               for length in instances["instance_length_um"])


def test_the_entities_named_are_the_ones_measured(monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_GEOMETRY_AS", "mito=skeleton")
    record = _object(mito=(_strand(), "label"), granules=(_strand(1, 12), "label"))

    instances = InstanceMeasurer().measure(record).tables["instance"]
    by_entity = dict(zip(instances["instance_entity"], instances["instance_length_um"]))

    assert by_entity["mito"] > 0
    assert by_entity["granules"] is None or math.isnan(by_entity["granules"])


# ── the cache ─────────────────────────────────────────────────────────────────

def test_the_second_reader_of_an_object_gets_the_cached_skeletons(monkeypatch):
    calls = []
    real = skeletons.compute_skeletons

    def counting(labels, voxel, **kwargs):
        calls.append(labels.shape)
        return real(labels, voxel, **kwargs)

    monkeypatch.setattr(skeletons, "compute_skeletons", counting)
    monkeypatch.setenv("LABEL_ANATOMY_GEOMETRY_AS", "mito=skeleton")
    record = _object(mito=(_strand(), "label"))
    volumes = {"mito": record.data[0]}

    InstanceMeasurer().measure(record)                       # metrics
    mesh_rows_for_object(volumes, {"mito": "label"}, VOXEL, object_id="object_a",
                       options=MeshOptions(contact_max_um=None,
                                           geometry_as=SKELETONISE))  # geometry

    # Skeletonising is the most expensive step; --with-mesh must not pay for it twice.
    assert len(calls) == 1


def test_a_different_object_is_not_served_from_the_cache(monkeypatch):
    calls = []
    real = skeletons.compute_skeletons
    monkeypatch.setattr(
        skeletons, "compute_skeletons",
        lambda labels, voxel, **kw: (calls.append(1), real(labels, voxel, **kw))[1],
    )
    monkeypatch.setenv("LABEL_ANATOMY_GEOMETRY_AS", "mito=skeleton")

    InstanceMeasurer().measure(_object(mito=(_strand(), "label")))
    InstanceMeasurer().measure(_object(mito=(_strand(1, 12), "label")))

    # Same object_id, different data: object folder names are not unique across groups, so
    # answering on the name alone would hand one object another object's skeletons.
    assert len(calls) == 2


def test_geometry_matches_whether_it_was_cached_or_not(monkeypatch):
    monkeypatch.setenv("LABEL_ANATOMY_GEOMETRY_AS", "mito=skeleton")
    volumes, kinds = {"mito": _strand()}, {"mito": "label"}
    options = MeshOptions(contact_max_um=None, geometry_as=SKELETONISE)
    fresh = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                               options=options)
    CACHE.clear()
    InstanceMeasurer().measure(_object(mito=(volumes["mito"], "label")))
    cached = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                                options=options)

    assert [r["skeleton"] for r in cached] == [r["skeleton"] for r in fresh]
    assert all(r["skeleton"] for r in cached)


def test_contacts_are_found_once_however_many_readers_ask(monkeypatch):
    import label_anatomy.analysis.gaps as contacts_module
    from label_anatomy.measure.contacts import ContactMeasurer

    calls = []
    real = contacts_module.pairwise_instance_gaps
    monkeypatch.setattr(
        contacts_module, "pairwise_instance_gaps",
        lambda *a, **kw: (calls.append(1), real(*a, **kw))[1],
    )
    volumes, kinds = _two_strands_of_one_structure()
    record = _object(mito=(volumes["mito"], "label"))

    ContactMeasurer().measure(record)                        # the report's contact rows
    mesh_rows_for_object({"mito": record.data[0]}, kinds, VOXEL, object_id="object_a",
                       options=MeshOptions(contact_max_um=0.5))  # the 3D viewer's copy

    # Finding pairs is seconds to minutes depending on instance count; --with-mesh must
    # not pay for it twice.
    assert len(calls) == 1


def test_a_different_gap_threshold_is_not_served_from_the_cache():
    volumes, kinds = _two_strands_of_one_structure()
    wide = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                              options=MeshOptions(contact_max_um=0.5))
    tight = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                               options=MeshOptions(contact_max_um=0.001))

    n_wide = sum(1 for r in wide if r["row_type"] == "contact")
    n_tight = sum(1 for r in tight if r["row_type"] == "contact")
    assert n_wide > n_tight


def test_the_mesh_overlay_follows_the_same_filter():
    volumes = {"mito": _strand(), "granules": _strand(1, 12)}
    kinds = {"mito": "label", "granules": "label"}
    rows = mesh_rows_for_object(volumes, kinds, VOXEL, object_id="object_a",
                              options=MeshOptions(geometry_as={"mito": "skeleton"},
                                                  contact_max_um=None))

    overlay = {r["entity_name"]: bool(r["skeleton"]) for r in rows}
    assert overlay == {"mito": True, "granules": False}
