// More rungs than this on one panel and the brackets are taller than the data; past it the
// same comparisons go under the panel as text, which stays readable at any number.
var MAX_BRACKETS = 6;

/** How many rows of annotation a panel needs above its data: a bracket each, and room for the untested lines. */
function bracketRungs(comparisons) {
  var tested = comparisons.filter(function(c){ return c.test; }).length;
  return tested + Math.ceil(untestedLines(comparisons).length * 0.75);
}

/**
 * Draw the brackets onto a Plotly layout, above everything already in the panel.
 *
 * Stacked, one rung per tested comparison and, last, one line for those that could not be, and
 * the y range is extended to make room: a bracket drawn at the top of the data lands on the
 * whiskers it is about.
 */
function bracketLayout(layout, comparisons, span) {
  if (!comparisons.length || !(span.max > -Infinity)) return layout;
  var range = (span.max - span.min) || Math.abs(span.max) || 1;
  var step = range * 0.13;
  var base = span.max + step * 0.55;
  layout.shapes = layout.shapes || [];
  layout.annotations = layout.annotations || [];
  var tested = comparisons.filter(function(c){ return c.test; });
  tested.forEach(function(c, rung) {
    var y = base + step * rung;
    var tick = step * 0.16;
    var t = c.test;
    layout.shapes.push({
      type: 'path', xref: 'x', yref: 'y',
      path: 'M ' + c.a + ' ' + (y - tick) + ' L ' + c.a + ' ' + y
        + ' L ' + c.b + ' ' + y + ' L ' + c.b + ' ' + (y - tick),
      line: { color: CS.ink3, width: 1 }
    });
    layout.annotations.push({
      xref: 'x', yref: 'y', x: (c.a + c.b) / 2, y: y, yanchor: 'bottom',
      showarrow: false,
      // The stars and the p, for every comparison that was made, however many cells it had:
      // "ns" alone reads as "no difference" when it can as well be "too few cells to say".
      text: significanceLabel(t),
      hovertext: c.aName + ' vs ' + c.bName + ': ' + formatP(t.p)
        + ' (Mann-Whitney U = ' + t.u + ', n = ' + t.n1 + ' vs ' + t.n2
        + (c.perObject ? ' ' + noun(true) + ', each one median' : '') + ')'
        + (t.floor ? '. The lowest p possible with this many is '
            + formatP(t.pMin).replace(/^p (= )?/, '') + '.' : '')
        + (t.delta != null ? ' Cliff\u2019s delta ' + t.delta.toFixed(2) + '.' : ''),
      font: { family: CS.sans, size: 10.5, color: CS.ink2 }
    });
  });
  var rest = untestedLines(comparisons);
  if (rest.length) {
    var topX = comparisons.reduce(function(m, c){ return Math.max(m, c.b); }, 0);
    layout.annotations.push({
      xref: 'x', yref: 'y', x: topX / 2, y: base + step * tested.length, yanchor: 'bottom',
      showarrow: false, text: rest.join('<br>'),
      hovertext: 'A test needs at least two ' + noun(true) + ' on each side.',
      font: { family: CS.sans, size: 8.5, color: CS.ink3 }
    });
  }
  layout.yaxis = layout.yaxis || {};
  // A little room under the lowest value, even at zero: a diamond or box edge sitting on the
  // axis floor is drawn half off the plot.
  var low = span.min - range * 0.08;
  layout.yaxis.range = [low, base + step * bracketRungs(comparisons)];
  return layout;
}

/**
 * The pairwise results as a line of text, for a chart a bracket does not suit.
 *
 * No "Mann-Whitney U, two-sided" heading on each one: with a dozen panels in a block it was
 * repeated a dozen times, and the section says it once already.
 */
function significanceNote(container, comparisons, unit) {
  if (!comparisons.length) return;
  var note = document.createElement('div');
  note.className = 'sig-note';
  comparisons = comparisons.filter(function(c){ return !(c.test && c.test.floor); });
  if (!comparisons.length) return;
  note.innerHTML = comparisons.map(function(c) {
    // The short labels, not the full names: object ids are long shared paths, and set
    // inline they broke across the stars and the p-value until the three parts read as
    // unrelated fragments. The full names are on the row's title.
    var a = escHtml(c.aLabel || c.aName), b = escHtml(c.bLabel || c.bName);
    var t = c.test;
    if (!t) return '';
    var signed = function(v){ return (v > 0 ? '+' : v < 0 ? '\u2212' : '') + fmtValue(Math.abs(v)); };
    return '<div class="sig-note-row" title="' + escHtml(c.aName + ' vs ' + c.bName) + '">'
      + '<span class="sig-note-stars">' + significanceStars(t.p) + '</span> '
      + a + ' vs ' + b + ' <span class="sig-note-p">(' + formatP(t.p) + '; ' + t.n1 + ' vs ' + t.n2
      + (c.perObject ? ' ' + noun(true) : '') + ')</span>'
      + (t.delta != null
          ? '<div class="sig-note-eff">' + a + ' is above ' + b + ' in '
            + Math.round(100 * (1 + t.delta) / 2) + '% of ' + (c.perObject ? noun() + ' ' : '')
            + 'pairs \u00b7 shift ' + signed(t.shift) + (unit ? ' ' + escHtml(unit) : '') + '</div>'
          : '')
      + '</div>';
  }).join('');
  // The pairs that could not be tested are one line, not a row each: a group of one cell
  // against ten others would otherwise be ten rows saying the same thing.
  var untested = untestedLines(comparisons);
  if (untested.length) {
    note.innerHTML += '<div class="sig-note-row"><span class="sig-note-stars">n/a</span> '
      + escHtml(untested.join(' ')) + '</div>';
  }
  container.appendChild(note);
}

// Traces for one series in one panel: median line, optional IQR ribbon. `opts` carries
// the subplot axes (matrix panels) and the axis labels used in the tooltip.
function trendTraces(t, name, color, opts) {
  var o = opts || {}, tr = [];
  if (o.band) {
    tr.push({ type:'scatter', mode:'lines', showlegend:false, hoverinfo:'skip',
      x: t.x.concat(t.x.slice().reverse()), y: t.hi.concat(t.lo.slice().reverse()),
      fill:'toself', fillcolor: withAlpha(color, 0.16), line:{width:0},
      xaxis:o.xaxis, yaxis:o.yaxis });
  }
  tr.push({ type:'scatter', mode:'lines+markers', name:name, showlegend:!!o.showlegend,
    x:t.x, y:t.med, customdata:t.n,
    line:{color:color, width:o.width || 2}, marker:{color:color, size:o.width === 1.5 ? 3 : 4},
    xaxis:o.xaxis, yaxis:o.yaxis,
    hovertemplate:(o.xlabel || 'x') + ': %{x:.3g}<br>median ' + (o.ylabel || 'y')
      + ': %{y:.3g}<br>%{customdata} in bin<extra>' + name + '</extra>' });
  return tr;
}

function fitTrace(fit, color, opts) {
  var o = opts || {};
  return { type:'scatter', mode:'lines', showlegend:false, hoverinfo:'skip',
    x:fit.x, y:fit.y, line:{ color:color, width:1.5, dash:'dot' },
    xaxis:o.xaxis, yaxis:o.yaxis,
    name:o.pooled ? 'trend (all instances)' : 'trend' };
}

// Fallback for a series with too few instances to bin: the instances themselves.
// Honest, and at this count there is nothing to overplot.
function pointTrace(pts, name, color, opts) {
  var o = opts || {};
  return { type:'scatter', mode:'markers', name:name, showlegend:!!o.showlegend,
    x:pts.map(function(q){ return q[0]; }), y:pts.map(function(q){ return q[1]; }),
    marker:{color:color, size:6, opacity:0.75, line:{width:0}},
    xaxis:o.xaxis, yaxis:o.yaxis,
    hovertemplate:(o.xlabel || 'x') + ': %{x:.3g}<br>' + (o.ylabel || 'y')
      + ': %{y:.3g}<extra>' + name + '</extra>' };
}
