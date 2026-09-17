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


def test_the_p_value_is_the_one_scipy_computes():
    """Every case, at whichever branch the page picked, to scipy's own precision."""
    for (a, b), mine in zip(CASES, page_tests(CASES)):
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


def test_ties_fall_back_to_the_corrected_approximation():
    """An exact p is not defined with ties, so the tie-corrected normal one is what is left."""
    tied = page_tests([TIED, ROUNDED])
    assert [t["method"] for t in tied] == ["asymptotic", "asymptotic"]
    for (a, b), mine in zip([TIED, ROUNDED], tied):
        assert mine["p"] == pytest.approx(
            float(mannwhitneyu(a, b, alternative="two-sided", method="asymptotic").pvalue),
            abs=1e-6)


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
