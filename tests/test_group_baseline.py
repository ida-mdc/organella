"""The group-reach baseline: the one number in the report that is not read off the data.

It exists because of one fact about taking a minimum: **a group of ten reaches closer to
anything than a group of two**, whatever either is made of, because the closest of ten
draws is closer than the closest of two. So a group's distance cannot be compared across
sizes, and plotting it against size measures the arithmetic rather than the biology.

Each group is therefore scored against the groups of its own size the same object could
have formed - its own instances of that structure - as the share of them it beats. The two
properties worth pinning are that the share is the one brute force gives, and that it is
50% under the null at every size. Both are checked by enumerating every subset.
"""

import itertools
from math import comb, log

import pytest
from conftest import run_report_page
from test_contacts_view import batch_with_distances


def scores(cases):
    return run_report_page({"baselineScore": cases})["baselineScore"]


def one_score(distances, k, reach):
    return scores([{"distances": distances, "k": k, "reach": reach}])[0]


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


# ── the score ────────────────────────────────────────────────────────────────

def test_the_score_is_the_share_brute_force_gives():
    cases = [
        ([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], 2, 0.2),
        ([0.9, 0.1, 0.5, 0.4, 0.3, 0.7, 0.2], 3, 0.3),
        ([0.5, 0.5, 0.5, 0.1, 0.9], 2, 0.5),
        ([0.4] * 6, 3, 0.4),
        ([0.1, 0.2, 0.3, 0.4], 4, 0.1),
    ]
    for (distances, k, reach), mine in zip(cases, scores(
            [{"distances": d, "k": k, "reach": r} for d, k, r in cases])):
        assert mine == pytest.approx(enumerated(distances, k, reach), abs=1e-12)


@pytest.mark.parametrize("n,k", [(12, 3), (20, 5), (9, 4), (10, 1)])
def test_the_score_averages_a_half_over_every_group_that_could_have_formed(n, k):
    """Which is what makes 50% the line to read it against, at any size.

    Without ties split evenly it drifts up wherever distances repeat - and they repeat a
    lot, since a distance is measured in whole voxel steps.
    """
    distances = [round(0.03 * ((i * 7) % n), 3) for i in range(n)]
    subsets = list(itertools.combinations(range(n), k))
    got = scores([{"distances": distances, "k": k,
                   "reach": min(distances[i] for i in s)} for s in subsets])

    assert sum(got) / len(got) == pytest.approx(0.5, abs=1e-12)


def test_a_group_that_is_every_instance_cannot_beat_anything():
    """There is one subset of size n and it is the group, so the score is exactly a half."""
    assert one_score([0.1, 0.2, 0.3], 3, 0.1) == pytest.approx(0.5)


def test_reaching_closest_of_all_beats_almost_everything():
    distances = [0.01] + [0.5] * 9

    lone = one_score(distances, 1, 0.01)

    # One instance drawn at random is the closest one in 1 of 10 draws, so a group of one
    # that *is* the closest beats the other 9 and ties with itself.
    assert lone == pytest.approx(0.9 + 0.05)


def test_a_bigger_group_needs_to_reach_further_in_for_the_same_score():
    """The whole point: at a fixed distance, a bigger group is less impressive."""
    distances = [round(0.05 * i, 3) for i in range(1, 21)]

    at_two = one_score(distances, 2, 0.25)
    at_eight = one_score(distances, 8, 0.25)

    assert at_two > at_eight, "the same reach scores lower for a larger group"


def test_a_group_of_one_is_scored_and_a_group_of_none_is_not():
    assert one_score([0.1, 0.2], 1, 0.1) is not None
    assert one_score([0.1, 0.2], 0, 0.1) is None
    assert one_score([], 1, 0.1) is None
    assert one_score([0.1, 0.2], 3, 0.1) is None, "a group cannot outgrow its own object"


# ── the rows the panel plots ─────────────────────────────────────────────────

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
