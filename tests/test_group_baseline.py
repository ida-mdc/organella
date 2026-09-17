import itertools
from math import comb, log

import pytest
from conftest import run_report_page
from test_contacts_view import batch_with_distances


def enumerated(distances, k, reach):
    """The same share, by looking at every subset there is."""
    beyond = tied = 0
    for subset in itertools.combinations(range(len(distances)), k):
        closest = min(distances[i] for i in subset)
        if closest > reach:
            beyond += 1
        elif closest == reach:
            tied += 1
    return (beyond + tied / 2) / comb(len(distances), k)


# ── the combinatorics under it ───────────────────────────────────────────────

def test_the_binomial_is_exact_where_it_can_be_checked():
    """In logs throughout, because C(8000, 4000) has no floating-point value at all."""
    cases = [(4, 2), (10, 3), (52, 5), (100, 0), (100, 100), (8000, 4000)]
    got = run_report_page({"logChoose": cases})["logChoose"]

    for (n, k), mine in zip(cases, got):
        assert mine == pytest.approx(log(comb(n, k)), rel=1e-12), (n, k)


def test_asking_for_more_than_there_are_is_not_a_number():
    assert run_report_page({"logChoose": [(5, 7)]})["logChoose"] == [None]


def baseline_of(rows, *, scope, entity="mito", gap=1.0):
    return run_report_page({
        "rows": rows, "structure": entity,
        "groupBaseline": [{"scope": scope, "entity": entity, "gap": gap}],
    })["groupBaseline"][0]


def test_a_group_is_scored_against_its_own_objects_instances():
    """The pool is that object's instances of that structure, and nobody else's.

    Scored against the whole cohort, an object whose instances all sit far out would make
    every group in every other object look close.
    """
    rows = batch_with_distances(
        [("object_a", "mito", 1, {"pm": 0.1}),
         ("object_a", "mito", 2, {"pm": 0.2}),
         ("object_a", "mito", 3, {"pm": 0.9}),
         ("object_a", "mito", 4, {"pm": 1.0}),
         ("object_b", "mito", 1, {"pm": 5.0}),
         ("object_b", "mito", 2, {"pm": 6.0})],
        [("object_a", "mito", 1, 2)],
        objects=("object_a", "object_b"))

    built = baseline_of(rows, scope=["object_a", "object_b"])

    assert len(built["rows"]) == 1
    group = built["rows"][0]
    assert group["object_id"] == "object_a"
    # Group of 2 reaching 0.1, against object_a's own four instances.
    assert group["distance_to_pm_um"] == pytest.approx(
        100 * enumerated([0.1, 0.2, 0.9, 1.0], 2, 0.1))


def test_the_concrete_distance_is_kept_beside_the_score():
    """A score does not say how close "closer than chance" actually is."""
    rows = batch_with_distances(
        [("object_a", "mito", 1, {"pm": 0.3}), ("object_a", "mito", 2, {"pm": 0.7}),
         ("object_a", "mito", 3, {"pm": 0.9})],
        [("object_a", "mito", 1, 2)])

    group = baseline_of(rows, scope=["object_a"])["rows"][0]

    assert group["distance_to_pm_um__reach"] == pytest.approx(0.3)


def test_a_real_report_scores_every_group_it_has(report_path):
    from conftest import report_rows

    scope = ["object_a", "object_b", "object_c", "object_d"]
    built = baseline_of(report_rows(report_path), scope=scope, gap=10.0)

    assert built["rows"], "a wide gap groups everything"
    for group in built["rows"]:
        for target in built["targets"]:
            score = group.get(target)
            if score is None:
                continue
            assert 0 <= score <= 100, (target, score)
