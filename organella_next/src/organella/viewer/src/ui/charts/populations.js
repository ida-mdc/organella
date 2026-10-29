//
// Every "how big / how far" panel in the report goes through here, whether it is about
// instances or about groups of them, so the two read the same and there is one place that
// knows what a facet looks like.
//
// Two styles, chosen once for the whole report:
//
//   distribution  a box per facet - where the middle is, and how spread out
//   share         a cumulative step curve per facet - what share is within any value
//
// They answer the same question from opposite ends, and which is easier to read depends on
// the structure: a box is unbeatable for "are these bigger than those", and a curve is the
// only one that still works when a facet holds two instances or ten thousand.

/**
 * Every chart through one door, so they read as one report: titles on the left over the plot
 * and not floating in the middle, one type size, one ink. What each chart *says* stays
 * with the chart; only how it is dressed is decided here.
 */
function drawPlot(el, traces, layout, config) {
  layout = layout || {};
  if (layout.title && layout.title.text) {
    layout.title = Object.assign({ x: 0, xref: 'container', xanchor: 'left', y: 0.98,
      yref: 'container', yanchor: 'top', font: { size: 14, color: CS.ink } }, layout.title);
  }
  layout.font = Object.assign({ size: 11, color: CS.ink2 }, layout.font);
  layout.paper_bgcolor = layout.paper_bgcolor || 'rgba(0,0,0,0)';
  return Plotly.newPlot(el, traces, layout, config);
}

/**
 * The box of one group, as traces.
 *
 * A group of tens of thousands of instances has thousands of outliers, and each one is a node
 * in the page: a screenful of such charts is tens of thousands of nodes, which is what makes
 * scrolling and redrawing slow. Past a few hundred values the quartiles and the whiskers are
 * worked out here, as Plotly does, and the box is drawn from them; only a thin, evenly spread
 * sample of the outliers - always the two extremes - is drawn as points.
 */
var BOX_EXACT_UP_TO = 400, BOX_OUTLIERS_SHOWN = 120;

function boxTraces(f) {
  var plain = { type:'box', name:f.label, y:f.values, boxpoints:'suspectedoutliers',
    marker:{ color:f.color }, line:{ color:f.color }, showlegend:false,
    hovertemplate:'%{y:.3g}<extra>' + f.name + '</extra>' };
  if (f.values.length <= BOX_EXACT_UP_TO) return [plain];
  var v = f.values.filter(function(x){ return x != null && isFinite(x); }).sort(function(a, b){ return a - b; });
  if (v.length <= BOX_EXACT_UP_TO) return [plain];
  var q1 = quantileSorted(v, 0.25), q3 = quantileSorted(v, 0.75), reach = 1.5 * (q3 - q1);
  var low = v[0], high = v[v.length - 1], k;
  for (k = 0; k < v.length && v[k] < q1 - reach; k++);
  low = v[k];
  for (k = v.length - 1; k >= 0 && v[k] > q3 + reach; k--);
  high = v[k];
  var outliers = v.filter(function(x){ return x < low || x > high; });
  if (outliers.length > BOX_OUTLIERS_SHOWN) {
    var step = (outliers.length - 1) / (BOX_OUTLIERS_SHOWN - 1);
    outliers = Array.from({ length: BOX_OUTLIERS_SHOWN }, function(_, i){ return outliers[Math.round(i * step)]; });
  }
  var box = { type:'box', name:f.label, x:[f.label], q1:[q1], median:[quantileSorted(v, 0.5)], q3:[q3],
    lowerfence:[low], upperfence:[high], marker:{ color:f.color }, line:{ color:f.color }, showlegend:false,
    hovertemplate:'%{y:.3g}<extra>' + f.name + '</extra>' };
  var traces = [box];
  if (outliers.length) {
    traces.push({ type:'scatter', mode:'markers', x:outliers.map(function(){ return f.label; }), y:outliers,
      marker:{ color:f.color, size:5 }, showlegend:false, hoverinfo:'skip' });
  }
  return traces;
}

var PANEL_WIDTH = 262;

var PANEL_HEIGHT = 236;

/**
 * A panel title that says what it is about as well as what it measures.
 *
 * "volume" over a chart is only readable if you already know which structure is selected
 * and that the selector applies to this panel; "er volume" is readable on its own, which
 * matters most when a panel is screenshotted into a slide away from the rest of the page.
 */
function panelTitle(subject, metric) {
  return subject ? subject + ' ' + metric : metric;
}

/** One value per facet is a bar chart: a box over a single observation is a line. */
function isSingleValued(series) {
  return series.every(function(f){ return f.values.length <= 1; });
}

/**
 * A y range for a panel where every value is the same.
 *
 * Left to itself Plotly pads a constant series by ±1, so a set of distances that are all
 * zero - every group overlapping the nucleus, say - is drawn on an axis running from -1 to
 * 1 µm, with a negative distance on it. An explicit range says "all of them are here".
 */
function flatRange(span, opts) {
  if (span.max !== span.min) return null;
  if (opts.range) return opts.range.slice();
  var value = span.max;
  if (value === 0) return [0, 1];
  return value > 0 ? [0, value * 1.6] : [value * 1.6, 0];
}

/**
 * Draw one population as a panel, and say whether the groups differ.
 *
 * `series` is one entry per facet: `{ name, label, color, values }`. `name` is the full
 * facet name (it goes in the hover and the brackets), `label` the short one for the axis.
 */
function distributionPanel(host, opts) {
  var series = opts.series.filter(function(f){ return f.values.length; });
  if (!series.length) return null;
  var el = document.createElement('div');
  el.className = 'chart-cell';
  el.style.width = (opts.width || PANEL_WIDTH) + 'px';
  host.appendChild(el);
  if (opts.explain) explainButton(el, opts.explain);
  if (opts.heading) {
    var heading = document.createElement('div');
    heading.style.cssText = 'font-size:.72rem;font-weight:600;color:' + CS.ink
      + ';margin-bottom:2px;display:flex;align-items:center;gap:5px';
    heading.innerHTML = opts.heading;
    el.appendChild(heading);
  }
  var plotEl = document.createElement('div');
  el.appendChild(plotEl);

  var comparisons = opts.noSignificance ? [] : pairwiseSignificance(series);
  var bars = isSingleValued(series);
  var bracketed = false, dotsDrawn = false;
  var traces, layout;
  // Which style actually got drawn, not which was asked for: a histogram of one value per
  // facet falls back to boxes, and the chance line goes on a different axis for each.
  var drawn = bars ? 'bar' : (plotStyle === 'histogram' ? 'histogram' : 'box');
  // The reference each facet is read against, where the caller has one.
  var chanceOf = function(f) {
    var at = opts.chanceBy ? opts.chanceBy[f.name] : null;
    return at != null && isFinite(at) ? at : null;
  };
  var chances = series.map(chanceOf).filter(function(v){ return v != null; });
  // A reference outside the data sits off an autoranged panel, and a line nobody can see is
  // worse than none, so wherever one is drawn the axis is told to hold it. Both sides
  // matter: above the data says the population is closer, or leans harder, than the
  // reference, below it says the opposite, and a panel of nearly equal values would hide
  // the second. Computed here, once, and not per branch because a single value per facet is
  // drawn as a bar and needs it just as much as a box does.
  var dataSpan = seriesSpan(series);
  var beyond = chances.filter(function(v) {
    return v < dataSpan.min || v > dataSpan.max;
  });
  var heldRange = function() {
    if (!beyond.length) return null;
    var low = Math.min.apply(null, [dataSpan.min].concat(beyond));
    var high = Math.max.apply(null, [dataSpan.max].concat(beyond));
    return [Math.min(0, low), high > 0 ? high * 1.08 : 0];
  };

  if (bars) {
    traces = [{ type:'bar', width: series.map(function(){ return 0.4; }),
      x: series.map(function(f){ return f.label; }),
      customdata: series.map(function(f){ return f.name; }),
      y: series.map(function(f){ return f.values[0]; }),
      marker:{ color: series.map(function(f){ return f.color; }) },
      showlegend:false,
      hovertemplate:'%{customdata}: %{y:.3g}<extra></extra>' }];
    layout = { margin:{l:54,r:6,t:28,b:opts.showFacetTicks ? 52 : 22}, height:opts.height || PANEL_HEIGHT,
      title:{ text: opts.title }, showlegend:false,
      xaxis: opts.showFacetTicks ? { type:'category', automargin:true } : { type:'category', showticklabels:false, ticks:'' },
      yaxis:{ title: opts.unit || null, rangemode:'tozero',
              range: (opts.range ? opts.range.slice() : null) || heldRange() || undefined } };
  } else if (plotStyle === 'histogram') {
    // Every facet binned on one grid, as a share of its own population, so the shapes are
    // comparable however many instances each facet holds.
    var grid = histogramOf(series, 24);
    if (!grid) {
      // One value across every facet: there is no shape to bin, so say the number.
      grid = null;
    }
    traces = grid ? series.map(function(f) {
      var binned = grid.of(f.values);
      return { type:'scatter', mode:'lines', name:f.label + ' (n=' + f.values.length + ')',
        x: binned.x, y: binned.y,
        line:{ color:f.color, width: series.length > 4 ? 1.5 : 2, shape:'hv' },
        fill: series.length === 1 ? 'tozeroy' : undefined,
        fillcolor: series.length === 1 ? withAlpha(f.color, 0.22) : undefined,
        showlegend:false,
        hovertemplate:'%{x:.3g}: %{y:.1f}% of ' + f.name + '<extra></extra>' };
    }) : [];
    var histSpanOf = seriesSpan(series);
    layout = { margin:{l:54,r:6,t:28,b:44}, height:opts.height || PANEL_HEIGHT,
      title:{ text: opts.title }, showlegend:false,
      xaxis:{ title: opts.unit || null, rangemode:'tozero',
              range: flatRange(histSpanOf, opts)
                || (opts.range ? opts.range.slice() : undefined) },
      yaxis:{ title:'% of ' + (opts.noun ? pluralise(opts.noun) : 'them'), rangemode:'tozero',
              ticksuffix:'%' } };
    if (!traces.length) {
      // Nothing to bin: fall back to the boxes, which can always draw a single value.
      traces = series.map(function(f) {
        return { type:'box', name:f.label, y:f.values, marker:{ color:f.color },
                 line:{ color:f.color }, showlegend:false };
      });
      layout.xaxis = { type:'category', showticklabels:false, ticks:'' };
      layout.yaxis = { title: opts.unit || null,
                       range: flatRange(histSpanOf, opts) || undefined };
      drawn = 'box';
    }
  } else {
    traces = [];
    series.forEach(function(f){ boxTraces(f).forEach(function(t){ traces.push(t); }); });
    // The cells themselves, over the boxes of their instances. The box says where thousands
    // of instances lie; the test compares one number per cell, and without those numbers on
    // the picture a reader judges a difference by the wrong thing. On an axis of their own
    // so they can be spread a little round the box, clear of its centre line.
    series.forEach(function(f, at) {
      if (!f.units || f.units.length >= f.values.length || isUngrouped(series)) return;
      dotsDrawn = true;
      traces.push({ type:'scatter', mode:'markers', xaxis:'x2', showlegend:false,
        x: f.units.map(function(_, k) {
          return at + (f.units.length > 1 ? (k / (f.units.length - 1) - 0.5) * 0.4 : 0);
        }),
        y: f.units, text: f.unitIds || f.units.map(function(_, k){ return 'cell ' + (k + 1); }),
        marker:{ symbol:'diamond', size:9, color:f.color, line:{ color:CS.ink, width:1.2 } },
        hovertemplate:'%{text}: %{y:.3g}<extra>median of one ' + noun() + '</extra>' });
    });
    var span = beyond.length
      ? { min: Math.min.apply(null, [dataSpan.min].concat(beyond)),
          max: Math.max.apply(null, [dataSpan.max].concat(beyond)) }
      : dataSpan;
    // The axis is a category axis by decree: Plotly reads group names such as "6" and "7" as numbers, puts
    // the boxes at x = 6 and 7 and leaves the diamonds, on [-0.5, n - 0.5], far to their left.
    layout = { margin:{l:54,r:6,t:28,b:opts.showFacetTicks ? 52 : 22}, height:opts.height || PANEL_HEIGHT,
      title:{ text: opts.title }, showlegend:false,
      xaxis: opts.showFacetTicks ? { type:'category', automargin:true } : { type:'category', showticklabels:false, ticks:'' },
      yaxis:{ title: opts.unit || null,
              range: flatRange(span, opts) || heldRange()
                || (opts.range ? opts.range.slice() : undefined) } };
    if (dotsDrawn) {
      layout.xaxis2 = { overlaying:'x', range:[-0.5, series.length - 0.5], visible:false,
                        fixedrange:true };
    }
    // Brackets belong on boxes, where there is a top to hang them above, and only while
    // they fit. A bracket sets the range itself, to make room for the rungs, so a panel
    // given an explicit range keeps it and says the comparison in text instead.
    // Not the comparisons that cannot reach 0.05 with this many cells: a bracket says "tested, and this
    // is what came out", and there is nothing to say.
    var forBrackets = comparisons.filter(function(c){ return !(c.test && c.test.floor); });
    if (!layout.yaxis.range && forBrackets.length && bracketRungs(forBrackets) <= MAX_BRACKETS) {
      bracketLayout(layout, forBrackets, span);
      bracketed = true;
    }
  }

  // The chance distribution's median, per facet: the distance half the object sits within
  // of that structure. Dotted, and on whichever axis carries the distance - across the
  // panel over each box, along it where a histogram puts the distance on x. A box sitting
  // below its line is closer to the structure than the object's shape alone puts it.
  if (chances.length) {
    layout.shapes = layout.shapes || [];
    series.forEach(function(f, i) {
      var at = chanceOf(f);
      if (at == null) return;
      layout.shapes.push(drawn === 'histogram'
        ? { type:'line', xref:'x', yref:'paper', x0:at, x1:at, y0:0, y1:1,
            line:{ color:f.color, width:1, dash:'dot' } }
        : { type:'line', xref:'x', yref:'y', x0:i - 0.42, x1:i + 0.42, y0:at, y1:at,
            line:{ color:CS.ink3, width:1.2, dash:'dot' } });
    });
  }

  // A line the panel is read against - 50% for the baseline scores - drawn across the
  // plot before the brackets, which append to the same list.
  if (opts.reference != null) {
    layout.shapes = layout.shapes || [];
    layout.shapes.push({ type:'line', xref:'paper', yref:'y', x0:0, x1:1,
                         y0:opts.reference, y1:opts.reference,
                         line:{ color:CS.ink3, width:1, dash:'dot' } });
  }
  drawPlot(plotEl, traces, layout, {responsive:true, displayModeBar:false});
  // A diamond is a cell: press it and the viewer shows that cell.
  if (dotsDrawn && plotEl.on) {
    plotEl.on('plotly_click', function(event) {
      var point = event.points && event.points[0];
      if (point && point.data && point.data.xaxis === 'x2' && typeof point.text === 'string') {
        showCellInViewer(point.text);
      }
    });
    plotEl.classList.add('has-cells');
  }
  // How many of what each box is made of, on the chart and not in a section's prose: a
  // reader looking at one panel should not have to scroll to learn what it is a picture of.
  if (!opts.noSizeLine) el.appendChild(sizeLine(series, opts.noun, dotsDrawn));
  var from = sourceFold(opts, series, comparisons);
  if (from) el.appendChild(from);
  if (opts.footnote) {
    var footnote = document.createElement('div');
    footnote.className = 'chart-note';
    footnote.innerHTML = opts.footnote;
    el.appendChild(footnote);
  }
  // What the measurement is, beside the chart of it: a column name is not an explanation,
  // and "sphericity 1.03" is a question until you know what the ratio is between.
  if (opts.help) {
    var help = document.createElement('div');
    help.className = 'metric-help';
    help.innerHTML = opts.help;
    el.appendChild(help);
  }
  // Where a bracket could not be drawn - a histogram's facets share one x axis, so there
  // are no positions to hang one between, and bars of one value each have nothing to test -
  // the same comparison goes underneath as text instead of being dropped.
  if (!bracketed) significanceNote(el, comparisons, opts.unit);
  return el;
}

/**
 * "Where this comes from", folded under a chart: which rows and which column it is made of, what
 * one box and one diamond are, and what was compared. A reader who doubts a picture should be able to
 * find what it is a picture of without leaving the card.
 */
function sourceFold(opts, series, comparisons) {
  var spec = opts.explain;
  var cols = spec ? (spec.metrics || (spec.metric ? [spec.metric] : [])) : [];
  var perCell = opts.noun === noun(), perGroup = opts.noun === 'group';
  if (!cols.length && !perGroup) return null;
  var total = series.reduce(function(n, f){ return n + f.values.length; }, 0);
  var lines = [];
  if (cols.length) {
    lines.push('<b>What:</b> the report\u2019s <code>' + cols.map(escHtml).join('</code>, <code>')
      + '</code>' + (perCell ? ', one value per ' + escHtml(noun()) + '.'
        : ' of every ' + escHtml(opts.noun ? singularOf(opts.noun) : 'instance') + ' - '
          + total.toLocaleString() + ' rows, one value each.'));
  } else {
    lines.push('<b>What:</b> one value per group of touching instances at the current gap, taken from the'
      + ' contact rows (' + total.toLocaleString() + ' groups).');
  }
  var withUnits = !isUngrouped(series) && series.some(function(f){ return f.units && f.units.length < f.values.length; });
  lines.push('<b>Drawn:</b> ' + (withUnits
    ? 'the box is every value of a group; each diamond is the median of one ' + escHtml(noun()) + '.'
    : 'one box per ' + (isUngrouped(series) ? escHtml(noun()) : 'group') + ', over the values above.'));
  var tested = comparisons.filter(function(c){ return c.test; })[0];
  if (tested) {
    var t = tested.test;
    lines.push('<b>Compared:</b> Mann\u2013Whitney U, two-sided (' + t.method + '), on one median per '
      + escHtml(noun()) + '.');
  }
  var fold = document.createElement('details');
  fold.className = 'cc-source';
  fold.innerHTML = '<summary>Where this comes from</summary><p>' + lines.join('<br>') + '</p>';
  return fold;
}

/** "granules" for a structure's plural that is already one, "cell" for "cells". */
function singularOf(word) {
  return /ss$/i.test(word) ? word : String(word).replace(/s$/i, '');
}

/**
 * "● high  40,125 granules · 4 cells" under a chart: the size of each group, in the thing the
 * box is made of and in the cells those come from, and - where cells are drawn over the boxes -
 * what the diamond is. Said once per chart so it travels with a screenshot.
 */
function sizeLine(series, word, dotsDrawn) {
  var line = document.createElement('div');
  line.className = 'cc-foot';
  var of = word ? pluralise(word) : 'values';
  var parts = series.map(function(f) {
    var cells = f.units && f.units.length < f.values.length && !isUngrouped(series)
      ? ' \u00b7 ' + f.units.length + ' ' + noun(f.units.length !== 1) : '';
    return '<span class="cc-n"><i style="background:' + f.color + '"></i>' + escHtml(f.label)
      + ' <b>' + f.values.length.toLocaleString() + '</b> ' + escHtml(of) + cells + '</span>';
  });
  line.innerHTML = parts.join('')
    + (dotsDrawn ? '<span class="cc-key">\u25c6 median per ' + escHtml(noun()) + '</span>' : '');
  return line;
}
