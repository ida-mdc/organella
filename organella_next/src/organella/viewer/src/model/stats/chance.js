// ── what a distance is read against ──────────────────────────────────────────
//
// The run measured, per object and target, the distance to that target from everywhere in
// the object: the chance distribution. It is what turns "these granules are 300 nm from the
// membrane" into a statement - against a cell where half the volume is within 200 nm that
// is *far*, and against one where half of it is a micrometre away it is close. The shape of
// the object, not the structure, decides which.
//
// These read it the way the panels need it: the stored histograms in the same columns an
// instance's own histogram arrives in, so histSpan and aggregateHistograms take them
// unchanged.

/** The stored chance histograms for these objects, shaped like instance rows. */
function chanceRows(objectIds, target) {
  var col = distColumnsFor(target);
  var out = [];
  (objectIds || []).forEach(function(id) {
    var found = (CHANCE[String(id)] || {})[target];
    if (!found || found.counts == null) return;
    var row = { object_id: String(id), chance_voxels: found.voxels,
                chance_median: found.median, chance_mean: found.mean,
                chance_excluded: found.excluded };
    row[col.hist] = found.counts;
    row[col.histMin] = found.min;
    row[col.histMax] = found.max;
    out.push(row);
  });
  return out;
}

/** The median of a binned population, interpolated inside the bin that holds it. */
function medianOfCounts(counts, min, width) {
  var total = counts.reduce(function(a, b){ return a + b; }, 0);
  if (!(total > 0)) return null;
  var half = total / 2, seen = 0;
  for (var i = 0; i < counts.length; i++) {
    if (seen + counts[i] >= half) {
      return min + (i + (counts[i] > 0 ? (half - seen) / counts[i] : 0)) * width;
    }
    seen += counts[i];
  }
  return min + counts.length * width;
}

/**
 * The pooled chance distribution of one target over several objects.
 *
 * One object answers with what the run measured off its own 4096-bin histogram. Several are
 * pooled first - a facet is the objects in it, and its reference has to be their distances
 * together, weighted by how much object each brought - and the median then comes off the
 * pooled bins.
 */
function chanceStats(objectIds, target) {
  var rows = chanceRows(objectIds, target);
  if (!rows.length) return null;
  var excluded = rows[0].chance_excluded || null;
  if (rows.length === 1) {
    return { median: rows[0].chance_median, mean: rows[0].chance_mean,
             voxels: rows[0].chance_voxels, objects: 1, excluded: excluded };
  }
  var span = histSpan(rows, target);
  var agg = span ? aggregateHistograms(rows, target, 200, span) : null;
  var voxels = 0, weighted = 0;
  rows.forEach(function(r) {
    if (r.chance_voxels == null || r.chance_mean == null) return;
    voxels += r.chance_voxels;
    weighted += r.chance_mean * r.chance_voxels;
  });
  return {
    median: agg ? medianOfCounts(agg.counts, agg.min, agg.width) : null,
    mean: voxels > 0 ? weighted / voxels : null,
    voxels: voxels || null, objects: rows.length, excluded: excluded
  };
}

/** The chance distribution as a curve on a given grid, to draw a measured one against. */
function chanceCurve(objectIds, target, span) {
  var rows = chanceRows(objectIds, target);
  if (!rows.length || !span) return null;
  var own = histSpan(rows, target);
  return aggregateHistograms(rows, target, 30,
    { min: span.min, max: span.max, srcWidth: own ? own.srcWidth : null });
}

/** Two spans as one, so a measured distribution and its reference share an axis. */
function spanUnion(a, b) {
  if (!a) return b;
  if (!b) return a;
  return { min: Math.min(a.min, b.min), max: Math.max(a.max, b.max),
           srcWidth: a.srcWidth != null ? a.srcWidth : b.srcWidth };
}

//
// This is the one number in the report that cannot be read off the data directly, and the
// reason it exists is worth stating plainly: **a group of ten reaches closer to anything
// than a group of two whatever it is made of**. The closest of ten draws is closer than the
// closest of two, so plotting a group's distance against its size measures the arithmetic
// of taking a minimum and not the biology of a network.
//
// So each group is scored against the groups of *its own size* that the same structure in
// the same object could have formed. A group is a chain of one structure, so that pool is
// simply that object's instances of it: of all C(n, k) subsets of size k, what share does
// this group beat by reaching closer? 50% when nothing is going on, whatever k is.
//
// Exact, with no simulation. P(a random k-subset stays beyond `reach`) is
// C(n - closer - equal, k) / C(n, k) - every member drawn from the instances further out -
// and the ties are split evenly, which is what keeps the score flat under the null instead
// of drifting up wherever distances repeat (and in whole voxel steps, they repeat a lot).

// Lanczos, which is exact enough at integer arguments that C(n, k) comes back to the last
// couple of digits. In logs throughout: C(8000, 4000) has no floating-point value at all.
var LANCZOS = [676.5203681218851, -1259.1392167224028, 771.32342877765313,
  -176.61502916214059, 12.507343278686905, -0.13857109526572012,
  9.9843695780195716e-6, 1.5056327351493116e-7];

function logGamma(z) {
  if (z < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * z)) - logGamma(1 - z);
  z -= 1;
  var x = 0.99999999999980993;
  for (var i = 0; i < LANCZOS.length; i++) x += LANCZOS[i] / (z + i + 1);
  var t = z + LANCZOS.length - 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(x);
}

function logChoose(n, k) {
  if (k < 0 || k > n || n < 0) return -Infinity;
  if (k === 0 || k === n) return 0;
  return logGamma(n + 1) - logGamma(k + 1) - logGamma(n - k + 1);
}

/**
 * The share of same-size groups this one beats, in 0..1, 50% under the null.
 *
 * `distances` is the pool - one object's instances of the structure, measured to one target
 * - `k` the group's size, and `reach` its own closest approach.
 */
function baselineScore(distances, k, reach) {
  var n = distances.length;
  if (!n || k < 1 || k > n || reach == null || !isFinite(reach)) return null;
  var closer = 0, equal = 0;
  distances.forEach(function(v) {
    if (v < reach) closer++;
    else if (v === reach) equal++;
  });
  var total = logChoose(n, k);
  // Beyond it: every member drawn from the instances further out than `reach`.
  var beyond = Math.exp(logChoose(n - closer - equal, k) - total);
  // At it or beyond: every member drawn from those not closer. The difference is the tie.
  var notCloser = Math.exp(logChoose(n - closer, k) - total);
  var tied = Math.max(0, notCloser - beyond);
  return Math.min(1, beyond + tied / 2);
}
