

var _ctInited = false;

/**
 * Fill the structure selector and the gap slider, and say which structure was chosen.
 *
 * The choice is returned and never read back off the select, because the select is
 * rebuilt here: reading it afterwards is how the section ended up drawing "% touching
 * another , by object" about a structure named nothing at all.
 */
function initContactsControls() {
  var sel = document.getElementById('ct-pair');
  var previous = sel.value;
  var structures = contactStructures();
  sel.innerHTML = structures.map(function(s) {
    return '<option value="' + escHtml(s) + '">' + escHtml(s) + '</option>';
  }).join('');
  var chosen = structures.indexOf(previous) >= 0 ? previous
    : structures.indexOf(st.statsEntity) >= 0 ? st.statsEntity
    : structures[0];
  sel.value = chosen;
  // The structure as pills, pinned under the top bar like the ones in the structure section: the
  // choice stays in view while the section's charts scroll past.
  var pills = document.getElementById('ct-pills');
  if (pills && pills.replaceChildren) {
    pills.replaceChildren();
    var label = document.createElement('span');
    label.className = 'pill-label';
    label.textContent = 'Structure';
    pills.appendChild(label);
    structures.forEach(function(name) {
      var pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'pill' + (name === chosen ? ' active' : '');
      pill.style.setProperty('--c', entColor(name));
      pill.innerHTML = '<i style="background:' + entColor(name) + '"></i>' + escHtml(name);
      pill.addEventListener('click', function() {
        sel.value = name;
        renderContacts();
      });
      pills.appendChild(pill);
    });
  }

  // Size the slider to the recorded gaps: full span = data max, ~100 steps. Start tight
  // (a quarter of the range, near "actually touching") - opening at the max would link the
  // whole recorded set into a few giant blobs and drown out the typical small groups.
  var maxGap = contactMaxGap();
  var slider = document.getElementById('ct-thr');
  slider.max = maxGap;
  slider.step = maxGap / 100;
  setText('ct-thr-max', fmtGap(maxGap) + ' µm (max recorded)');
  if (!_ctInited) {
    slider.value = maxGap * 0.25;
    setText('ct-thr-val', fmtGap(maxGap * 0.25));
    _ctInited = true;
    sel.onchange = renderContacts;
    slider.oninput = function() {
      setText('ct-thr-val', fmtGap(+this.value));
      renderContacts();
      syncViewerGap(+this.value);
    };
  } else if (+slider.value > maxGap) {
    slider.value = maxGap;
    setText('ct-thr-val', fmtGap(maxGap));
  }
  return chosen;
}

function renderContacts() {
  var section = document.getElementById('s-groups');
  if (!contactStructures().length) { section.classList.add('d-none'); return; }
  section.classList.remove('d-none');
  var entity = initContactsControls();
  var thr = +document.getElementById('ct-thr').value;
  if (!entity) { section.classList.add('d-none'); return; }
  var named = dataName(entity);

  // The example is the reader's own structures. Written out with somebody else's organelles
  // it explained mitochondria to someone looking at blood vessels.
  // No articles: a structure named `granules` turns "a granules" into nonsense, and the
  // rule is clearer without an example anyway.
  setHtml('ct-subtitle',
    'A contact group is a set of ' + named + ' that touch one another, directly or through '
    + 'others in the chain. Two ' + named + ' closer than the gap you choose belong to the '
    + 'same group, and one with none close enough to another stands alone.');
  setHtml('ct-coverage', coverageNote('contacts'));

  var scope = uniq(filteredInstances().filter(function(r){ return r.entity_name === entity; })
                                      .map(function(r){ return r.object_id; }));
  if (!scope.length) scope = uniq(OBJECTS.filter(inScope).map(function(r){ return r.object_id; }));

  setHtml('ct-summary', '');
  setText('ct-count', 'within ' + fmtGap(thr) + ' µm · ' + scope.length + ' '
    + noun(scope.length !== 1) + ' in scope');

  renderGroupSizes(scope, entity, thr, named);
  renderGroupReach(scope, entity, thr, named);
  renderGroupBaseline(scope, entity, thr, named);
}

/**
 * How large the groups get, and what share of instances are in one.
 *
 * Two views of the same graph: an instance is in a group iff it lands in one of 2+, so the
 * two always agree. The share is the block a condition has replicates for - one number per
 * object - which is why it is the one that gets a bracket.
 */
function renderGroupSizes(scope, entity, thr, named) {
  var wrap = document.getElementById('ct-compare-wrap');
  var chartsEl = document.getElementById('ct-compare-charts');
  var tableEl = document.getElementById('ct-compare-table');

  var groupOf = {};
  OBJECTS.forEach(function(r){ groupOf[r.object_id] = r.group_id || '(no group)'; });
  // One row per object: its group sizes, and its own share of instances in a group.
  var perObject = scope.map(function(oid) {
    var c = contactClusters(oid, entity, thr);
    var grouped = c.sizes.reduce(function(a, b){ return a + b; }, 0);
    return { object_id: oid, group_id: groupOf[oid], sizes: c.sizes,
             instances: c.instances, grouped: grouped,
             share: c.instances ? 100 * grouped / c.instances : 0,
             largest: c.sizes.reduce(function(m, v){ return Math.max(m, v); }, 0) };
  });
  if (!perObject.length) { wrap.classList.add('d-none'); return; }
  wrap.classList.remove('d-none');

  var facets = facetsOf(perObject);
  setHtml('ct-compare-title', 'How large do the ' + named + ' groups get?');
  setHtml('ct-compare-desc',
    '<b>Left:</b> how many instances chain together into one group. <b>Right:</b> the share'
    + ' of ' + named + ' instances that reach another, out of <em>every</em> instance of it -'
    + ' including the ones touching nothing, which is what makes it a share.'
    + ' Both panels are tested on one number per ' + noun() + ' - the share itself,'
    + ' and the median group size - since group sizes within one ' + noun() + ' are not'
    + ' independent of each other.');
  setHtml('ct-legend', !facets.many ? '' : facets.values.map(function(v) {
    return '<span class="d-inline-flex align-items-center gap-1">'
      + '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'
      + gColor(v) + '"></span>' + escHtml(v) + '</span>';
  }).join(''));

  chartsEl.innerHTML = '';
  // Group sizes, pooled per facet to draw, and one median per object to test on - the same
  // rule every other panel follows.
  var sizeSeries = facets.split(perObject, { name: entity, color: entColor(entity) })
    .map(function(f) {
      var medians = f.rows.map(function(r) {
        return r.sizes.length
          ? quantileSorted(r.sizes.slice().sort(function(a, b){ return a - b; }), 0.5) : null;
      }).filter(function(v){ return v != null; });
      return { name: f.name, label: f.label, color: f.color,
               values: f.rows.reduce(function(all, r){ return all.concat(r.sizes); }, []),
               units: medians };
    }).filter(function(f){ return f.values.length; });
  if (sizeSeries.length) {
    distributionPanel(chartsEl, {
      series: sizeSeries, title: entity + ' per group (groups of 2+)',
      unit: 'instances', noun: 'group', width: 340, height: 268,
      showFacetTicks: !facets.many,
      help: 'How many instances chained together into one group at the current gap.',
      explain: groupSpec(entity, 'size')
    });
  } else {
    chartsEl.innerHTML = '<div class="text-muted small">No groups of 2+ at this gap.</div>';
  }
  // The share, one point per object: the level a condition has replicates at.
  distributionPanel(chartsEl, {
    series: facets.valuesOf(perObject, 'share', { name: entity, color: entColor(entity) }),
    title: '% of ' + entity + ' reaching another', unit: '%', noun: noun(),
    width: 340, height: 268, showFacetTicks: !facets.many,
    // A share is out of a hundred whether or not any object gets near either end.
    range: [0, 104], explain: groupSpec(entity, 'share')
  });

  var share = function(part, whole) {
    return whole ? (part + ' <span class="text-muted" style="font-size:.7em">'
      + fmtPct(part, whole) + '</span>') : '–';
  };
  var rows = perObject.slice().sort(function(a, b) {
    return String(a.group_id).localeCompare(String(b.group_id))
      || String(a.object_id).localeCompare(String(b.object_id));
  });
  var hasGroups = groupNames().length > 0;
  tableEl.replaceChildren();
  perObjectFold(tableEl, 'Show the numbers for each ' + noun(), function(body) {
  body.innerHTML = '<table class="table table-sm table-hover mb-0" style="min-width:420px">'
    + '<thead class="table-light"><tr>'
    + (hasGroups ? '<th>group</th>' : '')
    + '<th>' + escHtml(noun()) + '</th><th class="text-end">' + escHtml(entity) + ' (n)</th>'
    + '<th class="text-end">reaching another</th><th class="text-end">groups (≥2)</th>'
    + '<th class="text-end">largest</th><th class="text-end">mean size</th></tr></thead><tbody>'
    + rows.map(function(r) {
        var mean = r.sizes.length
          ? r.sizes.reduce(function(a, b){ return a + b; }, 0) / r.sizes.length : 0;
        return '<tr>'
          + (hasGroups ? '<td><span style="display:inline-block;width:.7em;height:.7em;'
              + 'border-radius:2px;background:' + groupColor(r.group_id)
              + ';margin-right:.4em"></span>' + escHtml(r.group_id) + '</td>' : '')
          + '<td>' + escHtml(r.object_id) + '</td>'
          + '<td class="text-end">' + r.instances + '</td>'
          + '<td class="text-end">' + share(r.grouped, r.instances) + '</td>'
          + '<td class="text-end">' + r.sizes.length + '</td>'
          + '<td class="text-end">' + (r.largest || '–') + '</td>'
          + '<td class="text-end">' + (mean ? mean.toFixed(1) : '–') + '</td></tr>';
      }).join('')
    + '</tbody></table>';
  });
}

/**
 * Where the groups sit: the same two questions the instance section asks, asked of groups.
 *
 * A group's distance to a structure is its closest approach - the nearest any member gets -
 * so "this network touches the membrane" is one number per group, and the panels are the
 * same ones drawn above.
 */
function renderGroupReach(scope, entity, thr, named) {
  var farWrap = document.getElementById('ct-far-wrap');
  var bothWrap = document.getElementById('ct-both-wrap');
  var farEl = document.getElementById('ct-far-charts');
  var bothEl = document.getElementById('ct-both-charts');
  farEl.innerHTML = '';
  bothEl.innerHTML = '';

  var built = groupRows(scope, entity, thr);
  var rows = built.rows;
  var targets = built.targets.filter(function(col){ return hasValue(rows, col); });
  if (!rows.length || !targets.length) {
    farWrap.classList.add('d-none');
    bothWrap.classList.add('d-none');
    return;
  }
  var facets = facetsOf(rows);
  var fallback = { name: entity + ' groups', color: entColor(entity) };

  farWrap.classList.remove('d-none');
  setHtml('ct-far-label', 'How far is each ' + named + ' group from other structures?');
  setHtml('ct-far-desc',
    'One value per group: its <b>closest approach</b> to that structure, the nearest any of'
    + ' its members gets. Near 0 means the group is up against it somewhere along its'
    + ' length. ' + rows.length.toLocaleString() + ' group'
    + (rows.length === 1 ? '' : 's') + ' of 2+ at the current gap.');
  orderDistDims(targets).forEach(function(col) {
    distributionPanel(farEl, {
      series: facets.valuesOf(rows, col, fallback),
      title: entity + ' group to ' + distColLabel(col), unit: 'µm', noun: 'group',
      // Not the instance-level description with a prefix: that one talks about "the
      // instance's voxels", which is not what this panel measures.
      help: 'The nearest any member of the group gets to ' + dataName(distColLabel(col))
        + '. 0 means the group overlaps it somewhere.',
      showFacetTicks: !facets.many, explain: groupSpec(entity, 'reach', [col])
    });
  });

  if (targets.length >= 2) {
    bothWrap.classList.remove('d-none');
    setHtml('ct-both-label', 'Is each ' + named + ' group close to two structures at once?');
    setHtml('ct-both-desc',
      'One panel per pair. For each group we take the <b>larger</b> of its two closest'
      + ' approaches - the distance at which it reaches <b>both</b> structures - and the curve'
      + ' shows the share of groups at or below it. A curve that <b>rises early and steeply</b>'
      + ' means most groups bridge the two. The panels above read the same way for single'
      + ' instances.');
    pairPanels(bothEl, {
      series: facets.split(rows, fallback), dims: orderDistDims(targets), noun: 'group',
      entity: entity
    });
  } else {
    bothWrap.classList.add('d-none');
  }
}

/**
 * Closer than a group of its size would be anyway?
 *
 * The panel above plots the distances themselves, which is what you want to know and is
 * also confounded by construction: a group of ten reaches closer than a group of two
 * whatever either is made of, because the closest of ten draws is closer than the closest
 * of two. Here each group is scored against the groups of its own size the same object
 * could have formed, so 50% is "nothing going on" at every size, and the concrete distance
 * stays under the panel so the score never has to be read on its own.
 *
 * No bracket on this one. The scores are per group, and groups within one object come from
 * the same pool as each other, so they are not independent observations of a condition -
 * the difference between conditions is the block above, one number per object.
 */
function renderGroupBaseline(scope, entity, thr, named) {
  var wrap = document.getElementById('ct-baseline-wrap');
  var host = document.getElementById('ct-baseline-charts');
  host.innerHTML = '';

  var built = groupBaselineRows(scope, entity, thr);
  var targets = built.targets.filter(function(col){ return hasValue(built.rows, col); });
  if (!built.rows.length || !targets.length) {
    wrap.classList.add('d-none');
    return;
  }
  wrap.classList.remove('d-none');
  var facets = facetsOf(built.rows);
  var fallback = { name: entity + ' groups', color: entColor(entity) };

  setHtml('ct-baseline-label',
    'Is a ' + named + ' group closer than its size alone would make it?');
  setHtml('ct-baseline-desc',
    'The distances above are confounded by construction: <b>a group of ten reaches closer to'
    + ' anything than a group of two</b>, whatever either is made of, because the closest of'
    + ' ten draws is closer than the closest of two. So each group is scored against the'
    + ' groups of <b>its own size</b> that the same ' + named + ' instances in the same '
    + noun()
    + ' could have formed - the share of them it beats by reaching closer. <b>50%</b> is what'
    + ' chance gives at any size, at the dotted line; above it the group reaches closer than'
    + ' its size explains, below it further. Exact over the ' + noun() + '\u2019s own'
    + ' distances, with'
    + ' ties split evenly. The median distance behind each box is under it, because a score'
    + ' does not say how close "closer" is.');

  orderDistDims(targets).forEach(function(col) {
    var series = facets.valuesOf(built.rows, col, fallback);
    if (!series.length) return;
    // The concrete distances behind the scores, per facet, in the same order.
    var footnote = facets.split(built.rows, fallback).map(function(f) {
      var reaches = f.rows.map(function(r){ return r[col + '__reach']; })
                          .filter(function(v){ return v != null && isFinite(v); })
                          .sort(function(a, b){ return a - b; });
      if (!reaches.length) return '';
      return '<span class="fit-note-item"><span class="fit-dot" style="background:' + f.color
        + '"></span>' + escHtml(f.label) + ' median '
        + quantileSorted(reaches, 0.5).toPrecision(3) + ' µm</span>';
    }).filter(Boolean).join(' ');
    distributionPanel(host, {
      series: series, title: entity + ' group to ' + distColLabel(col),
      unit: '% of same-size groups beaten',
      noun: 'group', showFacetTicks: !facets.many, range: [0, 104], reference: 50,
      noSignificance: true, width: 300, explain: groupSpec(entity, 'baseline', [col]),
      footnote: footnote ? '<div class="fit-note">' + footnote + '</div>' : ''
    });
  });
}
