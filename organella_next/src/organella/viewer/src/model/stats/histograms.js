// Each instance ships its own histogram over its own [min,max] range, as a JSON array of
// fixed-width bins. Reading a population off them means re-binning onto one shared grid.

// The common [min,max] span of a target's per-voxel histograms across rows — pass it to
// aggregateHistograms so every group is binned on the SAME grid (otherwise per-group grids
// produce misaligned curves with gaps when overlaid).
function histSpan(rows, target) {
  var col = distColumnsFor(target);
  var gmin = Infinity, gmax = -Infinity, widths = [];
  rows.forEach(function(r) {
    if (r[col.hist] == null || r[col.histMin] == null || r[col.histMax] == null) return;
    if (!isFinite(r[col.histMin]) || !isFinite(r[col.histMax])) return;
    if (r[col.histMin] < gmin) gmin = r[col.histMin];
    if (r[col.histMax] > gmax) gmax = r[col.histMax];
    // Bin width this instance's stored histogram was built at — the real resolution
    // limit of everything downstream.
    var n = 0; try { n = JSON.parse(r[col.hist]).length; } catch(e){ n = 0; }
    if (n > 0 && r[col.histMax] > r[col.histMin]) widths.push((r[col.histMax] - r[col.histMin]) / n);
  });
  if (!isFinite(gmin) || gmax <= gmin) return null;
  widths.sort(function(a,b){ return a-b; });
  return { min: gmin, max: gmax,
           srcWidth: widths.length ? widths[Math.floor(widths.length/2)] : null };
}

// Aggregate per-instance histograms onto a grid. Pass `span` ({min,max}) to force a shared
// grid across groups; otherwise it is derived from `rows`.
function aggregateHistograms(rows, target, numBins, span) {
  numBins = numBins || 30;
  var col = distColumnsFor(target);
  var valid = rows.filter(function(r) {
    return r[col.hist] != null && r[col.histMin] != null && r[col.histMax] != null
      && isFinite(r[col.histMin]) && isFinite(r[col.histMax]);
  });
  if (!valid.length) return null;
  var gmin = span ? span.min : valid.reduce(function(m, r){ return Math.min(m, r[col.histMin]); }, Infinity);
  var gmax = span ? span.max : valid.reduce(function(m, r){ return Math.max(m, r[col.histMax]); }, -Infinity);
  if (!isFinite(gmin) || gmax <= gmin) return null;

  // Never ask for a finer grid than the stored histograms carry. Each instance ships
  // its bins over ITS OWN [min,max], so over the wider shared span those bins are
  // coarser than they look: requesting 30 bins from 20-bin sources leaves output bins
  // that no source bin can reach, and the curve combs into spikes-and-zeros that read
  // as real oscillation. Cap at the median source bin width, on the shared grid so
  // every facet stays aligned.
  var srcW = span && span.srcWidth ? span.srcWidth : null;
  if (!srcW) {
    var ws = [];
    valid.forEach(function(r) {
      var n = 0; try { n = JSON.parse(r[col.hist]).length; } catch(e){ n = 0; }
      if (n > 0 && r[col.histMax] > r[col.histMin]) ws.push((r[col.histMax] - r[col.histMin]) / n);
    });
    ws.sort(function(a,b){ return a-b; });
    srcW = ws.length ? ws[Math.floor(ws.length/2)] : null;
  }
  if (srcW > 0) numBins = Math.max(4, Math.min(numBins, Math.round((gmax - gmin) / srcW)));

  var bw = (gmax - gmin) / numBins;
  var counts = new Array(numBins).fill(0);
  valid.forEach(function(r) {
    var hist; try { hist = JSON.parse(r[col.hist]); } catch(e){ return; }
    if (!Array.isArray(hist)) return;
    var hmin = r[col.histMin], hmax = r[col.histMax], n = hist.length;
    if (n === 0 || hmax <= hmin) return;
    var sbw = (hmax - hmin) / n;
    hist.forEach(function(c, i) {
      if (!c) return;
      // Spread each source bin across the output bins it overlaps, proportional to the
      // overlap, instead of dumping it all into the bin holding its centre. Total count
      // is preserved either way; only this way is the shape preserved too.
      var lo = hmin + i * sbw, hi = lo + sbw;
      var b0 = Math.max(0, Math.floor((lo - gmin) / bw));
      var b1 = Math.min(numBins - 1, Math.floor((hi - gmin) / bw));
      for (var b = b0; b <= b1; b++) {
        var blo = gmin + b * bw, bhi = blo + bw;
        var ov = Math.min(hi, bhi) - Math.max(lo, blo);
        if (ov > 0) counts[b] += c * ov / sbw;
      }
    });
  });
  var total = counts.reduce(function(a, b){ return a + b; }, 0);
  return {
    centers: counts.map(function(_, i){ return gmin + (i + 0.5) * bw; }),
    counts:  counts,
    density: counts.map(function(c){ return total > 0 ? c / total / bw : 0; }),
    width: bw, min: gmin, max: gmax
  };
}

/**
 * One bin grid for every facet of a panel.
 *
 * Shared, because per-facet grids put the same value in differently placed bins and the
 * shapes stop being comparable - the same reason the per-voxel panels bin on a shared span.
 * Counts are turned into a percentage of the facet's own total, so a condition with three
 * times the instances does not simply draw three times as tall.
 */
function histogramOf(series, binTarget) {
  var span = seriesSpan(series);
  if (!(span.max > span.min)) return null;
  var total = series.reduce(function(n, f){ return n + f.values.length; }, 0);
  // Square root of n, capped: enough bins to show a shape, few enough that each holds
  // something. Below five the shape is the binning, above thirty it is the noise.
  var bins = Math.max(5, Math.min(binTarget || 24, Math.ceil(Math.sqrt(total))));
  var width = (span.max - span.min) / bins;
  var centers = [], edges = [];
  for (var i = 0; i < bins; i++) centers.push(span.min + (i + 0.5) * width);
  for (var e = 0; e <= bins; e++) edges.push(span.min + e * width);
  return {
    min: span.min, max: span.max, width: width, bins: bins,
    centers: centers, edges: edges,
    of: function(values) {
      var counts = new Array(bins).fill(0);
      values.forEach(function(v) {
        var at = Math.min(bins - 1, Math.floor((v - span.min) / width));
        if (at >= 0) counts[at]++;
      });
      var n = values.length || 1;
      var percent = counts.map(function(c){ return 100 * c / n; });
      // The outline itself, ready to draw: bin *edges* with each bin's height held across
      // it, which with an hv step traces the bars. Plotted against centres instead, every
      // bar sat half a bin to the left of the data in it. Closed at zero on both sides, so
      // a histogram reads as a whole shape and not a fragment of one.
      return {
        counts: counts, percent: percent,
        x: [edges[0]].concat(edges, [edges[bins]]),
        y: [0].concat(percent, [percent[bins - 1], 0])
      };
    }
  };
}

function seriesSpan(series) {
  var min = Infinity, max = -Infinity;
  series.forEach(function(f) {
    f.values.forEach(function(v) {
      if (v < min) min = v;
      if (v > max) max = v;
    });
  });
  return { min: min, max: max };
}
