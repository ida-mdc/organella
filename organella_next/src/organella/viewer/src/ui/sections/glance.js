/** Bring up the chart of one measurement of one structure, scroll to it, and flash it. */
function goToChart(ent, col) {
  if (st.statsEntity !== ent) {
    st.statsEntity = ent;
    var sel = document.getElementById('stats-entity-select');
    if (sel) sel.value = ent;
    renderEntityStats();
  }
  var needle = '|' + ent + '|' + col + '|';
  var button = [].find.call(document.querySelectorAll('.pin-3d'), function(b) {
    return b.dataset.explain.indexOf(needle) >= 0 && /^(metric|distance)\|/.test(b.dataset.explain);
  });
  var cell = button && button.parentNode;
  if (!cell) { document.getElementById('s-entities').scrollIntoView({ behavior: 'smooth' }); return; }
  cell.scrollIntoView({ behavior: 'smooth', block: 'center' });
  cell.classList.remove('flash');
  void cell.offsetWidth;
  cell.classList.add('flash');
}

/**
 * Where the groups differ most, one structure at a time: for every measurement, how far apart
 * the two groups' cells are (Cliff's delta over one median per cell, from -1 to +1) as a bar
 * from the middle, largest first. It answers "where should I look" before the reader has gone
 * through two hundred charts - and says how many comparisons that is, because a handful picked
 * out of dozens is not what it looks like on its own. Two groups only: with more there is no
 * single direction for a bar to point.
 */
function renderDifferences() {
  var host = document.getElementById('glance-diff');
  if (!host) return;
  host.innerHTML = '';
  if (plotGroupBy !== 'group') return;
  var rows = filteredInstances();
  var facets = facetsOf(rows);
  if (facets.values.length !== 2) return;
  var A = String(facets.values[0]), B = String(facets.values[1]);
  var blocks = [];
  uniq(rows.map(function(r){ return r.entity_name; })).forEach(function(ent) {
    var mine = rows.filter(function(r){ return r.entity_name === ent; });
    var items = [];
    nonDistMetricCols(mine).concat(distCols(mine)).forEach(function(col) {
      var series = facets.valuesOf(mine, col, { name: ent, color: entColor(ent) });
      var a = series.filter(function(f){ return f.name === A; })[0];
      var b = series.filter(function(f){ return f.name === B; })[0];
      if (!a || !b) return;
      var test = mannWhitney(a.units, b.units);
      if (!test || test.delta == null) return;
      items.push({ col: col, test: test, ent: ent });
    });
    if (items.length) {
      items.sort(function(x, y){ return Math.abs(y.test.delta) - Math.abs(x.test.delta); });
      blocks.push({ ent: ent, items: items });
    }
  });
  if (!blocks.length) return;

  // A distance is to something, and "golgi" alone reads as the structure and not the measurement.
  var diffLabel = function(col) {
    if (col === 'distance_to_closest_same_type_um') return 'distance to its own kind';
    return col.indexOf('distance_to_') === 0 ? 'distance to ' + distColLabel(col) : axisColLabel(col);
  };
  var rowHtml = function(item) {
    var t = item.test, side = t.delta >= 0 ? A : B;
    var unit = axisColUnit(item.col);
    var width = Math.min(50, Math.abs(t.delta) * 50);
    var bar = t.delta >= 0 ? 'left:50%;width:' + width + '%' : 'right:50%;width:' + width + '%';
    return '<div class="diff-row" data-ent="' + escHtml(item.ent) + '" data-col="' + escHtml(item.col)
      + '" role="button" tabindex="0" title="' + escHtml('Go to the chart. ' + labelFor(item.col) + ': ' + A + ' vs ' + B
        + (t.floor ? '' : ', ' + formatP(t.p) + ', ' + t.n1 + ' vs ' + t.n2 + ' ' + noun(true))) + '">'
      + '<span class="diff-label">' + escHtml(diffLabel(item.col))
      + (unit ? ' <span class="unit">(' + unit + ')</span>' : '') + '</span>'
      + '<span class="diff-bar"><span style="' + bar + ';background:' + groupColor(side) + '"></span></span>'
      + '<span class="diff-num">' + (t.delta >= 0 ? '+' : '−') + Math.abs(t.delta).toFixed(2)
      + (significanceLabel(t) ? ' · ' + escHtml(significanceLabel(t)) : '') + '</span></div>';
  };
  host.onclick = host.onkeydown = function(event) {
    if (event.type === 'keydown' && event.key !== 'Enter') return;
    var row = event.target.closest && event.target.closest('.diff-row');
    if (row) goToChart(row.dataset.ent, row.dataset.col);
  };
  var head = '<div class="diff-axis"><span></span><span class="sides"><span>← ' + escHtml(B)
    + ' higher</span><span>' + escHtml(A) + ' higher →</span></span><span></span></div>';
  host.innerHTML = '<div class="diff-title">Where do ' + escHtml(A) + ' and ' + escHtml(B) + ' differ most?</div>'
    + '<div class="diff-blocks">' + blocks.map(function(block) {
        var first = block.items.slice(0, 6).map(rowHtml).join('');
        var rest = block.items.slice(6).map(rowHtml).join('');
        return '<div class="diff-block"><h4>' + dataName(block.ent) + '</h4>' + head + first
          + (rest ? '<details class="per-object diff-more"><summary>Show the other '
              + (block.items.length - 6) + '</summary>' + rest + '</details>' : '') + '</div>';
      }).join('') + '</div>'
    + '<p class="diff-note">Press a row to go to its chart. Bar: Cliff’s delta, the effect size of the'
    + ' difference between two groups, from −1 to +1. 0 means the groups overlap completely; ±1 means every'
    + ' value in one group is higher than every value in the other.</p>';
}

/**
 * The top of the report: which groups there are (in the colours every chart uses), what the
 * batch's size allows a test to say, how to read what follows, and which part of the file
 * each section is made of. A reader should know all four before the first chart.
 */
function renderGlance() {
  var host = document.getElementById('glance-groups');
  if (!host) return;
  var groups = groupNames();
  var members = {};
  filteredObjects().forEach(function(o) {
    if (o.group_id) (members[o.group_id] = members[o.group_id] || []).push(o.object_id);
  });
  document.getElementById('fact-groups-row').style.display = groups.length < 2 ? 'none' : '';
  host.innerHTML = groups.length < 2 ? '' : groups.map(function(g) {
      var ids = members[g] || [];
      return '<span class="item" title="' + escHtml(ids.join(', ')) + '"><i style="background:'
        + groupColor(g) + '"></i>' + escHtml(g) + ' <span class="muted">' + ids.length + ' '
        + noun(ids.length !== 1) + '</span></span>';
    }).join('');

  renderDifferences();
}
