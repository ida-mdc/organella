//
// Mann-Whitney U, two-sided: a rank test, so it says nothing about normality and nothing
// about what the distributions look like beyond their ordering - which is the right amount
// to claim from a handful of segmented objects.
//
// Exact where it can be, and that matters here, as more than pedantry: a batch is often
// three or four objects per condition, and at n=4 against n=4 the normal approximation is
// out by enough to move a p across 0.05. With no ties the null distribution is tabulated;
// with ties - one median per cell is often a whole number, or a 0% or a 100% - the same
// arrangements are enumerated with their midranks, which is exact whatever the ties are.
// Only samples too large to enumerate fall to the tie-corrected normal approximation.

var EXACT_U_MAX = 20;

   // above this the exact table costs more than it is worth
var PERMUTATION_MAX = 300000;

   // arrangements enumerated before the approximation takes over

/**
 * The exact two-sided p of a rank-sum test with ties, from every arrangement of the pooled
 * ranks into two groups of the sizes given. `ranked` is :func:`rankedPair`'s result, whose
 * midranks are all the arrangements need: it is the multiset of ranks that is split, and the
 * observed rank sum of the first sample is what is compared. Null where there are too many
 * arrangements to walk.
 */
function permutationP(ranked, n1, n2) {
  var n = n1 + n2, rank = ranked.ranks;
  var arrangements = Math.round(Math.exp(logChoose(n, n1)));
  if (!(arrangements <= PERMUTATION_MAX)) return null;
  var centre = n1 * (n + 1) / 2, far = Math.abs(ranked.rankSumA - centre) - 1e-9;
  var count = 0;
  var walk = function(from, left, sum) {
    if (!left) { if (Math.abs(sum - centre) >= far) count++; return; }
    for (var at = from; at <= n - left; at++) walk(at + 1, left - 1, sum + rank[at]);
  };
  walk(0, n1, 0);
  return count / arrangements;
}

/**
 * The smallest two-sided p a rank test can return for samples of this size, however far
 * apart they are: every value of one above every value of the other is one arrangement in
 * C(n, n1), and it counts from both ends. For four cells against three it is 0.057 - so no
 * comparison of those can be called significant at 0.05, whatever the data say.
 */
function smallestPossibleP(n1, n2) {
  return Math.min(1, 2 / Math.exp(logChoose(n1 + n2, n1)));
}

/**
 * How large the difference is, as two numbers a reader can hold: Cliff's delta - the share
 * of pairs in which the first group is above the second, less the share in which it is
 * below, from -1 to +1 - and the Hodges-Lehmann shift, the median of every first-minus-second
 * difference, in the units of the measurement. Null for samples too large to pair up.
 */
function effectOf(a, b) {
  if (a.length * b.length > 250000) return null;
  var above = 0, below = 0, gaps = [];
  a.forEach(function(x) {
    b.forEach(function(y) {
      if (x > y) above++; else if (x < y) below++;
      gaps.push(x - y);
    });
  });
  gaps.sort(function(x, y){ return x - y; });
  return { delta: (above - below) / (a.length * b.length), shift: quantileSorted(gaps, 0.5) };
}

/** Ranks with ties averaged, and the tie sizes the variance correction needs. */
function rankedPair(a, b) {
  var all = a.map(function(v){ return { v:v, g:0 }; })
    .concat(b.map(function(v){ return { v:v, g:1 }; }))
    .sort(function(x, y){ return x.v - y.v; });
  var ranks = new Array(all.length), ties = [], i = 0;
  while (i < all.length) {
    var j = i;
    while (j + 1 < all.length && all[j + 1].v === all[i].v) j++;
    var rank = (i + j) / 2 + 1;
    for (var k = i; k <= j; k++) ranks[k] = rank;
    ties.push(j - i + 1);
    i = j + 1;
  }
  var rankSumA = 0;
  all.forEach(function(entry, at){ if (entry.g === 0) rankSumA += ranks[at]; });
  return { ranks: ranks, rankSumA: rankSumA, ties: ties, tied: ties.some(function(t){ return t > 1; }) };
}

/**
 * How many of the C(n1+n2, n1) arrangements give each value of U.
 *
 * The null distribution itself, by the standard recurrence: the smallest sample's next
 * member either sits above all of the other sample's remaining members or it does not.
 */
function exactUCounts(n1, n2) {
  var width = n1 * n2 + 1;
  var counts = [];
  for (var i = 0; i <= n1; i++) {
    counts.push([]);
    for (var j = 0; j <= n2; j++) counts[i].push(null);
  }
  var of = function(a, b) {
    if (counts[a][b]) return counts[a][b];
    var row = new Float64Array(width);
    if (a === 0 || b === 0) {
      row[0] = 1;
    } else {
      var left = of(a - 1, b), down = of(a, b - 1);
      for (var u = 0; u < width; u++) {
        row[u] = (u >= b ? left[u - b] : 0) + down[u];
      }
    }
    counts[a][b] = row;
    return row;
  };
  return of(n1, n2);
}

function normalCdf(z) {
  // Abramowitz & Stegun 7.1.26 on |z|, mirrored: enough for a p-value shown to three places.
  var t = 1 / (1 + 0.2316419 * Math.abs(z));
  var poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
    + t * (-1.821255978 + t * 1.330274429))));
  var upper = 0.3989422804014327 * Math.exp(-z * z / 2) * poly;
  return z >= 0 ? 1 - upper : upper;
}

/**
 * Two-sided Mann-Whitney U between two samples.
 *
 * Returns null when there is nothing to test: fewer than two values on a side, or two
 * samples that are one constant value.
 */
function mannWhitney(a, b) {
  var finite = function(v){ return v != null && isFinite(v); };
  a = a.filter(finite);
  b = b.filter(finite);
  var n1 = a.length, n2 = b.length;
  if (n1 < 2 || n2 < 2) return null;
  var ranked = rankedPair(a, b);
  var u1 = ranked.rankSumA - n1 * (n1 + 1) / 2;
  var u2 = n1 * n2 - u1;
  var u = Math.min(u1, u2);

  if (!ranked.tied && n1 <= EXACT_U_MAX && n2 <= EXACT_U_MAX) {
    var counts = exactUCounts(n1, n2);
    var total = 0, atOrBelow = 0;
    for (var k = 0; k < counts.length; k++) {
      total += counts[k];
      if (k <= u) atOrBelow += counts[k];
    }
    return withEffect({ u: u, n1: n1, n2: n2, method: 'exact',
                        p: Math.min(1, 2 * atOrBelow / total) }, a, b);
  }
  // Two samples that are one constant value have nothing to rank, whatever walks there are.
  if (ranked.ties.length === 1) return null;
  if (ranked.tied) {
    var walked = permutationP(ranked, n1, n2);
    if (walked !== null) {
      return withEffect({ u: u, n1: n1, n2: n2, method: 'permutation', p: walked }, a, b);
    }
  }

  var n = n1 + n2;
  var tieSum = ranked.ties.reduce(function(sum, t){ return sum + (t * t * t - t); }, 0);
  var variance = (n1 * n2 / 12) * ((n + 1) - tieSum / (n * (n - 1)));
  if (!(variance > 0)) return null;
  // Taken from the larger of the two U's with a half-step continuity correction, which is
  // what scipy's asymptotic branch does. Without the correction this reads a few thousandths
  // low, and with ties and a small n it was out by a tenth - enough to matter at 0.05.
  var z = (Math.max(u1, u2) - n1 * n2 / 2 - 0.5) / Math.sqrt(variance);
  return withEffect({ u: u, n1: n1, n2: n2, method: 'asymptotic',
                      p: Math.min(1, 2 * (1 - normalCdf(z))) }, a, b);
}

/** A test result with the smallest p its sample sizes allow, and how big the difference is. */
function withEffect(test, a, b) {
  test.pMin = smallestPossibleP(test.n1, test.n2);
  // A comparison that cannot come out below 0.05 is not a test of anything at that level,
  // and says so wherever it is shown.
  test.floor = test.pMin > 0.05;
  var effect = effectOf(a, b);
  test.delta = effect ? effect.delta : null;
  test.shift = effect ? effect.shift : null;
  return test;
}

/** The conventional stars, with "ns" spelled out where a blank might stand. */
function significanceStars(p) {
  if (p == null) return '';
  if (p <= 0.0001) return '****';
  if (p <= 0.001) return '***';
  if (p <= 0.01) return '**';
  if (p <= 0.05) return '*';
  return 'ns';
}

function formatP(p, compact) {
  if (p == null) return '–';
  if (p < 0.001) return 'p < 0.001';
  // A p of 1 between two handfuls of cells is "nothing to tell apart", not proof they are
  // the same, so it is not printed to the last digit.
  if (p > 0.995) return 'p ≈ 1';
  return 'p ' + (compact ? '' : '= ') + (p < 0.1 ? p.toFixed(3) : p.toFixed(2));
}

/**
 * What a bracket or a ranked row says of a test: the stars and the p behind them, always both.
 * A bracket that showed the p only where the sample sizes made significance impossible read
 * as if some comparisons had a p and others had not.
 */
function significanceLabel(test) {
  // Nothing where this many cells cannot reach 0.05 at all: "ns" would read as "no difference", and a
  // p that no data could have lowered says nothing either.
  if (test.floor) return '';
  return significanceStars(test.p) + ' \u00b7 ' + formatP(test.p, true);
}

/**
 * Every pair of facets, tested - on the objects behind them, not on their instances.
 *
 * A facet of a real batch is four cells holding forty thousand granules. Tested on the
 * granules, any difference at all comes back as p < 0.001, because the test is told it has
 * forty thousand independent samples when it has four: granules inside one cell share that
 * cell's size, its fixation, its glucose. So where the caller knows which object each row
 * came from (`units`, one median per object), that is what is compared, and the n the panel
 * reports is a count of objects.
 *
 * A pair is always returned. Where a facet has fewer than two objects there is nothing to
 * compare, and the pair says so (`test` is null, `why` is the reason) instead of dropping
 * out: a group of one cell is still a group on the chart, and a bracket missing from it
 * read as a comparison nobody had made. The panel does not fall back to the instances,
 * because a p-value between two single cells reads as a result and is not one.
 */
/**
 * Every facet a single object: a folder of cells run without -p. There is no group to
 * compare, no per-cell median that differs from the facet's own, and no cell count that is
 * not 1, so the panel leaves those out.
 */
function isUngrouped(series) {
  return series.length > 1 && series.every(function(s){ return s.units != null && s.units.length < 2; });
}

function pairwiseSignificance(series) {
  if (!showSignificance || series.length < 2) return [];
  if (isUngrouped(series)) return [];
  var out = [];
  for (var i = 0; i < series.length; i++) {
    for (var j = i + 1; j < series.length; j++) {
      var a = series[i], b = series[j];
      // A caller that cannot say which object a row came from - a hand-built series - is
      // tested on what it has, and says so.
      var known = a.units != null && b.units != null;
      var why = null, loneName = null;
      if (known && (a.units.length < 2 || b.units.length < 2)) {
        var lone = a.units.length < 2 ? a : b;
        why = lone.units.length + ' ' + noun(lone.units.length !== 1);
        loneName = lone.label || lone.name;
      }
      var test = why ? null : mannWhitney(known ? a.units : a.values, known ? b.units : b.values);
      if (!why && !test) why = 'nothing to rank';
      out.push({ a: i, b: j, test: test, why: why, lone: loneName, perObject: known,
                 aName: a.name, bName: b.name, aLabel: a.label, bLabel: b.label });
    }
  }
  return out;
}

/**
 * Why the pairs that were not tested were not, in lines: "macrophage (1 cell)" where one group
 * is too small, "a vs b (nothing to rank)" where the values were all the same. One annotation
 * and not a bracket each, because three groups with a single cell among them would otherwise
 * stack two rungs of "not tested" on a thumbnail that has room for one. Broken after the colon
 * once it is longer than a thumbnail is wide.
 */
function untestedLines(comparisons) {
  var seen = {}, parts = [];
  comparisons.forEach(function(c) {
    if (c.test) return;
    var part = c.lone ? c.lone + ' (' + c.why + ')'
      : (c.aLabel || c.aName) + ' vs ' + (c.bLabel || c.bName) + ' (' + c.why + ')';
    if (!seen[part]) { seen[part] = true; parts.push(part); }
  });
  if (!parts.length) return [];
  var text = parts.join('; ');
  return text.length > 14 ? ['not tested:', text] : ['not tested: ' + text];
}
