/**
 * Fill the "Showing" filter with whatever the charts are currently grouped by.
 *
 * A choice that is no longer on offer - one object, after switching to grouping by group -
 * falls back to all of them, so the filter never empties the report.
 */
function fillFacetFilter() {
  var choices = facetChoices();
  if (choices.indexOf(st.only) < 0) st.only = '';
  var all = plotGroupBy === 'group' ? 'All groups' : 'All ' + noun(true);
  setHtml('flt-only', '<option value="">' + all + '</option>'
    + choices.map(function(v) {
        return '<option value="' + escHtml(v) + '">' + escHtml(v) + '</option>';
      }).join(''));
  document.getElementById('flt-only').value = st.only;
}

/**
 * What a bracket is comparing, or why there is none.
 *
 * A test is run on the objects behind a facet (see pairwiseSignificance), so grouping the
 * charts by object leaves one object per box and nothing to compare. That is a property of
 * the grouping, not of any panel, so it is said here, once, beside the switch.
 */
function sayWhatIsTested() {
  var hint = document.getElementById('sig-hint');
  if (!hint) return;
  var objects = uniq(OBJECTS.map(function(r){ return r.object_id; })).length;
  hint.textContent = !showSignificance ? ''
    : (plotGroupBy === 'object' && objects > 1 ? 'one ' + noun() + ' per box: nothing to test' : '');
}

/**
 * What the groups' sizes allow a test to say, for the comparison between the two smallest.
 *
 * A rank test between 4 cells and 3 cannot return a p below 0.057 whatever the data, so every
 * bracket on the page would read "not significant" however different the groups are. That is
 * a fact about the batch and not about any chart, and the reader is owed it before they read
 * a single "ns" as a finding. Null where there are no two groups to compare.
 */
function testLimit() {
  if (plotGroupBy !== 'group') return null;
  var sizes = {};
  filteredObjects().forEach(function(o) {
    if (o.group_id) sizes[o.group_id] = (sizes[o.group_id] || 0) + 1;
  });
  var counts = Object.keys(sizes).map(function(g){ return sizes[g]; }).sort(function(a, b){ return a - b; });
  if (counts.length < 2) return null;
  var pMin = smallestPossibleP(counts[0], counts[1]);
  return { n1: counts[0], n2: counts[1], pMin: pMin, floor: pMin > 0.05, groups: counts.length };
}

/**
 * Redraw the whole report after a control changed, with a notice up while it happens.
 *
 * The redraw is one synchronous stretch that freezes the page, so the notice has to be on
 * screen before it starts: wait a frame, or it is only ever seen afterwards.
 */
function renderAllWithNotice(text) {
  var notice = document.createElement('div');
  notice.className = 'render-busy';
  notice.setAttribute('role', 'status');
  notice.textContent = text;
  document.body.appendChild(notice);
  requestAnimationFrame(function() {
    setTimeout(function() {
      try { renderAll(); } finally { notice.remove(); }
    }, 0);
  });
}

function initFilters() {
  var gs = groupNames();
  // By group wherever a batch has groups. That is the comparison the run was set up to
  // make - an object is one sample of a condition, not a thing to compare against another
  // condition's - so it is what the report should open on. A reader who wants the objects
  // can still say so, and with one group there is nothing to compare: the facets are the
  // objects and the control is not offered at all.
  plotGroupBy = gs.length > 1 ? 'group' : 'object';
  document.getElementById('plot-groupby-wrap').style.display = gs.length > 1 ? 'flex' : 'none';
  // The select is markup, so it opens on its first option unless it is told otherwise -
  // and a control reading "object" over charts grouped by group is worse than either.
  document.getElementById('plot-groupby').value = plotGroupBy;
  fillFacetFilter();
  document.getElementById('flt-only').onchange = function(){
    st.only = this.value;
    renderAllWithNotice(this.value ? 'Showing ' + this.value + '…' : 'Showing ' + this.options[0].textContent.toLowerCase() + '…');
  };
  document.getElementById('plot-groupby').onchange = function(){
    plotGroupBy = this.value;
    fillFacetFilter();
    sayWhatIsTested();
    renderAllWithNotice('Regrouping the charts…');
  };
  var style = document.getElementById('plot-style');
  style.value = plotStyle;
  style.onchange = function(){
    plotStyle = this.value;
    renderAllWithNotice('Redrawing the charts…');
  };
  // Always offered, whatever the faceting: opting in or out is the reader's call.
  var sigWrap = document.getElementById('sig-wrap');
  sigWrap.classList.remove('d-none');
  sigWrap.classList.add('d-flex');
  var sigBox = document.getElementById('show-significance');
  sigBox.checked = showSignificance;
  sigBox.onchange = function(){
    showSignificance = this.checked;
    sayWhatIsTested();
    renderAllWithNotice('Redrawing the charts…');
  };
  sayWhatIsTested();
}

/**
 * Draw every section, and say so on the page if one of them cannot be drawn.
 *
 * A section that throws used to be reported as a failure to *load*, which threw the whole
 * report away and named the wrong thing: the parquet was read fine. Each section is drawn
 * on its own so the others survive, and what went wrong is said where it happened.
 */
function renderAll() {
  // Kept in step with the grouping, and set here and not when the control changes: every path
  // that redraws the report - a load, a filter, the switch itself - comes through here.
  sayWhatIsTested();
  var sections = [
    ['overview', renderOverview], ['3d', renderScene], ['entities', renderEntityStats],
    ['groups', renderContacts], ['polarity', renderPolarity], ['structure', renderFileTable],
    ['nav', buildSubnav]
  ];
  sections.forEach(function(entry) {
    try {
      entry[1]();
    } catch (error) {
      console.error('organella: could not draw the ' + entry[0] + ' section', error);
      var section = document.getElementById('s-' + entry[0]);
      if (!section) return;
      section.classList.remove('d-none');
      var note = document.createElement('p');
      note.className = 'callout callout-warn';
      note.textContent = 'This section could not be drawn: '
        + (error && error.message ? error.message : String(error));
      section.appendChild(note);
    }
  });
  syncStickyBars();
}
