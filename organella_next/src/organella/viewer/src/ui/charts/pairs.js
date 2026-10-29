var _pairChoice = {};

/**
 * The order the pairs are offered in: the object's own bounding mask first (the canonical
 * "how deep inside" axis, and the report says which mask that is, so no regex
 * has to guess), then the rest alphabetically, with the same-structure distance last - it
 * answers a different question, how clustered a structure is with itself.
 */
function orderDistDims(cols) {
  var maskName = (OBJECTS[0] || {}).object_mask_name;
  var rank = function(c) {
    if (c === 'distance_to_closest_same_type_um') return 2;
    if (maskName && c.indexOf('distance_to_' + maskName) === 0) return 0;
    return 1;
  };
  return cols.slice().sort(function(a, b) {
    return rank(a) - rank(b) || distColLabel(a).localeCompare(distColLabel(b));
  });
}

function dimLabel(col) {
  return distColLabel(col).replace(/\s*\(mean\)$/, '');
}

/** Every unordered pair of the given columns, in the order they were given. */
function dimPairs(dims) {
  var out = [];
  for (var i = 0; i < dims.length; i++) {
    for (var j = i + 1; j < dims.length; j++) out.push([dims[i], dims[j]]);
  }
  return out;
}

/**
 * One panel per structure pair, side by side.
 *
 * Each panel asks the question directly: for every member take the LARGER of its two
 * distances - the distance at which it is close to BOTH structures - and plot the share at
 * or below each value. A curve that climbs early and steeply means most of them sit against
 * both. Every panel has the same two axes, so they are comparable across the row, and there
 * is no sample size at which the encoding stops working.
 *
 * This used to be a lower-triangle subplot matrix, which is compact and unreadable: the
 * pair a panel was about had to be worked out from its position, and at five structures the
 * panels were 148px wide with no axis of their own.
 */
function pairPanels(host, opts) {
  var dims = opts.dims;
  if (dims.length < 2) return;
  var pairs = dimPairs(dims);
  // What one member is called - "mito", or "group" for the group panels. Not the same
  // thing as noun(), which is what one measured dataset is called.
  var member = opts.noun;

  // A curve needs a few observations to be worth reading. Where a facet holds one or two
  // members (single-copy structures: one ER, one nucleus) the split by facet is what fails,
  // not the encoding - so pool them into one curve and say so, which avoids drawing eight
  // single-step lines.
  var ECDF_MIN_PER_SERIES = 4;
  var thinnest = opts.series.reduce(function(m, f){ return Math.min(m, f.rows.length); }, Infinity);
  var pooling = opts.series.length > 1 && thinnest < ECDF_MIN_PER_SERIES;
  var series = pooling
    ? [{ name:'all ' + (plotGroupBy === 'group' ? 'groups' : noun(true)), label:'all',
         color:CS.ink2,
         rows:opts.series.reduce(function(all, f){ return all.concat(f.rows); }, []) }]
    : opts.series;

  // One legend for the whole row, not one per panel. Plotly's own sat inside each panel and
  // landed on its title, and repeating the same two names across every pair was noise even
  // where it fitted.
  if (series.length > 1) {
    var legend = document.createElement('div');
    legend.className = 'chart-legend';
    legend.innerHTML = series.map(function(f) {
      return '<span class="d-inline-flex align-items-center gap-1">'
        + '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
        + 'background:' + f.color + '"></span>' + escHtml(f.name)
        + ' <span class="text-muted">(n=' + f.rows.length + ')</span></span>';
    }).join('');
    host.appendChild(legend);
  }

  var picker = document.createElement('select');
  host.appendChild(picker);
  var row = document.createElement('div');
  row.className = 'pair-panels';
  host.appendChild(row);

  var reachOf = function(rows, cx, cy) {
    return finitePairs(rows.map(function(r){ return r[cx]; }),
                       rows.map(function(r){ return r[cy]; }))
      .map(function(q){ return Math.max(q[0], q[1]); });
  };

  // One pair at a time: the reader picks the two structures they are asking about.
  var pairLabel = function(pair){ return dimLabel(pair[0]) + ' + ' + dimLabel(pair[1]); };
  var drawPair = function(pair) {
    row.innerHTML = '';
    var xRange = undefined;
    var panel = document.createElement('div');
    panel.className = 'chart-cell';
    panel.style.cssText = 'width:100%;max-width:520px';
    row.appendChild(panel);
    // A pair of distances is for instances of one structure; a group's pair is for groups,
    // which the viewer draws as the groups themselves.
    if (opts.noun === 'group' && opts.entity) explainButton(panel, groupSpec(opts.entity, 'both', [pair[0], pair[1]]));
    else if (opts.entity) explainButton(panel, bothSpec(opts.entity, pair[0], pair[1]));
    var plotEl = document.createElement('div');
    panel.appendChild(plotEl);

    var traces = [];
    series.forEach(function(f) {
      var reach = reachOf(f.rows, pair[0], pair[1]);
      if (!reach.length) return;
      var curve = ecdfCurve(reach);
      traces.push({ type:'scatter', name:f.label + ' (n=' + curve.n + ')',
        x:curve.x, y:curve.y,
        line:{ color:f.color, width: series.length > 4 ? 1.5 : 2, shape:'hv' },
        // Markers only while the steps are countable: they mark where each one sits, and
        // drop away once the curve is dense enough to read on its own.
        mode: curve.n <= 20 ? 'lines+markers' : 'lines',
        marker:{ color:f.color, size:5 }, showlegend:false,
        hovertemplate:'within %{x:.3g} µm of both<br>%{y:.0f}% of ' + pluralise(member)
          + '<extra>' + f.name + '</extra>' });
    });
    if (!traces.length) {
      panel.innerHTML = '<div class="text-muted small">Not measured for this pair.</div>';
      return;
    }
    drawPlot(plotEl, traces, {
      margin:{l:56, r:12, t:34, b:48}, height:300,
      title:{ text: dimLabel(pair[0]) + ' + ' + dimLabel(pair[1]) },
      xaxis:{ title:'within (µm) of both', rangemode:'tozero', range:xRange },
      yaxis:{ title:'% of ' + pluralise(member), range:[0, 103], dtick:25, ticksuffix:'%' },
      showlegend:false
    }, {responsive:true, displayModeBar:false});
  };

  var choiceKey = (opts.noun || '') + '|' + (opts.entity || '');
  var chosenAt = Math.max(0, pairs.map(pairLabel).indexOf(_pairChoice[choiceKey]));
  picker.className = 'form-select form-select-sm pair-picker';
  picker.title = 'Pair of structures';
  picker.innerHTML = pairs.map(function(pair, i) {
    return '<option value="' + i + '"' + (i === chosenAt ? ' selected' : '') + '>' + escHtml(pairLabel(pair)) + '</option>';
  }).join('');
  picker.onchange = function() {
    _pairChoice[choiceKey] = pairLabel(pairs[+picker.value]);
    drawPair(pairs[+picker.value]);
  };
  drawPair(pairs[chosenAt]);

  var notes = [];
  if (pooling) {
    notes.push('Each ' + (plotGroupBy === 'group' ? 'group' : noun()) + ' holds fewer than '
      + ECDF_MIN_PER_SERIES + ' of them, so all ' + series[0].rows.length + ' are pooled '
      + 'into one curve - one step each.');
  }
  if (opts.allMean) {
    notes.push('Distances are each ' + member + '\u2019s mean to that structure.');
  }
  if (notes.length) {
    var note = document.createElement('div');
    note.className = 'chart-note';
    note.textContent = notes.join(' ');
    host.appendChild(note);
  }
}

/** The facets of a block, named in one legend above its rows of panels. */
function facetLegendHtml(facets) {
  if (!facets.many) return '';
  return '<div class="chart-legend">' + facets.values.map(function(v) {
    return '<span class="d-inline-flex align-items-center gap-1">'
      + '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'
      + gColor(v) + '"></span>' + escHtml(v) + '</span>';
  }).join('') + '</div>';
}

/**
 * The facets a population is split into, ready for the panels above.
 *
 * One entry per object or per group, in one place because three sections split the same way
 * and each used to do it themselves.
 */
function facetsOf(rows, opts) {
  var key = plotGroupBy === 'group' ? 'group_id' : 'object_id';
  var values = uniq(rows.map(function(r){ return r[key]; }).filter(Boolean));
  var short = {};
  shortFacetLabels(values).forEach(function(label, i){ short[values[i]] = label; });
  return {
    key: key,
    word: plotGroupBy === 'group' ? 'group' : noun(),
    values: values,
    many: values.length > 1,
    labelOf: function(v){ return short[v] != null ? short[v] : String(v); },
    /** `{ name, label, color, rows }` per facet, or one entry for the lot. */
    split: function(subset, fallback) {
      if (values.length < 2) {
        return subset.length
          ? [{ name: fallback.name, label: fallback.name, color: fallback.color, rows: subset }]
          : [];
      }
      return values.map(function(v) {
        return { name: String(v), label: short[v] != null ? short[v] : String(v),
                 color: gColor(v),
                 rows: subset.filter(function(r){ return r[key] === v; }) };
      }).filter(function(f){ return f.rows.length; });
    },
    /**
     * The same, with one metric read off each row: what distributionPanel wants.
     *
     * Two populations come back per facet. `values` is every row, which is what the boxes
     * and the histograms draw. `units` is one number per object - the median of that
     * object's rows - which is what a test between facets has to use: eight thousand
     * granules inside one cell are eight thousand readings of one cell, not eight thousand
     * samples, and a rank test handed them answers a question nobody asked.
     */
    valuesOf: function(subset, column, fallback) {
      return this.split(subset, fallback).map(function(f) {
        var values = [], byObject = {};
        f.rows.forEach(function(r) {
          var value = r[column];
          if (value == null || !isFinite(value)) return;
          values.push(value);
          var id = String(r.object_id == null ? '' : r.object_id);
          (byObject[id] = byObject[id] || []).push(value);
        });
        return { name: f.name, label: f.label, color: f.color, values: values,
                 unitIds: Object.keys(byObject),
                 units: Object.keys(byObject).map(function(id) {
                   return quantileSorted(
                     byObject[id].slice().sort(function(a, b){ return a - b; }), 0.5);
                 }) };
      }).filter(function(f){ return f.values.length; });
    }
  };
}
