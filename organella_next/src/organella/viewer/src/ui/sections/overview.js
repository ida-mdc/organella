/**
 * A fold for what is done once per object: the page shows the average of a group by default,
 * and the object-by-object view is here for whoever wants to check one.
 *
 * `build` runs on first opening, not on drawing the page: a chart built inside a closed
 * fold has no width to be drawn at, and thirty hidden panels are thirty drawn for nothing.
 */
function perObjectFold(container, summary, build) {
  var fold = document.createElement('details');
  fold.className = 'per-object';
  var label = document.createElement('summary');
  label.textContent = summary;
  var body = document.createElement('div');
  body.className = 'per-object-body';
  fold.append(label, body);
  var built = false;
  fold.addEventListener('toggle', function() {
    if (!fold.open || built) return;
    built = true;
    build(body);
  });
  container.appendChild(fold);
  return fold;
}

/** How much of the region is which structure: one stacked bar per group, one scale.
 *
 * This is the question "how much of each structure is there" actually asked of a whole:
 * absolute totals on eight separate axes cannot be compared - a nucleus and a lipid
 * droplet drew the same bar - where shares of one region can be read against each other
 * and across objects at a glance.
 *
 * Structures may overlap (chromatin sits inside the nucleus), so the parts are not always
 * a partition. When they sum past the region the chart normalises to the segmented total
 * instead, drops the remainder, and says so: a stack that overflows its own axis, or a
 * negative remainder, would be worse than a different denominator named out loud.
 */
function compositionPanel(container, opt) {
  var terms = opt.terms;
  var rows = [];
  // The bounding mask is not one of the parts: it *is* the whole. Left in, it fills the bar
  // on its own and the structures inside it read as an overflow of their own container.
  var maskName = (OBJECTS[0] || {}).object_mask_name;
  var names = opt.entNames.filter(function(n){ return n !== maskName; });
  opt.objects.forEach(function(oid) {
    var object = OBJECTS.find(function(r){ return r.object_id === oid; });
    var parts = [], sum = 0;
    names.forEach(function(name) {
      var row = opt.entRows.find(function(r) {
        return r.object_id === oid && r.entity_name === name;
      });
      var value = row ? totalExtent(row) : null;
      if (value == null) return;
      parts.push({ name: name, value: +value });
      sum += +value;
    });
    if (parts.length) rows.push({ oid: oid, parts: parts, sum: sum,
                                  region: analysedExtent(object) });
  });
  if (!rows.length) {
    container.innerHTML = '<div class="text-muted small">No '
      + escHtml(terms.word) + ' was measured for any structure.</div>';
    return;
  }

  // One denominator for the whole chart, so the bars are comparable: the region when every
  // object has one and nothing overflows it, the segmented total otherwise.
  var haveRegion = rows.every(function(r){ return r.region != null; });
  var overflows = haveRegion && rows.some(function(r){ return r.sum > r.region.value * 1.0005; });
  var useRegion = haveRegion && !overflows;
  var bounded = useRegion && rows[0].region.bounded;
  rows.forEach(function(r) { r.whole = useRegion ? r.region.value : r.sum; });

  var wholeLabel = useRegion ? (bounded ? 'the ' + noun() : 'the analysed region')
                             : 'the segmented ' + terms.word;

  // The legend is the page's own, in HTML above the plot and not Plotly's: a nine-key
  // legend under a chart this short lands inside the bottom margin and sits on top of the
  // axis title.
  var keys = names.concat(useRegion ? [' rest'] : []);
  container.insertAdjacentHTML('beforeend', '<div class="chart-legend">'
    + keys.map(function(name) {
        var rest = name === ' rest';
        return '<span class="d-inline-flex align-items-center gap-1">'
          + '<span style="display:inline-block;width:9px;height:9px;border-radius:2px;'
          + 'background:' + (rest ? '#e4e8ed' : entColor(name)) + '"></span>'
          + (rest ? 'not in any structure' : dataName(name)) + '</span>';
      }).join('') + '</div>');

  // A bar is shares of one whole, one per structure, whatever it stands for: one object, or
  // the mean of a group of them.
  var barOf = function(label, row) {
    var shares = {}, absolute = {};
    row.parts.forEach(function(part) {
      shares[part.name] = row.whole ? 100 * part.value / row.whole : 0;
      absolute[part.name] = part.value;
    });
    return { label: label, shares: shares, absolute: absolute,
             rest: row.whole ? Math.max(0, 100 * (row.whole - row.sum) / row.whole) : 0,
             restAbsolute: Math.max(0, row.whole - row.sum) };
  };
  var objectBars = rows.map(function(r){ return barOf(r.oid || '(unnamed)', r); });

  // The mean over the objects that have a structure: one without it has no share of it to
  // average in, and a zero would say it has none of something it was never segmented for.
  // What is left over is then whatever the means do not cover.
  var meanBar = function(label, members) {
    var shares = {}, absolute = {}, partial = false;
    names.forEach(function(name) {
      var have = members.filter(function(m){ return name in m.shares; });
      if (!have.length) return;
      if (have.length < members.length) partial = true;
      shares[name] = have.reduce(function(n, m){ return n + m.shares[name]; }, 0) / have.length;
      absolute[name] = have.reduce(function(n, m){ return n + m.absolute[name]; }, 0) / have.length;
    });
    var covered = Object.keys(shares).reduce(function(n, k){ return n + shares[k]; }, 0);
    return { label: label, shares: shares, absolute: absolute, partial: partial,
             rest: Math.max(0, 100 - covered), restAbsolute: 0 };
  };
  var groupOf = {};
  OBJECTS.forEach(function(o){ groupOf[o.object_id] = o.group_id || ''; });
  var groupNamesHere = uniq(rows.map(function(r){ return groupOf[r.oid]; }).filter(Boolean));
  var averaged = rows.length < 2 ? null
    : (groupNamesHere.length
        ? groupNamesHere.map(function(g) {
            var members = objectBars.filter(function(_, i){ return groupOf[rows[i].oid] === g; });
            return meanBar(g + ' (' + members.length + ' ' + noun(members.length !== 1) + ')',
                           members);
          })
        : [meanBar('all ' + rows.length + ' ' + noun(true), objectBars)]);

  var drawBars = function(host, bars) {
    var plotEl = document.createElement('div');
    host.appendChild(plotEl);
    // Top to bottom in reading order, done by reversing the data and not the
    // axis: a reversed autorange on a one-category axis put the only bar outside the range.
    var shown = bars.slice().reverse();
    var yLabels = shown.map(function(b){ return b.label; });
    var traces = names.map(function(name) {
      return {
        type: 'bar', orientation: 'h', name: String(name), y: yLabels,
        x: shown.map(function(b){ return b.shares[name] || 0; }),
        customdata: shown.map(function(b){ return b.absolute[name] || 0; }),
        marker: { color: entColor(name), line: { color: '#ffffff', width: 1 } },
        showlegend: false,
        hovertemplate: '%{customdata:.3g} ' + terms.unit + '  ·  %{x:.2g}% of '
          + wholeLabel + '<extra>' + escHtml(name) + '</extra>',
      };
    });
    if (useRegion) {
      traces.push({
        type: 'bar', orientation: 'h', name: 'not in any structure', y: yLabels,
        x: shown.map(function(b){ return b.rest; }),
        customdata: shown.map(function(b){ return b.restAbsolute; }),
        marker: { color: '#e4e8ed', line: { color: '#ffffff', width: 1 } },
        showlegend: false,
        hovertemplate: '%{x:.2g}% unsegmented<extra>not in any structure</extra>',
      });
    }
    // One bar needs no y tick - the table above already named it, and the axis then
    // carries a single label wider than the bar it points at.
    var single = bars.length === 1 && rows.length === 1;
    drawPlot(plotEl, traces, {
      barmode: 'stack', bargap: single ? 0.15 : 0.3,
      margin: { l: single ? 8 : 150, r: 16, t: 6, b: 40 },
      height: (single ? 62 : 30 * shown.length + 26) + 46,
      xaxis: { title: '% of ' + wholeLabel, range: [0, 100.4], dtick: 25, ticksuffix: '%' },
      yaxis: { type: 'category', automargin: !single, showticklabels: !single,
               ticks: '', showgrid: false, linecolor: 'rgba(0,0,0,0)' },
      showlegend: false, hovermode: 'closest',
    }, { responsive: true, displayModeBar: false });
  };

  drawBars(container, averaged || objectBars);
  if (averaged) {
    // Said before the list that opens every cell on its own, since it is about the bars above it.
    container.insertAdjacentHTML('beforeend', '<div class="chart-note">Each bar is the mean of its '
      + noun(true) + (averaged.some(function(b){ return b.partial; })
          ? ', over the ones that have each structure' : '') + '.</div>');
    perObjectFold(container, 'Show every ' + noun() + ' on its own', function(body) {
      drawBars(body, objectBars);
    });
  }

  var note;
  if (!useRegion) {
    note = overflows
      ? 'The structures overlap - together they cover more than the region holds - so this '
        + 'is each one\'s share of the total ' + terms.word + ' segmented, not of the '
        + noun() + '. Nothing here is missing; the parts are simply not a partition.'
      : 'No ' + noun() + ' extent was recorded, so this is each structure\'s share of the '
        + 'total ' + terms.word + ' segmented.';
  } else if (!bounded) {
    note = 'Nothing bounds ' + (rows.length > 1 ? 'these ' + noun(true) : 'this ' + noun())
      + ', so the whole is the region analysed - the image itself. Name one with '
      + '--object-mask to read these as shares of the ' + noun() + '.';
  }
  if (note) {
    container.insertAdjacentHTML('beforeend',
      '<div class="chart-note">' + note + '</div>');
  }
}

/**
 * Keep a long table from pushing everything after it off the screen: past a handful of rows
 * it is cut short and fades out, with a button that opens the rest.
 */
var TABLE_FOLD_ROWS = 8;

function foldLongTable(host, rows) {
  if (rows <= TABLE_FOLD_ROWS) return;
  var wrap = host.querySelector('.table-wrap');
  wrap.classList.add('table-folded');
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'table-fold-toggle';
  button.textContent = 'Show all';
  button.addEventListener('click', function() {
    var folded = wrap.classList.toggle('table-folded');
    button.textContent = folded ? 'Show all' : 'Show less';
  });
  host.appendChild(button);
}

function renderOverview() {
  var terms = extentTerms();
  var cols = structureColumns();
  var objects = scopedObjectNames();
  var summary = overviewSummary();

  var warnings = [];
  if (summary.mixedDims) {
    warnings.push('This batch mixes 2D and 3D ' + noun(true) + '. An area and a volume are not'
      + ' comparable, so the extent charts below hold both and only one of them is about any'
      + ' given ' + noun() + '.');
  }
  if (DROPPED) {
    warnings.push('Some rows of this report name no structure and could not be placed. They are'
      + ' left out of everything below.');
  }
  setHtml('overview-warnings', warnings.map(function(w) {
    return '<p class="callout callout-warn">' + w + '</p>';
  }).join(''));
  renderGlance();
  setText('fact-cells-label', Noun(summary.objects !== 1));
  setText('fact-cells', String(summary.objects || 1));
  setText('fact-structures-label', 'Structure' + (cols.length === 1 ? '' : 's'));
  setHtml('fact-structures', cols.map(function(c) {
    return '<span class="item"><i style="background:' + entColor(c.name) + '"></i>' + escHtml(c.name) + '</span>';
  }).join(''));

  // ── presence / instance-count table ───────────────────────────────────────
  var groupOf = {};
  OBJECTS.forEach(function(r){ groupOf[r.object_id] = r.group_id || ''; });
  var hasGroups = groupNames().length > 0;
  var entRows = filteredEnts();
  var rowObjects = (objects.length ? objects.slice() : ['']).sort(function(a, b) {
    return (groupOf[a] || '').localeCompare(groupOf[b] || '') || a.localeCompare(b);
  });
  var presence = {};
  entRows.forEach(function(r){ presence[r.object_id + '|' + r.entity_name] = r; });

  var thead = '<tr>'
    + (hasGroups ? '<th>group</th>' : '')
    + '<th>' + escHtml(noun()) + '</th>'
    + cols.map(function(c) {
        return '<th class="text-center">' + escHtml(c.name)
          + '<br><span class="cs-chip-kind">' + escHtml(c.kind) + '</span></th>';
      }).join('')
    // Its own extent is a thing only a bounded one has. Without a mask the column is not
    // in the report at all, so the table does not offer a row of dashes where it would be.
    + (NOUN.bounded
        ? '<th class="text-end">' + escHtml(noun()) + ' ' + escHtml(terms.word)
          + ' <span class="unit">(' + terms.unit + ')</span></th>'
        : '')
    + '</tr>';
  var tbody = rowObjects.map(function(oid) {
    var gid = groupOf[oid] || '';
    var dot = gid ? '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'
      + groupColor(gid) + ';margin-right:4px;flex-shrink:0"></span>' : '';
    var object = OBJECTS.find(function(r){ return r.object_id === oid; }) || {};
    var own = object.object_volume_um3 != null ? object.object_volume_um3 : object.object_area_um2;
    return '<tr>'
      + (hasGroups ? '<td>' + dot + escHtml(gid) + '</td>' : '')
      + '<td>' + escHtml(oid || '(unnamed)') + '</td>'
      + cols.map(function(c) {
          var r = presence[oid + '|' + c.name];
          if (!r) return '<td class="text-center cs-missing" title="' + escHtml(c.name)
            + ' not found for ' + escHtml(oid) + '">missing</td>';
          if (c.kind === 'mask') return '<td class="text-center">&#10003;</td>';
          var n = r.instance_count;
          return '<td class="text-center">' + (n != null ? (+n).toLocaleString() : '–') + '</td>';
        }).join('')
      + (NOUN.bounded
          ? '<td class="text-end">' + (own != null ? (+own).toFixed(1) : '–') + '</td>'
          : '')
      + '</tr>';
  }).join('');
  setHtml('overview-table-wrap',
    '<div class="table-wrap"><table class="table table-sm table-hover mb-0">'
    + '<thead class="table-light">' + thead + '</thead><tbody>' + tbody + '</tbody></table></div>');
  foldLongTable(document.getElementById('overview-table-wrap'), rowObjects.length);

  // ── what share of the region each structure occupies ──────────────────────
  var volContainer = document.getElementById('overview-volumes');
  volContainer.innerHTML = '';
  var entNames = cols.map(function(c){ return c.name; });
  if (!entNames.length) return;

  compositionPanel(volContainer, {
    objects: rowObjects, entNames: entNames, entRows: entRows, terms: terms,
  });

  // Below it, how many of each structure and how much of it, one value per object. For a single object
  // that is the composition above told again.
  if (rowObjects.length < 2) return;
  structureTotals(volContainer, {
    objects: rowObjects, groupOf: groupOf, entNames: entNames, entRows: entRows, terms: terms
  });
}

/**
 * How many of each structure there are, and how much of it in all, per object: the page's own box panels, in
 * two rows - the count, and the total - with a column per structure.
 *
 * A mask is one shape and has no count, so those columns come last and are empty in the first row. The columns
 * share the width and no narrower than a panel can be read; below that the table scrolls sideways.
 */
function structureTotals(host, o) {
  var groups = uniq(o.objects.map(function(oid){ return o.groupOf[oid]; }).filter(Boolean));
  var grouped = plotGroupBy === 'group' && groups.length > 1;
  var inObjects = o.entRows.filter(function(r){ return o.objects.indexOf(r.object_id) >= 0; });
  var facets = facetsOf(inObjects);
  var count = { head: 'count', unit: '', value: function(r){ return r.instance_count != null ? +r.instance_count : null; } };
  var total = { head: 'total ' + o.terms.word, unit: o.terms.unit, value: totalExtent };
  var counted = o.entNames.filter(function(e) {
    return inObjects.some(function(r){ return r.entity_name === e && r.instance_count != null; });
  });
  var names = counted.concat(o.entNames.filter(function(e){ return counted.indexOf(e) < 0; }));

  var container = document.createElement('div');
  container.className = 'totals-card';
  if (grouped) {
    container.insertAdjacentHTML('beforeend', '<div class="totals-legend">' + groups.map(function(g) {
      return '<span><i style="background:' + groupColor(g) + '"></i>' + escHtml(g) + '</span>';
    }).join('') + '</div>');
  }
  var grid = document.createElement('div');
  grid.className = 'totals-grid';
  grid.style.gridTemplateColumns = '92px repeat(' + names.length + ', minmax(170px, 1fr))';
  container.appendChild(grid);

  var cellOf = function(html) {
    var cell = document.createElement('div');
    cell.className = 'totals-cell';
    if (html) cell.innerHTML = html;
    grid.appendChild(cell);
    return cell;
  };
  cellOf('');
  names.forEach(function(e) {
    var head = cellOf(escHtml(e));
    head.className = 'totals-head';
    head.style.borderTopColor = entColor(e);
  });
  [count, total].forEach(function(m) {
    var head = cellOf(escHtml(m.head) + (m.unit ? '<br><span class="unit">' + escHtml(m.unit) + '</span>' : ''));
    head.className = 'totals-row';
    names.forEach(function(e) {
      var cell = cellOf('');
      var have = inObjects.filter(function(r){ return r.entity_name === e && m.value(r) != null; });
      // One row per object, so these values are the units a test compares.
      var series = facets.split(have, { name: e, color: entColor(e) }).map(function(f) {
        var values = f.rows.map(m.value);
        return { name: f.name, label: f.label, color: f.color, values: values, units: values };
      }).filter(function(f){ return f.values.length; });
      distributionPanel(cell, { series: series, unit: m.unit || null, noun: noun(), width: 200, height: 170,
                                showFacetTicks: false, noSizeLine: true });
    });
  });
  host.appendChild(container);
}
