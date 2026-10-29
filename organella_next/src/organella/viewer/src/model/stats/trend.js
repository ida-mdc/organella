// A point per instance answers neither question these panels are asked: with
// thousands of instances the cloud is opaque, and a cloud shows no trend. So x is
// split into equal-count bins and each bin reports the median of y with its 25–75%
// range. Every instance lands in a bin — nothing is sampled away.
var TREND_MIN_N = 24;

      // below this, bin medians are noise; show the points
var TREND_MAX_BANDS = 4;

   // more overlapping ribbons than this is mud

// Pearson correlation coefficient (r); square it for R²
function pearsonR(xs, ys) {
  var n = xs.length;
  if (n < 2) return null;
  var mx = 0, my = 0;
  for (var i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
  mx /= n; my /= n;
  var sxx = 0, syy = 0, sxy = 0;
  for (i = 0; i < n; i++) {
    var dx = xs[i]-mx, dy = ys[i]-my;
    sxx += dx*dx; syy += dy*dy; sxy += dx*dy;
  }
  if (sxx === 0 || syy === 0) return null;
  return sxy / Math.sqrt(sxx * syy);
}

function quantileSorted(sorted, q) {
  if (!sorted.length) return null;
  var i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

function finitePairs(xs, ys) {
  var pts = [];
  for (var i = 0; i < xs.length; i++) {
    if (xs[i] != null && isFinite(xs[i]) && ys[i] != null && isFinite(ys[i])) pts.push([xs[i], ys[i]]);
  }
  return pts;
}

function binnedTrend(pts, maxBins) {
  if (pts.length < TREND_MIN_N) return null;
  pts = pts.slice().sort(function(a, b){ return a[0] - b[0]; });
  // At least ~12 instances per bin: thinner bins make the median jump around, which
  // reads as structure though it is only sampling noise.
  var nBins = Math.max(3, Math.min(maxBins || 8, Math.floor(pts.length / 12)));
  var out = { x:[], med:[], lo:[], hi:[], n:[] }, per = pts.length / nBins;
  for (var b = 0; b < nBins; b++) {
    var i0 = Math.round(b * per), i1 = (b === nBins - 1) ? pts.length : Math.round((b + 1) * per);
    if (i1 <= i0) continue;
    var chunk = pts.slice(i0, i1);
    var cy = chunk.map(function(q){ return q[1]; }).sort(function(a, b){ return a - b; });
    out.x.push(quantileSorted(chunk.map(function(q){ return q[0]; }), 0.5));
    out.med.push(quantileSorted(cy, 0.5));
    out.lo.push(quantileSorted(cy, 0.25));
    out.hi.push(quantileSorted(cy, 0.75));
    out.n.push(chunk.length);
  }
  return out.x.length >= 3 ? out : null;
}

// Share of a population at or below each value, as a step curve. This has no minimum
// sample size: one observation is one step, ten thousand is a smooth curve, and the
// reading ("x% are within d") is the same either way — which is why the pair matrix
// uses it instead of anything that needs enough points to bin.
function ecdfCurve(vals, maxVertices) {
  var v = vals.slice().sort(function(a, b){ return a - b; });
  var n = v.length;
  if (!n) return null;
  // Quantiles stand in for every point: a monotone curve needs no more vertices than
  // this to look identical, and 8k instances per series would otherwise be 8k nodes.
  var K = Math.min(n, maxVertices || 160);
  var xs = [0], ys = [0];
  for (var i = 1; i <= K; i++) {
    var idx = Math.ceil(i * n / K) - 1;
    xs.push(v[idx]); ys.push((idx + 1) / n * 100);
  }
  return { x:xs, y:ys, n:n, median:quantileSorted(v, 0.5) };
}

// Least-squares line — the honest trend when there are too few instances to bin. Not
// a substitute for the binned median: it assumes the relationship is a straight one.
function fitLine(pts) {
  var n = pts.length;
  if (n < 3) return null;
  var sx = 0, sy = 0, sxx = 0, sxy = 0, lo = Infinity, hi = -Infinity;
  pts.forEach(function(q) {
    sx += q[0]; sy += q[1]; sxx += q[0] * q[0]; sxy += q[0] * q[1];
    if (q[0] < lo) lo = q[0];
    if (q[0] > hi) hi = q[0];
  });
  var den = n * sxx - sx * sx;
  if (!den || hi <= lo) return null;
  var slope = (n * sxy - sx * sy) / den;
  return { x:[lo, hi], y:[(sy - slope * sx) / n + slope * lo, (sy - slope * sx) / n + slope * hi] };
}
