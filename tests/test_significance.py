import random

import pytest
from conftest import run_report_page
from scipy.stats import mannwhitneyu

random.seed(20260903)


def page_tests(pairs):
    return run_report_page({"mannWhitney": [{"a": a, "b": b} for a, b in pairs]})["mannWhitney"]


SEPARATED = ([1, 2, 3, 4], [5, 6, 7, 8])
INTERLEAVED = ([1, 5, 3, 9], [2, 6, 4, 10])
SMALLEST = ([1, 2], [3, 4])
TIED = ([1, 2, 3], [2, 3, 4])
ALL_TIED = ([2, 2, 2, 2], [2, 2, 2, 2])
ROUNDED = ([round(random.gauss(0, 1), 1) for _ in range(25)],
           [round(random.gauss(0.5, 1), 1) for _ in range(25)])
BIG = ([random.gauss(0, 1) for _ in range(40)], [random.gauss(0, 1) for _ in range(40)])
LOPSIDED = (sorted(random.sample(range(200), 12)), sorted(random.sample(range(200, 400), 15)))

CASES = [SEPARATED, INTERLEAVED, SMALLEST, TIED, ROUNDED, BIG, LOPSIDED,
         ([1.5, 2.5, 3.5, 4.5, 5.5], [0.5, 6.5, 7.5, 8.5, 9.5]),
         ([random.gauss(0, 1) for _ in range(30)], [random.gauss(0.4, 1) for _ in range(28)])]


def every_arrangement_p(a, b):
    """The exact two-sided p of the rank-sum statistic, tied or not, by walking every split."""
    from itertools import combinations

    from scipy.stats import rankdata
    pooled = list(a) + list(b)
    ranks = rankdata(pooled)
    n, k = len(pooled), len(a)
    centre = k * (n + 1) / 2
    far = abs(sum(ranks[:k]) - centre) - 1e-9
    hits = total = 0
    for chosen in combinations(range(n), k):
        total += 1
        hits += abs(sum(ranks[i] for i in chosen) - centre) >= far
    return hits / total


def test_the_p_value_is_the_one_scipy_computes():
    """Every case, at whichever branch the page picked, to scipy's own precision.

    A small tied sample is an exact permutation test, which scipy has no `method` for, so it
    is checked against every split of the pooled values instead.
    """
    for (a, b), mine in zip(CASES, page_tests(CASES)):
        if mine["method"] == "permutation":
            assert mine["p"] == pytest.approx(every_arrangement_p(a, b), abs=1e-9), (a, b)
            continue
        reference = mannwhitneyu(a, b, alternative="two-sided", method=mine["method"])
        assert mine["p"] == pytest.approx(float(reference.pvalue), abs=1e-6), (a, b)


def test_a_small_untied_sample_is_tested_exactly():
    """It has to be: at n=4 against n=4 the approximation moves a p across 0.05.

    The exact two-sided p there is 0.0286 and the normal approximation reads 0.0143 without
    a continuity correction - a bracket that says ** where the data says *.
    """
    exact = page_tests([SEPARATED, INTERLEAVED, SMALLEST])
    assert [t["method"] for t in exact] == ["exact", "exact", "exact"]
    # And the exact answers are exactly scipy's, not merely close.
    for (a, b), mine in zip([SEPARATED, INTERLEAVED, SMALLEST], exact):
        assert mine["p"] == float(mannwhitneyu(a, b, alternative="two-sided",
                                               method="exact").pvalue)


def test_ties_are_walked_exactly_while_that_is_possible_and_approximated_after():
    """A tied small sample has an exact p, found by enumerating every split with midranks.

    The tie-corrected normal approximation is what is left only when there are too many
    splits to walk - and for four cells against three it can say 0.050 where the truth is
    0.057, which is the difference between a star and none.
    """
    tied = page_tests([TIED, ROUNDED])
    assert [t["method"] for t in tied] == ["permutation", "asymptotic"]
    assert tied[0]["p"] == pytest.approx(every_arrangement_p(*TIED), abs=1e-9)
    assert tied[1]["p"] == pytest.approx(
        float(mannwhitneyu(*ROUNDED, alternative="two-sided", method="asymptotic").pvalue),
        abs=1e-6)


def test_four_cells_against_three_cannot_reach_significance():
    """Whatever the data, the smallest two-sided p is 2/C(7,3) = 0.057."""
    best = page_tests([([1, 2, 3, 4], [5, 6, 7]), ([1.5, 2.5, 3.5, 4.5], [0.1, 0.2, 0.3])])
    for test in best:
        assert test["p"] == pytest.approx(2 / 35)
        assert test["pMin"] == pytest.approx(2 / 35)
        assert test["floor"] is True
    # With more cells a star is possible, and the page does not pretend otherwise.
    roomy = page_tests([([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])])[0]
    assert roomy["floor"] is False and roomy["p"] < 0.05


def test_the_effect_is_how_often_one_group_is_above_the_other_and_by_how_much():
    test = page_tests([([5, 6, 7, 8], [1, 2, 3])])[0]
    assert test["delta"] == pytest.approx(1.0)          # every pair has the first above
    assert test["shift"] == pytest.approx(4.5)          # median of the twelve differences
    reversed_ = page_tests([([1, 2, 3], [5, 6, 7, 8])])[0]
    assert reversed_["delta"] == pytest.approx(-1.0)


def test_two_samples_of_one_constant_value_cannot_be_told_apart():
    """The variance is zero, so there is no test - not a p of 1 and not a divide by zero."""
    assert page_tests([ALL_TIED]) == [None]


def test_a_sample_of_one_is_not_tested():
    assert page_tests([([1], [2, 3, 4])]) == [None]
    assert page_tests([([], [1, 2])]) == [None]


def test_the_stars_follow_the_conventional_thresholds():
    stars = run_report_page({"stars": [0.00005, 0.0005, 0.005, 0.02, 0.2]})["stars"]
    assert stars == ["****", "***", "**", "*", "ns"]


def test_the_null_distribution_sums_to_every_arrangement():
    """The exact branch is a table of counts, and it has to be all of them.

    C(n1+n2, n1) arrangements, spread over U = 0..n1*n2. A table that missed some would
    give a p that is a fraction of the wrong denominator.
    """
    from math import comb

    counts = run_report_page({"exactU": [[4, 4], [3, 7], [6, 6]]})["exactU"]
    for (n1, n2), table in zip([(4, 4), (3, 7), (6, 6)], counts):
        assert len(table) == n1 * n2 + 1
        assert sum(table) == comb(n1 + n2, n1)
        # Symmetric about the middle: U and n1*n2 - U are the same statement.
        assert table == table[::-1]
