/** Mark the chart cells whose spec is the one the viewer is showing. */
function markPinned() {
  var current = sceneState.explain ? explainKey(sceneState.explain) : null;
  // Charts of one view are told apart by where they are: two plots that explain the same
  // thing are not both the one pressed.
  var seen = {};
  document.querySelectorAll('.pin-3d').forEach(function(button) {
    var key = button.dataset.explain;
    seen[key] = seen[key] === undefined ? 0 : seen[key] + 1;
    button.parentNode.classList.toggle('pinned',
      current !== null && key === current && seen[key] === (sceneState.pinnedIndex || 0));
  });
  syncRoseMarkers();
  syncMapMarkers();
}

/** Show one cell in the viewer, as a diamond in a chart was pressed. */
function showCellInViewer(id) {
  if (!hasGeometry() || !GEOMETRY.some(function(o){ return String(o.id) === String(id); })) return;
  sceneState.objectId = GEOMETRY.find(function(o){ return String(o.id) === String(id); }).id;
  if (sceneState.explain) sceneState.entities = new Set(explainEntities(sceneState.explain));
  sceneState.maxGap = null;
  var dock = document.getElementById('s-3d');
  if (dock) {
    dock.classList.remove('flash');
    void dock.offsetWidth;
    dock.classList.add('flash');
  }
  renderScene();
}

/** Which of the charts with this button's spec it is, in page order. */
function pinIndexOf(button) {
  var same = [].filter.call(document.querySelectorAll('.pin-3d'), function(other) {
    return other.dataset.explain === button.dataset.explain;
  });
  return same.indexOf(button);
}

/**
 * Put a "Show in 3D" (or 2D) button on a chart cell. Pressing it again lets go.
 *
 * Always built, shown only when the report has geometry (the stylesheet decides): geometry
 * can be added after the charts are drawn, and nothing then has to redraw them.
 */
function explainButton(cell, spec) {
  if (!cell || !spec) return;
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'pin-3d';
  // The same small cube and the same word on every chart; pressed or not is the fill, never the
  // wording, so the button does not change size under the pointer. The cube is drawn here
  // because an icon font needs the network.
  var shape = PLANAR ? '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>'
    : '<path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z"/><path d="M2 4.8 8 8l6-3.2M8 8v6.5"/>';
  button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"'
    + ' stroke-width="1.4" stroke-linejoin="round" aria-hidden="true">' + shape + '</svg><span>' + dimsWord() + '</span>';
  button.title = 'Draw what this chart measures on a cell, in the viewer';
  button.setAttribute('aria-label', button.title);
  button.dataset.explain = explainKey(spec);
  button.addEventListener('click', function(){ pinToViewer(spec, pinIndexOf(button)); });
  cell.classList.add('pinnable');
  cell.appendChild(button);
  markPinned();
}

/**
 * Show a chart's subject in the viewer, or - given the spec already showing - let it go.
 *
 * What changes is only what the spec asks for: which object, which structures, how they are
 * coloured. The object stays the one on screen where it has the structure, and moves to one
 * that does where it has not, since a chart about a structure an object lacks has nothing to
 * draw there.
 */
function pinToViewer(spec, index) {
  if (!hasGeometry()) return;
  var sameView = spec && sceneState.explain && explainKey(sceneState.explain) === explainKey(spec);
  // Another chart of the view already showing: the viewer has nothing to change, and only
  // which chart it is for does.
  if (sameView && index !== undefined && index !== (sceneState.pinnedIndex || 0)) {
    sceneState.pinnedIndex = index;
    markPinned();
    return;
  }
  var same = sameView;
  if (!spec || same) {
    sceneState.explain = null;
    sceneState.entities = null;
    sceneState.colourBy = 'entity';
    sceneState.threshold = null;
    sceneState.wedge = null;
    sceneState.t = 0;
  } else {
    sceneState.explain = spec;
    sceneState.pinnedIndex = index || 0;
    dockState.tab = 'scene';
    sceneState.entities = new Set(explainEntities(spec));
    sceneState.threshold = null;
    sceneState.wedge = null;
    sceneState.t = 0;
    sceneState.groupAt = null;
    sceneState.explode = 0;
    sceneState.colourBy = spec.kind === 'group' ? 'group'
      : (spec.kind === 'metric' || spec.kind === 'distance' || spec.kind === 'both')
        ? 'metric' : 'entity';
    var subject = spec.kind === 'polarity' ? spec.structure : spec.entity;
    var wanted = spec.objectId || sceneState.objectId;
    var here = GEOMETRY.filter(function(o){ return objectHasStructure(o.id, subject); });
    var keep = here.find(function(o){ return String(o.id) === String(wanted); }) || here[0];
    if (keep) sceneState.objectId = keep.id;
    sceneState.maxGap = null;
  }
  stopWedge();
  markPinned();
  renderScene();
}

/**
 * Hand the viewer the contacts slider's gap, and the slider the viewer's.
 * Both ends are one number, which is the point: the groups in the chart and the colours in
 * the cell are the same groups.
 */
function contactGapUm() {
  var slider = document.getElementById('ct-thr');
  return slider ? +slider.value : null;
}

// What the chart sliders reach the open scene through: set while a scene is up.
// Told when the viewer docks beside the charts or leaves; the page redraws what depends on it.
var dockChangeListeners = [];

var _sceneApi = null;

/** The contacts slider moved: if the viewer is showing groups, it follows. */
function syncViewerGap(gap) {
  if (_sceneApi && sceneState.explain && sceneState.explain.kind === 'group') {
    _sceneApi.setGap(gap);
  }
}

/**
 * The wedge being explained, marked on the polarity rose: a shaded sector over it, so the bar
 * a reader is looking at in the chart and the shell of instances in the cell are the same
 * thing. Only the pinned chart's, and gone with it.
 */
/**
 * The cell the viewer is showing, ringed on the group plot it belongs to: its diamonds - one
 * per structure - are the viewer's picture, so the two should be findable against each other.
 */
function syncMapMarkers() {
  var spec = sceneState.explain;
  document.querySelectorAll('[data-map]').forEach(function(plot) {
    if (!plot.data || !window.Plotly) return;
    var at = plot.data.findIndex(function(t){ return t.name === '__cell'; });
    var mine = spec && spec.kind === 'polarity' && spec.aspect === 'map'
      && plot.parentNode.classList.contains('pinned')
      ? (plot._cells || {})[String(sceneState.objectId)] : null;
    if (!mine || !mine.length) {
      if (at >= 0) Plotly.deleteTraces(plot, at);
      return;
    }
    var update = { r: [mine.map(function(q){ return q.r; })], theta: [mine.map(function(q){ return q.theta; })] };
    if (at >= 0) {
      Plotly.restyle(plot, update, [at]);
    } else {
      Plotly.addTraces(plot, { type: 'scatterpolar', mode: 'markers', name: '__cell', showlegend: false,
        r: update.r[0], theta: update.theta[0], hoverinfo: 'skip',
        marker: { symbol: 'circle-open', size: 22, color: '#14181d', line: { color: '#14181d', width: 2.2 } } });
    }
  });
}

function syncRoseMarkers() {
  var spec = sceneState.explain;
  document.querySelectorAll('[data-rose]').forEach(function(plot) {
    if (!plot.data || !window.Plotly) return;
    var at = plot.data.findIndex(function(t){ return t.name === '__wedge'; });
    var live = spec && spec.kind === 'polarity' && spec.aspect === 'angle'
      && plot.parentNode.classList.contains('pinned') && sceneState.wedge !== null;
    if (!live) {
      if (at >= 0) Plotly.deleteTraces(plot, at);
      return;
    }
    var width = 180 / POLAR_ANGLE_BINS, centre = (sceneState.wedge + 0.5) * width;
    if (at >= 0) {
      Plotly.restyle(plot, { theta: [[centre]] }, [at]);
    } else {
      Plotly.addTraces(plot, { type: 'barpolar', name: '__wedge', r: [1.02], theta: [centre],
        width: [width], marker: { color: 'rgba(28, 92, 171, .14)',
                                  line: { color: '#1c5cab', width: 1.5 } },
        hoverinfo: 'skip', showlegend: false });
    }
  });
}

// ── what the viewer is showing ──────────────────────────────────────────────

var _wedgeTimer = null;

function stopWedge() { if (_wedgeTimer) { clearTimeout(_wedgeTimer); _wedgeTimer = null; } }

/** The line above the viewer: what it shows, why, and the way to change that. */
function renderDockStatus() {
  var host = document.getElementById('dock-status');
  if (!host || !host.replaceChildren) return;
  host.replaceChildren();
  // Nothing to follow or to explain in without geometry: the section says how to get some.
  if (!hasGeometry()) return;
  var add = function(tag, className, html) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (html !== undefined) el.innerHTML = html;
    host.appendChild(el);
    return el;
  };
  var button = function(text, primary, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = primary ? 'primary' : '';
    b.textContent = text;
    b.addEventListener('click', onClick);
    host.appendChild(b);
  };
  var spec = sceneState.explain;
  if (spec) {
    add('span', 'dot pinned');
    add('span', '', '<b>Explaining</b> ' + escHtml(spec.title));
    add('span', 'grow');
    button('Go to the chart', false, function() {
      var cell = document.querySelector('.chart-cell.pinned');
      if (cell) cell.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    button('Release', true, function(){ pinToViewer(spec); });
  } else {
    add('span', 'hint', 'Press <b>' + dimsWord() + '</b> on any chart to see what it measures on a ' + escHtml(noun()) + '.');
  }
}

// ── the 3D section ───────────────────────────────────────────────────────────
//
// renderScene draws it, and every piece below takes the `ctx` of that one render: the page elements it draws into, the
// object on show, the geometry loaded for it, and the chart being explained (null when none is pinned). What outlives a
// render is at module scope in scene-controls.js: the stage, the geometry cache and `sceneState`.

async function renderScene() {
  var ctx = sceneBegin();
  if (!sceneChooseObject(ctx)) return;
  sceneAcquireStage(ctx);
  // The structure a chart is about, and the ones drawn solid for it. The rest of what is
  // loaded is there to measure against and is drawn faint.
  var explain = ctx.explain;
  ctx.subject = explain ? (explain.kind === 'polarity' ? explain.structure : explain.entity) : null;
  ctx.shown = explain ? explainShown(explain) : null;
  _sceneApi = { setGap: function(gap) { return sceneSetGap(ctx, gap); } };
  await sceneDraw(ctx, true);
}

/** Clear the section for a new render, and return its context. */
function sceneBegin() {
  var ctx = {
    controls: document.getElementById('scene-controls'),
    holderWrap: document.getElementById('scene-holder'),
    noteEl: document.getElementById('scene-note'),
    explainEl: document.getElementById('scene-explain'),
    rampHost: document.getElementById('scene-scale'),
    explain: sceneState.explain,
    loaded: null,        // what was read for the object on show, and is drawn
    readoutEl: null,     // the readout under the explanation, kept so a slider can rewrite it as it moves
    stripEl: null,
    groupNames: [],     // the menu's entries, worked out on each paint
    groupNoteEl: null,  // what the chart's number is for the group picked
    groupNote: '',
    groupMenuEl: null,  // the menu choosing the group to highlight, set by sceneBuildExplain
    gapAsked: 0          // only the latest gap is drawn: dragging the chart's slider asks faster than contacts can be read
  };
  ctx.controls.replaceChildren();
  ctx.holderWrap.replaceChildren();
  ctx.explainEl.replaceChildren();
  ctx.noteEl.textContent = '';
  sceneDockLayout();
  renderDockStatus();
  var dockEl = document.getElementById('s-3d');
  if (dockEl) dockEl.classList.toggle('explaining', !!ctx.explain);
  syncDock();
  // Two renders can overlap - a second pin before the first has read anything - and the
  // older must not paint over the newer.
  var run = ++_sceneRun;
  ctx.isLatest = function() { return run === _sceneRun; };
  return ctx;
}

/** With geometry the viewer is a column of its own, beside the charts (see the stylesheet); without it, this section is
 *  where the page says how to get some. */
function sceneDockLayout() {
  var mainEl = document.getElementById('main-content');
  var wasDocked = mainEl.classList.contains('docked');
  mainEl.classList.toggle('docked', hasGeometry());
  if (wasDocked !== hasGeometry()) dockChangeListeners.forEach(function(listen) { listen(); });
  // The charts were drawn at the width the page had, and the viewer takes some of it: a
  // chart only follows the window, so the ones already drawn would run on under the card.
  if (wasDocked !== hasGeometry() && window.Plotly) {
    requestAnimationFrame(function() {
      document.querySelectorAll('.js-plotly-plot').forEach(function(plot) {
        try { Plotly.Plots.resize(plot); } catch (e) { /* not on screen */ }
      });
    });
  }
  var topbarEl = document.getElementById('topbar');
  mainEl.style.setProperty('--topbar-h', (topbarEl ? topbarEl.offsetHeight : 0) + 'px');
}

/** Pick the object on show, and the metrics it can be coloured by. False when the filter leaves none. */
function sceneChooseObject(ctx) {
  // Only the objects the filter leaves, so "the object in 3D" is one the rest of the page
  // is also about.
  ctx.objects = filteredGeometry();
  if (!ctx.objects.length) {
    geometryHint(ctx.holderWrap, 'the ' + noun() + ' is drawn here');
    return false;
  }
  ctx.current = ctx.objects.find(function(o){ return o.id === sceneState.objectId; }) || ctx.objects[0];
  sceneState.objectId = ctx.current.id;
  // Both kinds: what the geometry file carries, and what only the report does.
  ctx.fromReport = reportOnlyMetrics(ctx.current.id);
  ctx.metrics = geometryMetrics().concat(ctx.fromReport);
  if (!sceneState.metric || ctx.metrics.indexOf(sceneState.metric) < 0) sceneState.metric = ctx.metrics[0];
  return true;
}

/** Put the one stage canvas in the section. */
function sceneAcquireStage(ctx) {
  // The same canvas every time: a new one would need a new WebGL context, and a browser
  // gives up the oldest of those after a handful.
  var stageParts = _sceneHolder;
  if (stageParts) {
    ctx.holderWrap.replaceChildren(stageParts.holder);
    stageParts.holder.querySelectorAll('.stage-busy').forEach(function(el){ el.remove(); });
    stageParts.holder.style.display = '';
  } else {
    stageParts = _sceneHolder = stageCanvas(ctx.holderWrap, 430);
  }
  ctx.holder = stageParts.holder;
  ctx.canvas = stageParts.canvas;
  // Under the scene, filled by the painting - which is where the scale's ends are known,
  // since they come from the instances on screen and not from the whole report.
  ctx.rampHost.replaceChildren();
}

/** The chart's slider moved the contact gap: read the contacts for it and repaint. */
async function sceneSetGap(ctx, gap) {
  var mine = ++ctx.gapAsked;
  if (!ctx.loaded || !ctx.explain || ctx.explain.kind !== 'group') return;
  sceneState.gap = gap;
  var track = ctx.explainEl.querySelector('input[type=range]');
  if (track) {
    track.value = gap;
    track.dispatchEvent(new Event('input'));
  }
  var edges = await sceneReadEdges(ctx);
  if (mine !== ctx.gapAsked) return;
  ctx.loaded.edges = edges;
  scenePaint(ctx);
}

/**
 * What is in this object's geometry file, and how big: the cheap query. One structure can
 * contribute both row kinds (a whole-structure mask and a mesh per instance), so counts
 * are folded into one entry per name, which is what the reader toggles.
 */
async function sceneReadSummary(ctx) {
  // Keyed on the file: another object, or geometry added since, starts the cache over.
  var key = sourceOf([ctx.current]);
  if (_geomCache.key !== key) _geomCache = { key: key, summary: null, byEntity: new Map() };
  if (_geomCache.summary) return _geomCache.summary;
  var rows = await queryRows(sceneSummarySql(sourceOf([ctx.current])));
  var gaps = await queryRows(sceneGapSql(sourceOf([ctx.current])));
  var byName = new Map();
  rows.forEach(function(row) {
    var name = String(row.name);
    var entry = byName.get(name) || { name: name, instances: 0, masks: 0, faces: 0 };
    if (row.row_type === 'file') entry.masks += Number(row.n);
    else entry.instances += Number(row.n);
    entry.faces += Number(row.faces || 0);
    byName.set(name, entry);
  });
  _geomCache.summary = { entities: [...byName.values()],
                         widestGap: numOrNull((gaps[0] || {}).widest) };
  return _geomCache.summary;
}

/** Read the structures not yet in the cache for this object, decoded, with the metrics the file carries. False if a newer
 *  render took over while it was reading. */
async function sceneReadMissing(ctx, summary, missing, metricCols) {
  var source = sourceOf([ctx.current]);
  if (ctx.current.hasLevels === undefined) ctx.current.hasLevels = await geometryHasColumn(ctx.current.path, 'multires');
  var rows = await queryRows(sceneGeometrySql(source, missing, metricCols, ctx.current.hasLevels));
  if (!ctx.isLatest()) return false;
  // Every instance of every structure that is switched on, so nothing is silently
  // missing; what gives instead is how finely each one is tessellated.
  setSurfaceDetail(summary.entities
    .filter(function(e){ return sceneState.entities.has(e.name); })
    .reduce(function(n, e){ return n + e.instances + e.masks; }, 0) || rows.length);
  missing.forEach(function(name){ _geomCache.byEntity.set(name, { instances: [], masks: [] }); });
  rows.forEach(function(row) {
    var drawable = decodeDrawable(row);
    if (!drawable) return;
    var vals = {};
    metricCols.forEach(function(col){ vals[col] = numOrNull(row[col]); });
    var entry = _geomCache.byEntity.get(String(row.entity));
    (row.row_type === 'file' ? entry.masks : entry.instances).push({
      entity: String(row.entity),
      label: row.label === null || row.label === undefined ? null : String(row.label),
      vals: vals,
      polar: { dist: numOrNull(row.polar_dist_um) || 0,
               n: [numOrNull(row.polar_nx) || 0, numOrNull(row.polar_ny) || 0,
                   numOrNull(row.polar_nz) || 0] },
      geometry: drawable.geometry,
      flat: drawable.flat,
      levels: levelsOf(row.multires),
      kind: String(row.surface_kind || 'mesh')
    });
  });
  return true;
}

/** Where the centre is among the meshes, for the arrows a polarity chart is explained by; null for any other chart. */
function scenePolarFrame(ctx, instances, masks) {
  if (!(ctx.explain && ctx.explain.kind === 'polarity')) return null;
  var frame;
  var whole = ctx.explain.aspect === 'map' || ctx.explain.aspect === 'cellmap';
  var subjectsHere = whole ? polarSubjects(ctx.explain.ref) : [ctx.explain.structure];
  frame = polarityFrame(instances.concat(masks), ctx.current.id, ctx.explain.ref,
                        subjectsHere[0] || ctx.explain.structure, ctx.explain.radius,
                        subjectsHere.slice(1));
  // The wedge to begin on is the fullest: the tallest bar of the chart it explains.
  if (frame && sceneState.wedge === null) {
    var counts = new Array(POLAR_ANGLE_BINS).fill(0);
    frame.units.forEach(function(u){ counts[polarBinOf(u.angle)]++; });
    sceneState.wedge = counts.indexOf(Math.max.apply(null, counts));
  }
  return frame;
}

/** What is drawn for the object on show: the instances and masks of the structures switched on, with the contact edges and the
 *  polarity frame a chart needs. Null if a newer render took over while it was reading. */
async function sceneReadGeometry(ctx, summary) {
  var wanted = summary.entities
    .filter(function(e){ return sceneState.entities.has(e.name); })
    .map(function(e){ return e.name; });
  if (!wanted.length) return { instances: [], masks: [], edges: [] };

  // Every metric the file carries, not only the one on screen: what is read is kept, and a
  // different colouring should not mean reading it again.
  var metricCols = [...new Set(geometryMetrics().concat(
    ['polar_dist_um', 'polar_nx', 'polar_ny', 'polar_nz']))]
    .filter(function(col){ return ctx.fromReport.indexOf(col) < 0; });
  // A report-only metric is joined on by structure and label instead of selected. A chart
  // being explained always is: it plots a column of the report's own rows, which the
  // geometry file may not carry, and the join works for any of them.
  var joined = ctx.explain ? null : ctx.fromReport.indexOf(sceneState.metric) >= 0
    ? reportMetricValues(ctx.current.id, sceneState.metric) : null;
  var explained = ctx.explain ? explainValueCols(ctx.explain)
    .map(function(col){ return reportMetricValues(ctx.current.id, col); }) : null;

  // Only the structures not already read for this object.
  var missing = wanted.filter(function(name){ return !_geomCache.byEntity.has(name); });
  if (missing.length && !(await sceneReadMissing(ctx, summary, missing, metricCols))) return null;

  // What is drawn is a thin copy of what was read: the surfaces are shared, while the
  // value that colours an item and the marks a slider puts on it belong to this render.
  var instances = [], masks = [];
  var copy = function(read) {
    var key = read.entity + '|' + (read.label === null ? '' : read.label);
    var value = explained ? explainedValue(explained, key)
      : joined ? (key in joined ? joined[key] : null)
      : (read.vals[sceneState.metric] === undefined ? null : read.vals[sceneState.metric]);
    return { entity: read.entity, label: read.label, metric: value, polar: read.polar,
             // One value per column the chart reads, for a chart that reads several (a group's
             // closest approach to each of two structures).
             vals: explained ? explained.map(function(m){ return key in m ? m[key] : null; }) : null,
             geometry: read.geometry, flat: read.flat, kind: read.kind, levels: read.levels };
  };
  wanted.forEach(function(name) {
    var read = _geomCache.byEntity.get(name);
    if (!read) return;
    read.instances.forEach(function(item){ instances.push(copy(item)); });
    read.masks.forEach(function(item){ masks.push(copy(item)); });
  });

  var edges = await sceneReadEdges(ctx);
  // Where the centre is among the meshes, for the arrows a polarity chart is explained by.
  var frame = scenePolarFrame(ctx, instances, masks);
  return { instances: instances, masks: masks, edges: edges, frame: frame };
}

async function sceneReadEdges(ctx) {
  if (sceneState.colourBy !== 'group' || sceneState.gap === null) return [];
  return (await queryRows(sceneEdgesSql(sourceOf([ctx.current]), sceneState.gap)))
    .map(function(e){ return { a: e.ea + '|' + e.la, b: e.eb + '|' + e.lb }; });
}

// What the overlay of the polarity view was drawn into, so a slider can swap it alone.
// It lives on the stage, which outlasts this render: an overlay left by the last one is
// still in the scene.
function sceneClearOverlay(ctx) {
  var stage = _objectStage;
  if (!stage || !stage.overlay) return;
  stage.scene.remove(stage.overlay);
  stage.overlay.traverse(function(node) {
    if (node.geometry) node.geometry.dispose();
    if (node.material) node.material.dispose();
  });
  stage.overlay = null;
}

/**
 * Each group's closest approach to what the chart measures it against: the nearest any member
 * gets to each structure, and - for two structures - the larger of the two. Null for an
 * instance touching nothing, which is not a group, and for a group with no value.
 */
function sceneGroupReach(ctx) {
  var items = ctx.loaded.instances.filter(function(i){ return i.entity === ctx.subject; });
  var groups = contactGroups(items.map(function(i){ return i.entity + '|' + i.label; }), ctx.loaded.edges);
  var columns = (ctx.explain.metrics || []).length, best = new Map();
  items.forEach(function(item) {
    var root = groups.rootOf(item.entity + '|' + item.label);
    var row = best.get(root);
    if (!row) { row = []; for (var j = 0; j < columns; j++) row.push(Infinity); best.set(root, row); }
    (item.vals || []).forEach(function(v, j){ if (v !== null && isFinite(v) && v < row[j]) row[j] = v; });
  });
  var values = new Map();
  best.forEach(function(row, root) {
    var done = groups.sizeOfRoot(root) >= 2 && row.length && row.every(isFinite);
    values.set(root, done ? Math.max.apply(null, row) : null);
  });
  return { values: values, groups: groups };
}

/** The closest approach half of the random groups of size k get beyond: the chance level a group is scored against. */
function sceneTypicalReach(pool, k) {
  for (var i = 0; i < pool.length; i++) if (baselineScore(pool, k, pool[i]) <= 0.5) return pool[i];
  return pool[pool.length - 1];
}

/**
 * What the baseline chart plots, for each group on show: the share of same-size groups it beats
 * (the pool being this cell's instances of the structure, measured to the same target), beside its own
 * closest approach and the one a group of that size typically has. Null for a group with no value.
 */
function sceneGroupScores(ctx) {
  var reach = sceneGroupReach(ctx), pool = [], typical = {}, scores = new Map();
  ctx.loaded.instances.forEach(function(i) {
    if (i.entity === ctx.subject && i.vals && i.vals[0] !== null && isFinite(i.vals[0])) pool.push(i.vals[0]);
  });
  pool.sort(function(a, b){ return a - b; });
  reach.values.forEach(function(v, root) {
    var k = reach.groups.sizeOfRoot(root), score = v === null ? null : baselineScore(pool, k, v);
    if (score === null) { scores.set(root, null); return; }
    if (!(k in typical)) typical[k] = sceneTypicalReach(pool, k);
    scores.set(root, { score: score * 100, reach: v, size: k, typical: typical[k] });
  });
  return { scores: scores, groups: reach.groups };
}

/**
 * Which instances a chart's slider has put out of play.
 *
 * A chart about "at most this far" or "within this angle" is a count of the instances
 * that qualify, so the ones that do not are drawn faint and kept: they are
 * still there, and where they sit is part of the picture.
 */
function sceneApplyThreshold(ctx) {
  if (!ctx.explain || !ctx.loaded) return;
  var subjects = ctx.loaded.instances.filter(function(i){ return i.entity === ctx.subject; });
  subjects.forEach(function(item){ item.dim = false; });
  if (ctx.explain.kind === 'polarity') {
    // Only the rose puts instances out of play - those outside the wedge. The others turn
    // the whole cell into a chart, and leave none behind.
    if (!ctx.loaded.frame || ctx.explain.aspect !== 'angle') return;
    var counted = new Set();
    ctx.loaded.frame.units.forEach(function(u) {
      if (!u.item) return;
      u.item.dim = polarBinOf(u.angle) !== sceneState.wedge;
      counted.add(u.item);
    });
    // Without a direction there is nothing to be inside the wedge.
    subjects.forEach(function(item){ if (!counted.has(item)) item.dim = true; });
  } else if (ctx.explain.kind === 'group' && (ctx.explain.aspect === 'reach' || ctx.explain.aspect === 'both'
                                          || ctx.explain.aspect === 'baseline')) {
    // Only groups have a closest approach: what touches nothing is out of play, and so is a
    // group further than the slider's value.
    var reach = sceneGroupReach(ctx);
    subjects.forEach(function(item) {
      var v = reach.values.get(reach.groups.rootOf(item.entity + '|' + item.label));
      item.dim = v === null || v === undefined || (sceneState.threshold !== null && v > sceneState.threshold);
    });
  } else if (sceneState.colourBy === 'metric') {
    // An instance with no value is faint, never painted a colour of the scale; and past the
    // slider's value it is faint too.
    subjects.forEach(function(item) {
      item.dim = item.metric === null || !isFinite(item.metric)
        || (sceneState.threshold !== null && item.metric > sceneState.threshold);
    });
  }
}

/** Draw the polarity arrows and cone over the meshes. Cheap, so a slider can call it live. */
function scenePaintOverlay(ctx, andDraw) {
  var stage = _objectStage;
  if (!stage || !ctx.loaded || !ctx.isLatest()) return;
  sceneClearOverlay(ctx);
  if (ctx.explain && ctx.explain.kind === 'polarity' && ctx.loaded.frame) {
    var colours = {};
    polarSubjects(ctx.explain.ref).concat([ctx.explain.ref]).forEach(function(n){ colours[n] = litEntColour(n); });
    stage.overlay = polarityOverlay(stage.THREE, ctx.loaded.frame, {
      aspect: ctx.explain.aspect, wedge: sceneState.wedge || 0, t: sceneState.t,
      planar: PLANAR, radiusMode: ctx.explain.radius, colour: litEntColour(ctx.explain.structure),
      colours: colours, refName: ctx.explain.ref });
    stage.scene.add(stage.overlay);
  }
  if (andDraw !== false) stage.draw();
}

/**
 * Turn the cell into the chart, as far as the slider says: the whole cell rotated until the
 * chart's plane faces the camera, the cell itself fading as the points that stand for it
 * take over, and the camera swinging round to look at it square on. Put back, camera
 * included, when the slider is.
 */
function sceneApplyMorph(ctx) {
  var stage = _objectStage;
  if (!stage) return;
  var morph = ctx.explain && ctx.explain.kind === 'polarity' && POLAR_FLATTENS[ctx.explain.aspect]
    && ctx.loaded && ctx.loaded.frame;
  if (!morph) {
    stage.setPose(null);
    stage.fade(1);
    if (stage.morphFrom) { stage.view(stage.morphFrom, false); stage.morphFrom = null; }
    return;
  }
  var t = sceneState.t;
  stage.setPose(polarMorphPose(stage.THREE, ctx.loaded.frame, t));
  stage.fade(1 - Math.min(1, t * 2));
  if (t > 0 && !stage.morphFrom) stage.morphFrom = stage.view();
  if (!stage.morphFrom) return;
  if (t === 0) {
    stage.view(stage.morphFrom, false);
    stage.morphFrom = null;
  } else {
    // Round the short way, to straight on: the plane's normal is the camera's z.
    var from = stage.morphFrom;
    var turn = ((-from.theta + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    stage.view({ theta: from.theta + turn * t, phi: from.phi + (Math.PI / 2 - from.phi) * t },
               false);
  }
}

/** A slider moved a polarity view: redraw what is over the cell and turn it, no more. */
function sceneRefreshPolar(ctx) {
  var stage = _objectStage;
  if (!stage || !ctx.loaded || !ctx.isLatest()) return;
  scenePaintOverlay(ctx, false);
  sceneApplyMorph(ctx);
  stage.draw();
}

/** Colour by the value of a metric, on a ramp whose ends are the instances on screen. */
function sceneColourByMetric(ctx, inSubject) {
  var explain = ctx.explain, colourOf;
  var values = inSubject.map(function(i){ return i.metric; }).filter(function(v){ return v !== null && isFinite(v); });
  var low = Math.min.apply(null, values), high = Math.max.apply(null, values);
  var span = high - low || 1;
  var byDistance = !!explain && (explain.kind === 'distance' || explain.kind === 'both');
  var ramp = byDistance ? distRampAt : viridisAt;
  colourOf = function(item) {
    return isFinite(item.metric) && item.metric !== null
      ? ramp((item.metric - low) / span) : [0.4, 0.4, 0.4].map(toLinear);
  };
  // Which colour was which value, from the instances actually on screen.
  if (values.length) {
    ctx.rampHost.appendChild(colourRamp({
      label: explain && explain.valueLabel ? explain.valueLabel : labelFor(sceneState.metric),
      low: low, high: high, ramp: byDistance ? 'distance' : 'viridis' }));
  }
  return colourOf;
}

/** Colour by contact group: by size, by how close a group gets to a structure, or in-or-out of a group. The group on show
 *  keeps its colour and every other instance goes grey. */
function sceneColourByGroup(ctx, inSubject) {
  var explain = ctx.explain, colourOf, offFocus = function(){ return false; };
  var keys = inSubject.map(function(i){ return i.entity + '|' + i.label; });
  var groups = contactGroups(keys, ctx.loaded.edges);
  var aspect = explain && explain.kind === 'group' ? explain.aspect : 'size';
  var alone = [0.34, 0.37, 0.42].map(toLinear);
  var rootOfItem = function(item){ return groups.rootOf(item.entity + '|' + item.label); };
  if (aspect === 'share') {
    // In a group or not, and nothing more: the chart is the share that are.
    var own = toLinearRgb(litEntColour(ctx.subject));
    colourOf = function(item){ return groups.sizeOfRoot(rootOfItem(item)) < 2 ? alone : own; };
  } else if (aspect === 'baseline') {
    // The chart's own number: the share of same-size groups this one beats.
    var scored = sceneGroupScores(ctx);
    colourOf = function(item) {
      var s = scored.scores.get(rootOfItem(item));
      return s ? viridisAt(s.score / 100) : alone;
    };
    ctx.rampHost.appendChild(colourRamp({ label: explain.valueLabel, low: 0, high: 100, whole: true }));
  } else if (aspect === 'reach' || aspect === 'both') {
    // How close a group gets to the structure, on the hue-free ramp.
    var reach = sceneGroupReach(ctx), all = [];
    reach.values.forEach(function(v){ if (v !== null) all.push(v); });
    var lowest = Math.min.apply(null, all), highest = Math.max.apply(null, all);
    colourOf = function(item) {
      var v = reach.values.get(rootOfItem(item));
      return v === null || v === undefined ? alone : distRampAt((v - lowest) / ((highest - lowest) || 1));
    };
    if (all.length) {
      ctx.rampHost.appendChild(colourRamp({ label: explain.valueLabel, low: lowest, high: highest, ramp: 'distance' }));
    }
  } else {
    // By size, on the report's one colour scale: what the group charts plot is how large
    // groups are, and a palette of arbitrary colours says only that there are several. A
    // single network that holds nearly everything is then one colour, and the small groups
    // beside it are another.
    var biggest = 2;
    keys.forEach(function(k){ biggest = Math.max(biggest, groups.sizeOfRoot(groups.rootOf(k))); });
    var span = Math.log(biggest / 2) || 1;
    colourOf = function(item) {
      var size = groups.sizeOfRoot(rootOfItem(item));
      // A singleton touches nothing: grey, so the groups are what stands out.
      if (size < 2) return alone;
      return viridisAt(biggest > 2 ? Math.log(size / 2) / span : 0.5);
    };
    ctx.rampHost.appendChild(colourRamp({ label: 'Instances in the group (log scale)', low: 2, high: biggest, whole: true }));
  }
  // Every group is coloured by the metric; the menu under the scene picks one to stand out, and
  // every other instance of the structure goes faint.
  if (explain && explain.kind === 'group' && aspect !== 'share') {
    var order = [], seen = {};
    keys.forEach(function(k) {
      var root = groups.rootOf(k);
      if (!seen[root] && groups.sizeOfRoot(root) >= 2) { seen[root] = true; order.push(root); }
    });
    var scoredGroups = aspect === 'baseline' ? sceneGroupScores(ctx).scores : null;
    var rank = function(root) {
      if (scoredGroups) return scoredGroups.get(root) ? -scoredGroups.get(root).score : Infinity;
      if (aspect === 'size' || !aspect) return -groups.sizeOfRoot(root);
      var v = sceneGroupReach(ctx).values.get(root);
      return v === null || v === undefined ? Infinity : v;
    };
    order.sort(function(a, b){ return rank(a) - rank(b); });
    // The groups change with the contact gap: a pick that no longer exists falls back to all.
    if (sceneState.groupAt !== null && sceneState.groupAt >= order.length) sceneState.groupAt = null;
    if (sceneState.groupAt !== null) {
      var focus = order[sceneState.groupAt];
      offFocus = function(item) { return item.entity === ctx.subject && rootOfItem(item) !== focus; };
    }
    ctx.groupNames = order.map(function(root, i) {
      var beaten = scoredGroups && scoredGroups.get(root);
      return 'Group ' + (i + 1) + ' \u00b7 ' + groups.sizeOfRoot(root).toLocaleString() + ' instances'
        + (beaten ? ' \u00b7 beats ' + Math.round(beaten.score) + '%' : '');
    });
    ctx.groupNote = scoredGroups ? sceneBaselineNote(ctx, order, scoredGroups) : '';
    sceneFillGroupMenu(ctx);
  }
  return { colourOf: colourOf, offFocus: offFocus };
}

/** How each instance is coloured: by structure, by a metric, or by contact group, and which ones sit out of the group on show.
 *  Fills the scale under the scene as a side effect, since its ends come from the instances on screen. */
function sceneColouring(ctx) {
  // Colours come from the report's full structure list, not the drawn subset, so turning a
  // structure off does not recolour the ones left.
  var colourOf;
  var offFocus = function(){ return false; };
  ctx.rampHost.replaceChildren();
  var inSubject = ctx.loaded.instances.filter(function(item){
    return !ctx.explain || item.entity === ctx.subject;
  });
  if (sceneState.colourBy === 'metric') {
    colourOf = sceneColourByMetric(ctx, inSubject);
  } else if (sceneState.colourBy === 'group') {
    var byGroup = sceneColourByGroup(ctx, inSubject);
    colourOf = byGroup.colourOf;
    offFocus = byGroup.offFocus;
  } else if (ctx.explain && ctx.explain.kind === 'polarity' && ctx.explain.aspect === 'angle') {
    // What lies in the wedge is the subject of the picture, so it is in a strong colour of its own and the wedge is behind it.
    var inWedge = toLinearRgb(POLAR_FOCUS_COLOUR);
    colourOf = function(item){ return item.entity === ctx.subject ? inWedge : toLinearRgb(litEntColour(item.entity)); };
  } else {
    colourOf = function(item){ return toLinearRgb(litEntColour(item.entity)); };
  }
  return { colourOf: colourOf, offFocus: offFocus };
}

/** What a painted item looks like: its colour, whether it is faint, and whether it is what a distance is measured to. */
function sceneStyle(ctx, colouring) {
  var explain = ctx.explain, colourOf = colouring.colourOf, offFocus = colouring.offFocus;
  // Faint where the chart being explained is not about it: the structures it only
  // measures against, and whatever a slider has put out of play.
  var ghostly = function(item) {
    return !!explain && (!ctx.shown.has(item.entity) || item.dim === true || offFocus(item));
  };
  var asItem = function(item) {
    return { geometry: item.geometry, kind: item.kind, flat: item.flat, rgb: colourOf(item),
             offset: explodeOffset(item.polar, sceneState.explode), levels: item.levels, key: item.entity + '|' + item.label };
  };
  // Put out of play by a slider: grey. Only measured against: its own colour, faint.
  var asGhost = function(item) {
    var out = asItem(item);
    out.rgb = item.dim === true || offFocus(item) ? [0.62, 0.64, 0.68].map(toLinear)
      : toLinearRgb(litEntColour(item.entity));
    return out;
  };
  // What a distance is measured to: its own colour (the one it has in every legend), see-through
  // so the instances measured can be seen against it, and unmistakably there.
  var isTarget = function(item){ return !!explain && (explain.targets || []).indexOf(item.entity) >= 0; };
  return { colourOf: colourOf, ghostly: ghostly, asItem: asItem, asGhost: asGhost, isTarget: isTarget };
}

/** The instances: solid, faint where the chart is not about them, and what a distance is measured to see-through. */
function sceneAddInstances(ctx, stage, style) {
  var THREE = stage.THREE;
  var solid = ctx.loaded.instances.filter(function(i){ return !i.flat && !style.ghostly(i); })
    .map(style.asItem);
  var faint = ctx.loaded.instances.filter(function(i){ return !i.flat && style.ghostly(i) && !style.isTarget(i); })
    .map(style.asGhost);
  var targetItems = ctx.loaded.instances.filter(function(i){ return !i.flat && style.isTarget(i); })
    .map(function(i) { var o = style.asItem(i); o.rgb = toLinearRgb(REFERENCE_COLOUR); return o; });

  // A plane has no surface to shade: same merge, built as LineSegments.
  var surfaces = mergeInstances(THREE, solid);
  if (surfaces) {
    var mesh = new THREE.Mesh(surfaces, new THREE.MeshPhongMaterial({
      vertexColors: true, side: THREE.DoubleSide, flatShading: false,
      // A grey specular is what makes a saturated surface look chalky: it adds the same
      // amount to all three channels, so the brightest faces - the ones being looked at -
      // lose their chroma. Measured on the old value, the highlights came back at a third
      // of the saturation they were handed. Nearly black keeps the sheen and the colour.
      shininess: 14, specular: 0x0d0d10
    }));
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    stage.scene.add(mesh);
    // The big meshes in it have levels of detail: refine them where the camera is close enough to tell.
    multiresAttach(ctx, stage, solid, mesh);
  }
  var faintSurfaces = mergeInstances(THREE, faint);
  if (faintSurfaces) {
    stage.scene.add(new THREE.Mesh(faintSurfaces, new THREE.MeshPhongMaterial({
      vertexColors: true, side: THREE.DoubleSide, shininess: 14, specular: 0x0d0d10,
      transparent: true, opacity: 0.16, depthWrite: false })));
  }
  var outlines = mergeInstances(THREE, ctx.loaded.instances
    .filter(function(i){ return i.flat && !style.ghostly(i); }).map(style.asItem));
  if (outlines) {
    stage.scene.add(drawnLines(THREE, outlines));
  }
  var targetSurfaces = mergeInstances(THREE, targetItems);
  if (targetSurfaces) {
    stage.scene.add(new THREE.Mesh(targetSurfaces, new THREE.MeshPhongMaterial({
      vertexColors: true, side: THREE.DoubleSide, shininess: 20, specular: 0x0d0d10,
      transparent: true, opacity: 0.5, depthWrite: false })));
  }
  var faintOutlines = mergeInstances(THREE, ctx.loaded.instances
    .filter(function(i){ return i.flat && style.ghostly(i); }).map(style.asGhost));
  if (faintOutlines) {
    stage.scene.add(drawnLines(THREE, faintOutlines, 0.3));
  }
}

/** The whole-structure masks. Only the one that bounds the object is see-through; the rest are shapes of their own. */
function sceneAddMasks(ctx, stage, style) {
  var THREE = stage.THREE, explain = ctx.explain;
  // Only the mask that bounds the object is see-through, and it has to be: it encloses
  // everything else, so drawn solid it is the only thing you would see. The other
  // whole-structure masks are shapes in their own right and are drawn like the instances
  // are - solid, shaded, and lit. Never merged, so each keeps its own material.
  // In a plane a boundary line does the enclosing job without any transparency.
  var bounding = (OBJECTS[0] || {}).object_mask_name;
  // The structure with the fewest vertices is drawn nearest: a part (sheets, tubules) shows over the whole it sits in (the ER).
  var bySize = ctx.loaded.masks.slice().sort(function(a, b){ return b.geometry.positions.length - a.geometry.positions.length; });
  ctx.loaded.masks.forEach(function(mask) {
    var rank = bySize.indexOf(mask);
    var geometry = mergeInstances(THREE, [{ geometry: mask.geometry,
      rgb: explain ? toLinearRgb(((explain.targets || []).indexOf(mask.entity) >= 0) ? REFERENCE_COLOUR : litEntColour(mask.entity)) : style.colourOf(mask) }]);
    if (!geometry) return;
    var asContext = style.ghostly(mask);
    if (mask.flat) {
      stage.scene.add(drawnLines(THREE, geometry, asContext ? 0.3 : 0.55));
      return;
    }
    var encloses = bounding != null && mask.entity === String(bounding);
    var seeThrough = encloses || asContext;
    var targeted = !!explain && (explain.targets || []).indexOf(mask.entity) >= 0;
    var surface = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
      vertexColors: true, side: THREE.DoubleSide,
      shininess: 14, specular: 0x0d0d10,
      transparent: seeThrough, opacity: encloses ? (targeted ? 0.3 : 0.12) : targeted ? 0.5 : asContext ? 0.16 : 1,
      depthWrite: !seeThrough,
      // Structures that share a membrane (ER and its sheets and tubules) put surfaces in the same place, and the depth buffer
      // then picks a winner pixel by pixel. The camera's depth range gives about a nanometre per unit here, and the meshes of one
      // membrane differ by some tens of nanometres, so a step of a few dozen units makes the smallest structure win where they
      // meet. More than that buries whatever lies behind a surface by that much.
      polygonOffset: true, polygonOffsetFactor: -rank, polygonOffsetUnits: -40 * rank
    }));
    if (!seeThrough) { surface.castShadow = true; surface.receiveShadow = true; }
    stage.scene.add(surface);
    // A big mask has levels of detail too: refined where the camera is close, like the instances.
    if (!mask.flat && mask.levels) multiresAttach(ctx, stage, [{ levels: mask.levels, key: mask.entity + '|mask', offset: [0, 0, 0],
      rgb: style.colourOf(mask) }], surface);
  });
}

/** Paint what is loaded, with the current colouring and explode factor. */
function scenePaint(ctx) {
  var stage = _objectStage;
  if (!stage || !ctx.loaded || !ctx.isLatest()) return;
  multiresReset(stage);
  stage.clear();
  sceneApplyThreshold(ctx);
  var style = sceneStyle(ctx, sceneColouring(ctx));
  sceneAddInstances(ctx, stage, style);
  sceneAddMasks(ctx, stage, style);

  scenePaintOverlay(ctx, false);
  sceneApplyMorph(ctx);
  stage.frameShadow();
  stage.draw();
  // Still pictures spin gently; a chart being explained holds still, so what the slider
  // does is the only thing that moves.
  stage.spin(!ctx.explain);
  sceneWriteExplain(ctx);
}

/** The range of what a metric-coloured chart plots, over the instances on screen. */
function sceneSubjectValues(ctx) {
  return ctx.loaded ? ctx.loaded.instances.filter(function(i){ return i.entity === ctx.subject; })
    .map(function(i){ return i.metric; })
    .filter(function(v){ return v !== null && isFinite(v); }) : [];
}

/** The groups on screen: their sizes, largest first, and how many instances touch nothing. */
function sceneCurrentGroups(ctx) {
  var keys = ctx.loaded.instances.filter(function(i){ return i.entity === ctx.subject; })
    .map(function(i){ return i.entity + '|' + i.label; });
  var groups = contactGroups(keys, ctx.loaded.edges);
  var seen = new Map();
  keys.forEach(function(k) {
    var root = groups.rootOf(k);
    seen.set(root, groups.sizeOfRoot(root));
  });
  var sizes = [], alone = 0;
  seen.forEach(function(size){ if (size < 2) alone++; else sizes.push(size); });
  sizes.sort(function(x, y){ return y - x; });
  return { sizes: sizes, alone: alone, total: keys.length };
}

/** The readout of a polarity chart: how many structures lie where, and R and V for the object. */
function scenePolarityReadout(ctx, live) {
  var named = dataName(ctx.subject || '');
  var frame = ctx.loaded.frame;
  if (!frame) return 'Nothing to measure from: ' + escHtml(ctx.explain.ref)
    + ' has no direction from the centre in this ' + noun() + '.';
  var total = frame.units.length.toLocaleString();
  var indices = 'R <b>' + (frame.R === null ? '–' : frame.R.toFixed(2)) + '</b>, V <b>'
    + (frame.V === null ? '–' : frame.V.toFixed(2)) + '</b> for this ' + noun();
  if (ctx.explain.aspect === 'angle') {
    var wedge = live === undefined ? sceneState.wedge : live;
    var width = 180 / POLAR_ANGLE_BINS;
    var here = frame.units.filter(function(u){ return polarBinOf(u.angle) === wedge; });
    var whole = frame.units.reduce(function(n, u){ return n + u.weight; }, 0);
    var share = here.reduce(function(n, u){ return n + u.weight; }, 0) / (whole || 1);
    return '<b>' + here.length.toLocaleString() + '</b> of <b>' + total + '</b> ' + named
      + ' lie between <b>' + Math.round(wedge * width) + '°</b> and <b>'
      + Math.round((wedge + 1) * width) + '°</b> of ' + dataName(ctx.explain.ref) + ', <b>'
      + fmtPct(share, 1) + '</b> of them by size: the height of this wedge<br>' + indices;
  }
  if (ctx.explain.aspect === 'index') return '<b>' + total + '</b> ' + named + '<br>' + indices;
  if (ctx.explain.aspect === 'where') {
    return '<b>' + total + '</b> ' + named + ', each at its angle to ' + dataName(ctx.explain.ref)
      + ' and its distance from the centre';
  }
  var counts = frame.groups.filter(function(g){ return g.units.length; }).map(function(g) {
    return '<b>' + g.units.length.toLocaleString() + '</b> ' + escHtml(g.name);
  });
  return counts.join(' \u00b7 ') + '<br>in ' + escHtml(String(ctx.current.id))
    + ', each structure one diamond in the plot';
}

/** The readout of a contact-group chart: how many instances are in a group, and how close the groups get. */
function sceneGroupReadout(ctx, live) {
  var named = dataName(ctx.subject || '');
  var gap = live === undefined ? sceneState.gap : live;
  var found = sceneCurrentGroups(ctx);
  var grouped = found.sizes.reduce(function(n, v){ return n + v; }, 0);
  var largest = found.sizes[0] || 0;
  var within = '<b>' + fmtGap(gap) + ' \u00b5m</b>';
  if (ctx.explain.aspect === 'share') {
    return '<b>' + grouped.toLocaleString() + '</b> of <b>' + found.total.toLocaleString() + '</b> ' + named
      + ' (<b>' + fmtPct(grouped, found.total) + '</b>) are in a group when touching means within ' + within
      + '<br><b>' + found.alone.toLocaleString() + '</b> touch nothing';
  }
  if (ctx.explain.aspect === 'reach' || ctx.explain.aspect === 'both' || ctx.explain.aspect === 'baseline') {
    var reach = sceneGroupReach(ctx), values = [];
    reach.values.forEach(function(v){ if (v !== null) values.push(v); });
    if (!values.length) return 'No group of ' + named + ' has a measured distance here.';
    var at = live !== undefined && ctx.explain.__sliding ? live : (sceneState.threshold === null ? Math.max.apply(null, values) : sceneState.threshold);
    var close = values.filter(function(v){ return v <= at; }).length;
    return '<b>' + close.toLocaleString() + '</b> of <b>' + values.length.toLocaleString() + '</b> groups come within <b>'
      + fmtValue(at) + ' \u00b5m</b><br>groups formed at ' + within + '; the nearest gets to <b>' + fmtValue(Math.min.apply(null, values))
      + ' \u00b5m</b>';
  }
  return '<b>' + grouped.toLocaleString() + '</b> of <b>' + found.total.toLocaleString()
    + '</b> ' + named + ' touch another within ' + within + ', in <b>'
    + found.sizes.length.toLocaleString() + '</b> group' + (found.sizes.length === 1 ? '' : 's')
    + (largest ? '<br>the largest holds <b>' + largest.toLocaleString() + '</b> ('
        + fmtPct(largest, found.total) + ')' : '');
}

/** What the chart's number is, counted on this object, as the slider currently has it. */
function sceneReadoutHtml(ctx, live) {
  if (!ctx.explain || !ctx.loaded) return '';
  var named = dataName(ctx.subject || '');
  if (ctx.explain.kind === 'polarity') return scenePolarityReadout(ctx, live);
  if (ctx.explain.kind === 'group') return sceneGroupReadout(ctx, live);
  if (ctx.explain.kind === 'structure') return '';
  var values = sceneSubjectValues(ctx);
  if (!values.length) return 'None of the ' + named + ' here has this measurement.';
  var top = Math.max.apply(null, values);
  var at = live !== undefined ? live : (sceneState.threshold === null ? top : sceneState.threshold);
  var under = values.filter(function(v){ return v <= at; }).length;
  return '<b>' + under.toLocaleString() + '</b> of <b>' + values.length.toLocaleString()
    + '</b> ' + named + ' at or below <b>' + fmtValue(at) + (ctx.explain.unit ? ' ' + ctx.explain.unit : '')
    + '</b>';
}

/**
 * One bar per group, tallest first, in the colour that group has in the cell: the very
 * values the group-size chart plots, set out so a bar can be matched to a blob.
 */
function sceneDrawGroupStrip(ctx) {
  if (!ctx.stripEl || !ctx.loaded) return;
  var found = sceneCurrentGroups(ctx);
  var width = ctx.stripEl.clientWidth || 300, height = 64, ratio = window.devicePixelRatio || 1;
  ctx.stripEl.width = width * ratio; ctx.stripEl.height = height * ratio;
  ctx.stripEl.style.height = height + 'px';
  var g = ctx.stripEl.getContext('2d');
  g.scale(ratio, ratio);
  g.clearRect(0, 0, width, height);
  var aspect = ctx.explain.aspect;
  if (aspect === 'share') {
    // The two parts of the share, as one bar: in a group, and touching nothing.
    var inGroup = found.sizes.reduce(function(n, v){ return n + v; }, 0);
    var wide = found.total ? width * inGroup / found.total : 0;
    g.fillStyle = litEntColour(ctx.subject); g.fillRect(0, 16, wide, 32);
    g.fillStyle = '#586070'; g.fillRect(wide, 16, width - wide, 32);
    return;
  }
  if (aspect === 'baseline') {
    // One bar per group, as tall as the share of same-size groups it beats.
    var beaten = [];
    sceneGroupScores(ctx).scores.forEach(function(v){ if (v) beaten.push(v.score); });
    beaten.sort(function(x, y){ return y - x; });
    beaten = beaten.slice(0, 120);
    var gapB = beaten.length > 60 ? 0 : 1, eachB = Math.max(1.5, (width - gapB * (beaten.length - 1)) / (beaten.length || 1));
    beaten.forEach(function(v, i) {
      g.fillStyle = viridisHexAt(v / 100);
      g.fillRect(i * (eachB + gapB), height - Math.max(3, v / 100 * (height - 4)), eachB, Math.max(3, v / 100 * (height - 4)));
    });
    return;
  }
  if (aspect === 'reach' || aspect === 'both') {
    var values = [];
    sceneGroupReach(ctx).values.forEach(function(v){ if (v !== null) values.push(v); });
    values.sort(function(x, y){ return x - y; });
    values = values.slice(0, 120);
    if (!values.length) return;
    var top = values[values.length - 1] || 1, first = values[0];
    var gapPx = values.length > 60 ? 0 : 1, each = Math.max(1.5, (width - gapPx * (values.length - 1)) / values.length);
    values.forEach(function(v, i) {
      var h = Math.max(3, (v / top) * (height - 4));
      g.fillStyle = distRampHexAt((v - first) / ((top - first) || 1));
      g.fillRect(i * (each + gapPx), height - h, each, h);
    });
    return;
  }
  var sizes = found.sizes.slice(0, 90);
  if (!sizes.length) return;
  var big = sizes[0], span = Math.log(big / 2) || 1, gap = sizes.length > 45 ? 1 : 2;
  var each2 = Math.max(2, (width - gap * (sizes.length - 1)) / sizes.length);
  sizes.forEach(function(size, i) {
    var h = Math.max(2, (Math.log(size) / Math.log(big)) * (height - 4));
    g.fillStyle = viridisHexAt(big > 2 ? Math.log(size / 2) / span : 0.5);
    g.fillRect(i * (each2 + gap), height - h, each2, h);
  });
}

/** Rewrite the readout in place; called on every repaint and live from the sliders. */
function sceneWriteExplain(ctx, live) {
  if (ctx.readoutEl) ctx.readoutEl.innerHTML = sceneReadoutHtml(ctx, live);
  sceneDrawGroupStrip(ctx);
}

/** What is drawn, in what: the boundary, the structure being measured and the one it is measured to, in the colours they have
 *  everywhere. */
function sceneExplainLegend(ctx) {
  // What is drawn, in what: the cell's boundary, the structure being measured and - for a
  // distance - the one it is measured to, in the colour that structure has everywhere.
  var legend = document.createElement('div');
  legend.className = 'explain-key';
  var chip = function(colour, words, outline) {
    return '<span><i class="sw" style="background:' + colour + (outline ? ';box-shadow:0 0 0 1px rgba(20,24,29,.3)' : '')
      + '"></i>' + words + '</span>';
  };
  var parts = [];
  if (ctx.explain.kind === 'distance' || ctx.explain.kind === 'both') {
    parts.push('<span><i class="sw ramp ' + (ctx.explain.kind === 'metric' ? 'viridis' : 'lum')
      + '"></i>' + escHtml(ctx.explain.entity) + (ctx.explain.kind === 'metric' ? ', by value' : ', by distance \u2013 magenta is near, cyan is far') + '</span>');
  }
  (ctx.explain.targets || []).forEach(function(t) {
    parts.push(chip(REFERENCE_COLOUR, escHtml(t) + ' \u2013 measured to, drawn see-through'));
  });
  if (ctx.explain.kind === 'group') {
    var a2 = ctx.explain.aspect;
    if (a2 === 'share') parts.push(chip(litEntColour(ctx.explain.entity), escHtml(ctx.explain.entity) + ' in a group'));
    else if (a2 === 'size' || !a2) parts.push('<span><i class="sw ramp viridis"></i>groups, by size</span>');
    else if (a2 === 'baseline') parts.push('<span><i class="sw ramp viridis"></i>groups, by share of same-size groups beaten \u2013 cyan beats many, magenta few</span>');
    else parts.push('<span><i class="sw ramp lum"></i>groups, by closest approach \u2013 magenta is near</span>');
    parts.push(chip(ctx.explain.aspect === 'share' ? '#586070' : '#c5c9d0', ctx.explain.aspect === 'share' ? 'touches nothing' : 'faint: in no group, past the slider, or not highlighted', ctx.explain.aspect !== 'share'));
  }
  if (ctx.explain.kind === 'structure') parts.push(chip(litEntColour(ctx.explain.entity), escHtml(ctx.explain.entity)));
  if (ctx.loaded && ctx.explain.kind !== 'metric' && (ctx.explain.targets || []).indexOf(boundingMaskName()) < 0
      && ctx.loaded.masks.concat(ctx.loaded.instances).some(function(i){ return i.entity === boundingMaskName(); })) {
    parts.push(chip(litEntColour(boundingMaskName()), 'the ' + noun() + ' boundary', true));
  }
  legend.innerHTML = parts.join('');
  ctx.explainEl.appendChild(legend);
}

/** The wedges of the rose are a sequence the cell is walked through, so it plays by itself; the strip is the rose's own bar
 *  heights, and pressing a bar stops on that wedge. */
function sceneWedgeRail(ctx) {
  // Not a slider: the wedges are a sequence the cell is walked through, so it plays by itself,
  // and the strip below is the rose's own bar heights - press a bar to stop on that wedge.
  var binWidth = 180 / POLAR_ANGLE_BINS;
  var rail = document.createElement('div');
  rail.className = 'wedge-rail';
  var play = document.createElement('button');
  play.type = 'button';
  play.className = 'wedge-play';
  var bars = document.createElement('div');
  bars.className = 'wedge-bars';
  var shares = [], whole = ctx.loaded.frame.units.reduce(function(n, u){ return n + u.weight; }, 0) || 1;
  for (var w = 0; w < POLAR_ANGLE_BINS; w++) shares.push(0);
  ctx.loaded.frame.units.forEach(function(u){ shares[polarBinOf(u.angle)] += u.weight / whole; });
  var tallest = Math.max.apply(null, shares) || 1;
  var buttons = shares.map(function(share, i) {
    var bar = document.createElement('button');
    bar.type = 'button';
    bar.className = 'wedge-bar';
    bar.title = Math.round(i * binWidth) + '\u00b0\u2013' + Math.round((i + 1) * binWidth) + '\u00b0: '
      + fmtPct(share, 1) + ' of the ' + ctx.explain.structure + ', by size';
    bar.setAttribute('aria-label', bar.title);
    var fill = document.createElement('i');
    fill.style.height = Math.max(6, 100 * share / tallest) + '%';
    bar.appendChild(fill);
    bar.addEventListener('click', function() { pauseWedge(true); goToWedge(i); });
    bars.appendChild(bar);
    return bar;
  });
  var show = function() {
    var w2 = sceneState.wedge || 0;
    buttons.forEach(function(bar, i){ bar.classList.toggle('on', i === w2); });
    play.textContent = sceneState.wedgePaused ? '\u25b6' : '\u275a\u275a';
    play.title = sceneState.wedgePaused ? 'Play: step through the wedges' : 'Pause';
    play.setAttribute('aria-label', play.title);
  };
  var goToWedge = function(i) {
    sceneState.wedge = i;
    show();
    scenePaint(ctx);
    sceneWriteExplain(ctx);
    syncRoseMarkers();
  };
  var pauseWedge = function(on) { sceneState.wedgePaused = on; stopWedge(); if (!on) startWedge(); show(); };
  // One step at a time: the next is scheduled when this one has finished, never on a clock that
  // can run ahead of it. And if a step is slow - a machine that cannot draw the cell several times
  // a second - the walk stops itself and the page never freezes; the bars still work.
  var startWedge = function() {
    stopWedge();
    if (sceneState.wedgePaused) return;
    var step = function() {
      _wedgeTimer = null;
      if (!document.body.contains(rail)) return;
      if (!document.hidden) {
        var began = performance.now();
        goToWedge(((sceneState.wedge || 0) + 1) % POLAR_ANGLE_BINS);
        if (performance.now() - began > 1200) { sceneState.wedgePaused = true; show(); return; }
      }
      _wedgeTimer = setTimeout(step, 1400);
    };
    _wedgeTimer = setTimeout(step, 1400);
  };
  play.addEventListener('click', function(){ pauseWedge(!sceneState.wedgePaused); });
  // Motion is the explanation here, but it is never forced on someone who has asked for less of it.
  if (sceneState.wedgePaused === undefined || sceneState.wedgePaused === null) {
    sceneState.wedgePaused = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  rail.append(play, bars);
  ctx.explainEl.appendChild(rail);
  show();
  startWedge();
}

/** The slider that turns the cell into the chart. */
function sceneTurnSlider(ctx, aspect) {
  ctx.explainEl.appendChild(slider('Showing', {
    min: 0, max: 1, step: 0.01, value: sceneState.t,
    format: function(v) {
      if (aspect === 'map') {
        return v < 0.005 ? 'the ' + noun() : v > 0.995 ? 'the plot, one diamond per structure'
          : v < 0.6 ? 'turning the ' + noun() + ' into the plot'
          : 'gathering each structure into its diamond';
      }
      return v < 0.005 ? 'the ' + noun() : v > 0.995 ? 'the chart'
        : Math.round(100 * v) + '% of the way to the chart';
    },
    onInput: function(v){ sceneState.t = v; sceneRefreshPolar(ctx); }
  }, function(v){ sceneState.t = v; sceneRefreshPolar(ctx); }));
}

/** The key to what is drawn over the cell, and what moves it: the wedge rail, or a slider. */
function scenePolarityExplain(ctx) {
  var key = document.createElement('div');
  key.className = 'explain-key';
  var swatch = function(colour, words) {
    return '<span><i style="background:' + colour + '"></i>' + words + '</span>';
  };
  var aspect = ctx.explain.aspect;
  key.innerHTML = swatch('#5a6e88', 'towards ' + escHtml(ctx.explain.ref) + ' (0\u00b0)')
    + (aspect === 'angle'
        ? swatch(POLAR_FOCUS_COLOUR, escHtml(ctx.explain.structure) + ', in the wedge')
          + swatch('#c5c9d0', 'outside the wedge, faint')
          + swatch('#d9dce1', 'the wedge of angles')
          + swatch('#1b222b', 'the average direction (length R)')
        : aspect === 'index'
          ? swatch(litEntColour(ctx.explain.structure), escHtml(ctx.explain.structure) + ', one dot each')
            + swatch('#1b222b', 'the average (length R); the dark dot is V')
          : (aspect === 'map' || aspect === 'cellmap')
            ? polarSubjects(ctx.explain.ref).map(function(n){ return swatch(litEntColour(n), escHtml(n)); }).join('')
              + swatch(litEntColour(ctx.explain.ref), escHtml(ctx.explain.ref) + ' (the reference)')
              + '<span>\u25c6 one point per structure</span>'
            : swatch(litEntColour(ctx.explain.structure), escHtml(ctx.explain.structure) + ', one dot each'));
  ctx.explainEl.appendChild(key);
  if (aspect === 'angle') sceneWedgeRail(ctx);
  else if (aspect !== 'index') sceneTurnSlider(ctx, aspect);
}

/** In words, what the baseline chart's number is for the group on show, or for all of them. */
function sceneBaselineNote(ctx, order, scores) {
  var target = dataName(ctx.explain.targets[0] || 'the structure'), named = dataName(ctx.subject);
  if (sceneState.groupAt === null) {
    var closer = 0, valued = 0;
    order.forEach(function(root) {
      var s = scores.get(root);
      if (s) { valued++; if (s.score > 50) closer++; }
    });
    return closer + ' of ' + valued + ' groups reach ' + target + ' closer than a random group of their size: cyan groups beat'
      + ' many random groups of their size, magenta ones few. Pick a group to see its numbers.';
  }
  var s2 = scores.get(order[sceneState.groupAt]);
  if (!s2) return 'This group has no measured distance to ' + target + '.';
  return 'Its nearest ' + dataName(ctx.subject) + ' is ' + fmtValue(s2.reach) + ' \u00b5m from ' + target + '. Half of the random groups of '
    + s2.size + ', drawn from this ' + noun() + '\u2019s ' + named + ', get as near as ' + fmtValue(s2.typical) + ' \u00b5m; this one is nearer than '
    + Math.round(s2.score) + '% of them.';
}

/** The menu that picks which contact group stands out; its entries are filled in on every paint, since the groups follow the gap. */
function sceneGroupRail(ctx) {
  var rail = document.createElement('div');
  rail.className = 'group-pick';
  var label = document.createElement('label');
  label.textContent = 'Highlight';
  label.className = 'small';
  ctx.groupMenuEl = document.createElement('select');
  ctx.groupMenuEl.className = 'form-select form-select-sm';
  ctx.groupMenuEl.addEventListener('change', function() {
    sceneState.groupAt = ctx.groupMenuEl.value === '' ? null : +ctx.groupMenuEl.value;
    scenePaint(ctx);
  });
  label.appendChild(ctx.groupMenuEl);
  ctx.groupNoteEl = document.createElement('p');
  ctx.groupNoteEl.className = 'explain-scope mb-0 mt-1';
  rail.append(label, ctx.groupNoteEl);
  ctx.explainEl.insertBefore(rail, ctx.explainEl.firstChild);
  sceneFillGroupMenu(ctx);
}

/** The menu's entries: all groups, then one per group, with the pick kept. The first paint comes before the menu exists, so the names wait on ctx. */
function sceneFillGroupMenu(ctx) {
  var menu = ctx.groupMenuEl, names = ctx.groupNames || [];
  if (!menu) return;
  ctx.groupNoteEl.innerHTML = ctx.groupNote || '';
  menu.replaceChildren();
  [['', 'All groups (' + names.length + ')']].concat(names.map(function(n, i){ return [String(i), n]; }))
    .forEach(function(pair) {
      var option = document.createElement('option');
      option.value = pair[0];
      option.textContent = pair[1];
      menu.appendChild(option);
    });
  menu.value = sceneState.groupAt === null ? '' : String(sceneState.groupAt);
}

/**
 * The panel under the viewer: what the pinned chart is about, the slider that ties the
 * picture to it, and a count of what the picture shows. Built once per load - the
 * sliders keep their grip while the scene repaints behind them.
 */
function sceneBuildExplain(ctx, summary) {
  stopWedge();
  ctx.explainEl.replaceChildren();
  ctx.readoutEl = null;
  ctx.stripEl = null;
  ctx.groupMenuEl = null;
  ctx.groupNoteEl = null;
  // Nothing pinned: the status line above the viewer says what it is showing, and a box here
  // saying the same again was the first thing a reader met and the least useful.
  if (!ctx.explain) return;
  var body = document.createElement('p');
  body.className = 'explain-body';
  body.innerHTML = ctx.explain.text;
  var scope = document.createElement('p');
  scope.className = 'explain-scope';
  var pooled = scopedObjectNames().length;
  scope.textContent = 'Drawn for ' + ctx.current.id + ' alone' + (pooled > 1
    ? '; the chart pools ' + pooled + ' ' + noun(true) + '.' : '.');
  // What to touch and what it says come first, under the picture; the words explaining the
  // picture follow, since they are read once and the slider is used again and again.

  if (ctx.explain.kind !== 'polarity') sceneExplainLegend(ctx);
  if (ctx.explain.kind === 'polarity' && ctx.loaded && ctx.loaded.frame) scenePolarityExplain(ctx);
  if (ctx.explain.kind === 'group' && ctx.explain.aspect !== 'share') sceneGroupRail(ctx);
  // A chart of one measurement is explained by the status line above the viewer and the key
  // under it; a slider, a count and a caption beneath said the same again.
  if (ctx.explain.kind !== 'structure') return;
  ctx.readoutEl = document.createElement('div');
  ctx.readoutEl.className = 'explain-readout';
  ctx.explainEl.append(ctx.readoutEl, body, scope);
  sceneWriteExplain(ctx);
}

async function sceneDraw(ctx, refit) {
  var done = busy(ctx.holder, _geomCache.summary ? 'Drawing…' : 'Reading geometry…');
  try {
    var summary = await sceneReadSummary(ctx);
    if (!ctx.isLatest()) return;
    if (sceneState.entities === null) {
      // Every structure the report holds is on to begin with.
      sceneState.entities = new Set(summary.entities.map(function(e){ return e.name; }));
    }
    if (sceneState.maxGap === null) {
      sceneState.maxGap = summary.widestGap;
      sceneState.gap = summary.widestGap === null ? null : summary.widestGap * 0.25;
    }
    // A chart of contact groups has a gap of its own; the viewer takes it from there.
    if (ctx.explain && ctx.explain.kind === 'group' && contactGapUm() !== null) {
      sceneState.gap = contactGapUm();
    }
    var read = await sceneReadGeometry(ctx, summary);
    if (!read || !ctx.isLatest()) return;
    ctx.loaded = read;
    if (!_objectStage) {
      _objectStage = makeStage(await loadThree(), ctx.canvas);
      new ResizeObserver(function() {
        if (_objectStage) _objectStage.resize(ctx.holder.clientWidth, ctx.holder.clientHeight);
      }).observe(ctx.holder);
    }
    _objectStage.setPlanar(PLANAR);
    scenePaint(ctx);
    _objectStage.resize(ctx.holder.clientWidth, ctx.holder.clientHeight);
    // Framed once per object: pinning a chart changes what is drawn and how, not where the
    // reader was looking, and turning the camera back each time would undo their work.
    if (refit !== false && _framedFor !== ctx.current.id) {
      _objectStage.fit();
      _framedFor = ctx.current.id;
    }

    sceneBuildControls(ctx, summary);
    sceneBuildExplain(ctx, summary);
    syncRoseMarkers();
    syncMapMarkers();
    // Said, not left as an empty box: the chart is about something this object has no
    // surface for - a structure written without geometry, say.
    if (ctx.explain && !ctx.loaded.instances.concat(ctx.loaded.masks).some(function(i){ return i.entity === ctx.subject; })) {
      ctx.noteEl.textContent = 'There is no geometry for ' + ctx.subject + ' in this ' + noun()
        + ', so there is nothing to draw it from.';
    }
  } catch (error) {
    ctx.holder.style.display = 'none';
    ctx.noteEl.textContent = 'Could not read the geometry: ' + geometryProblem(error);
  } finally {
    done();
  }
}

function sceneBuildControls(ctx, summary) {
  ctx.controls.replaceChildren();
  if (ctx.objects.length > 1) {
    var shortOf = new Map(ctx.objects.map(function(o) {
      return [o.id, o.id.length > 34 ? '…' + o.id.slice(-32) : o.id];
    }));
    ctx.controls.appendChild(selector(Noun(), ctx.objects.map(function(o){ return o.id; }),
      ctx.current.id, function(value) {
        sceneState.objectId = value;
        sceneState.entities = ctx.explain ? new Set(explainEntities(ctx.explain)) : null;
        sceneState.maxGap = null;
        renderScene();
      }, shortOf));
  }
  // Which group the cell on show belongs to, in the colour the charts give that group.
  var currentRow = OBJECTS.find(function(o){ return String(o.object_id) === String(ctx.current.id); });
  if (currentRow && currentRow.group_id) {
    var badge = document.createElement('span');
    badge.className = 'cell-group';
    badge.innerHTML = '<i style="background:' + groupColor(currentRow.group_id) + '"></i>' + escHtml(currentRow.group_id);
    ctx.controls.appendChild(badge);
  }

  // A chart being explained has already said how things are coloured and which structures
  // matter, so the free ctx.controls would only contradict it.
  if (ctx.explain) return;
  var colourings = ['entity', 'metric'];
  if (summary.widestGap !== null) colourings.push('group');
  ctx.controls.appendChild(selector('Colour by', colourings, sceneState.colourBy, function(value) {
    sceneState.colourBy = value;
    sceneBuildControls(ctx, summary);
    if (value === 'group') sceneDraw(ctx, false);
    else scenePaint(ctx);
  }, new Map([['entity', 'structure'], ['metric', 'a metric'], ['group', 'contact group']])));

  if (sceneState.colourBy === 'metric') {
    ctx.controls.appendChild(selector('Metric', ctx.metrics, sceneState.metric, function(value) {
      sceneState.metric = value;
      sceneDraw(ctx, false);
    }, new Map(ctx.metrics.map(function(m){ return [m, labelFor(m)]; }))));
  }
  if (sceneState.colourBy === 'group' && sceneState.maxGap) {
    ctx.controls.appendChild(slider('Gap ≤', {
      min: 0, max: sceneState.maxGap, step: sceneState.maxGap / 100, value: sceneState.gap,
      format: function(v){ return v.toFixed(3) + ' µm'; }
    }, function(value) {
      sceneState.gap = value;
      sceneDraw(ctx, false);
    }));
  }

  // Exploding needs each instance's direction from the object centre, which rides in the
  // geometry file. Geometry written before that was carried has none, and a slider that
  // silently does nothing is worse than no slider.
  if (!PLANAR && ctx.loaded && ctx.loaded.instances.some(function(item){ return item.polar.dist > 0; })) {
    ctx.controls.appendChild(slider('Explode', {
      min: 0, max: 2, step: 0.1, value: sceneState.explode,
      format: function(v){ return v.toFixed(1) + '×'; }
    }, function(value) {
      sceneState.explode = value;
      scenePaint(ctx);
      if (_objectStage) _objectStage.fit();
    }));
  }

  var depth = multiresDepth(ctx.loaded);
  if (depth > 1) {
    var levels = ['auto'], names = new Map([['auto', 'automatic (by distance)']]);
    for (var level = 0; level < depth; level++) {
      levels.push(String(level));
      names.set(String(level), level + (level === 0 ? ' (finest)' : level === depth - 1 ? ' (coarsest)' : ''));
    }
    if (levels.indexOf(String(sceneState.levelPin)) < 0) sceneState.levelPin = 'auto';
    var control = selector('Level of detail', levels, sceneState.levelPin, function(value) {
      sceneState.levelPin = value;
      multiresRefresh();
    }, names);
    control.title = 'Draw every big mesh at one level of detail, to look at each level in turn';
    ctx.controls.appendChild(control);
  }

  var chips = document.createElement('div');
  chips.style.cssText = 'display:flex;flex-wrap:wrap;gap:.5rem;align-items:center';
  summary.entities.forEach(function(entity) {
    var label = document.createElement('label');
    label.title = [
      entity.instances
        ? entity.instances.toLocaleString()
          + (entity.instances === 1 ? ' instance' : ' instances') : '',
      entity.masks ? 'whole-structure mask' : ''
    ].filter(Boolean).join(', ');
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = sceneState.entities.has(entity.name);
    box.addEventListener('change', function() {
      if (box.checked) sceneState.entities.add(entity.name);
      else sceneState.entities.delete(entity.name);
      sceneDraw(ctx, false);
    });
    var swatch = document.createElement('span');
    swatch.className = 'dot';
    swatch.style.background = litEntColour(entity.name);
    label.className = 'scene-chip';
    label.append(box, swatch, entity.name);
    chips.appendChild(label);
  });
  ctx.controls.appendChild(chips);
}
