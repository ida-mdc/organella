//
// The gallery is not a section of its own: it sits inside the structure block, right under
// the distributions it explains. A sphericity outlier in the box above is a thumbnail here,
// and clicking it shows two granules the segmentation merged.

/**
 * How many thumbnails to ask for: whole rows of however many the grid fits.
 *
 * The grid is auto-fill, so its column count is a property of the width, not of the
 * gallery. Asking for a round number instead left the last row part empty at every width
 * that did not happen to divide it.
 */
function thumbsWanted(columns, rows) {
  columns = Math.max(1, columns);
  return Math.min(MAX_THUMBS - (MAX_THUMBS % columns), columns * rows);
}

var _thumbStage = null;

/** One small renderer, reused for every thumbnail: a canvas each would exhaust WebGL. */
async function thumbStage() {
  if (!_thumbStage) {
    var canvas = document.createElement('canvas');
    canvas.width = THUMB_PX * 2;
    canvas.height = THUMB_PX * 2;
    _thumbStage = makeStage(await loadThree(), canvas, { shadows: false });
    _thumbStage.resize(THUMB_PX * 2, THUMB_PX * 2);
  }
  return _thumbStage;
}

/**
 * The material for an instance's own geometry.
 *
 * A mesh with a skeleton inside it is drawn see-through: an opaque surface hides the very
 * thing the skeleton metrics are about. Outlines need no such help.
 */
function instanceMaterial(THREE, item) {
  if (item.flat) return new THREE.LineBasicMaterial({ vertexColors: true });
  return new THREE.MeshPhongMaterial(Object.assign({
    vertexColors: true, side: THREE.DoubleSide, shininess: 26, specular: 0x0d0d10
  }, item.skeleton ? { transparent: true, opacity: 0.35, depthWrite: false } : {}));
}

/** One instance into a stage: its shape in `hex`, and its skeleton over it in near-black. */
function addInstance(stage, item, hex) {
  var THREE = stage.THREE;
  var geometry = mergeInstances(THREE, [{ geometry: item.geometry, rgb: toLinearRgb(hex) }]);
  if (geometry) {
    stage.scene.add(item.flat
      ? new THREE.LineSegments(geometry, instanceMaterial(THREE, item))
      : new THREE.Mesh(geometry, instanceMaterial(THREE, item)));
  }
  if (item.skeleton) {
    var lines = mergeInstances(THREE, [{ geometry: item.skeleton,
                                         rgb: [0.09, 0.08, 0.06].map(toLinear) }]);
    if (lines) {
      stage.scene.add(new THREE.LineSegments(lines,
        new THREE.LineBasicMaterial({ vertexColors: true })));
    }
  }
}

async function paintThumb(target, item, hex) {
  var stage = await thumbStage();
  stage.clear();
  addInstance(stage, item, hex);
  stage.fit();
  var context = target.getContext('2d');
  context.clearRect(0, 0, target.width, target.height);
  context.drawImage(stage.renderer.domElement, 0, 0, target.width, target.height);
}

var _modalStage = null;

/** The full-size look at one instance, with its skeleton if it has one. */
async function openInstance(item, title) {
  var overlay = document.createElement('div');
  overlay.className = 'instance-overlay';
  var panel = document.createElement('div');
  panel.className = 'instance-panel';
  var head = document.createElement('div');
  head.style.cssText = 'display:flex;align-items:center;gap:.6rem;color:' + CS.ink2
    + ';font-size:.8rem';
  head.append(title);
  var close = document.createElement('button');
  close.textContent = 'Close';
  close.className = 'btn btn-sm btn-outline-dark';
  close.style.marginLeft = 'auto';
  head.appendChild(close);
  var canvas = document.createElement('canvas');
  panel.append(head, canvas);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  var dismiss = function() {
    if (_modalStage) _modalStage.clear();
    overlay.remove();
  };
  close.addEventListener('click', dismiss);
  overlay.addEventListener('click', function(event){ if (event.target === overlay) dismiss(); });

  var THREE = await loadThree();
  // The modal keeps its own stage across openings, for the same reason the gallery does.
  if (!_modalStage || _modalStage.renderer.domElement !== canvas) {
    if (_modalStage) _modalStage.dispose();
    _modalStage = makeStage(THREE, canvas, { shadows: false });
  }
  var stage = _modalStage;
  stage.clear();
  addInstance(stage, item, item.hex);
  stage.resize(canvas.clientWidth, canvas.clientHeight);
  stage.fit();
}

// ── the instances, in the viewer ────────────────────────────────────────────
//
// The gallery used to sit under each structure's charts, which put the shapes behind a
// scroll from the numbers they explain and left the viewer's column empty of them. With a
// viewer beside the charts, it is a tab of it: the same cards, one structure at a time, and
// the structure follows the one the charts are on until the reader picks another.
var dockState = { tab: 'scene', entity: null, followed: null, drawn: null };

/** Structures with geometry to browse: labelled ones card by card, masks one per object. */
function galleryChoices() {
  var labels = uniq(INSTANCES.map(function(r){ return r.entity_name; }));
  var masks = uniq(ENTS.filter(function(r){ return r.entity_kind === 'mask'; })
                       .map(function(r){ return r.entity_name; }))
    .filter(function(name){ return labels.indexOf(name) < 0; });
  return { labels: labels, masks: masks };
}

function showDockTab(tab, entity) {
  if (!hasGeometry()) return;
  dockState.tab = tab;
  if (entity) dockState.entity = entity;
  syncDock();
}

/** Which of the viewer's two tabs is up; the gallery is only (re)drawn when it is. */
function syncDock() {
  var scene = document.getElementById('dock-scene');
  var gallery = document.getElementById('dock-gallery');
  var tabs = document.getElementById('dock-tabs');
  if (!scene || !gallery || !tabs) return;
  var browsing = hasGeometry() && dockState.tab === 'gallery';
  scene.classList.toggle('d-none', browsing);
  gallery.classList.toggle('d-none', !browsing);
  var status = document.getElementById('dock-status');
  if (status) status.classList.toggle('d-none', browsing);
  tabs.replaceChildren();
  [['scene', Noun()], ['gallery', 'Instances']].forEach(function(entry) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'dock-tab' + ((entry[0] === 'gallery') === browsing ? ' active' : '');
    button.textContent = entry[1];
    button.addEventListener('click', function(){ showDockTab(entry[0]); });
    tabs.appendChild(button);
  });
  if (browsing) renderDockGallery();
}

function renderDockGallery() {
  var host = document.getElementById('dock-gallery');
  var choices = galleryChoices();
  var all = choices.labels.concat(choices.masks);
  if (!all.length) { host.replaceChildren(); return; }
  if (all.indexOf(dockState.entity) < 0) {
    dockState.entity = all.indexOf(st.statsEntity) >= 0 ? st.statsEntity : all[0];
  }
  var kind = choices.labels.indexOf(dockState.entity) >= 0 ? 'label' : 'file';
  // Redrawn when what it shows changes, not on every repaint of the page around it: reading
  // and drawing a strip of shapes is the one slow thing here.
  var key = [dockState.entity, st.only, plotGroupBy, GEOMETRY.length, kind].join('|');
  if (dockState.drawn === key && host.childNodes.length) return;
  dockState.drawn = key;
  host.replaceChildren();
  var bar = document.createElement('div');
  bar.className = 'ctrl-bar';
  var labelOf = new Map(all.map(function(name) {
    return [name, name + (choices.masks.indexOf(name) >= 0 ? ' (one per ' + noun() + ')' : '')];
  }));
  bar.appendChild(selector('Structure', all, dockState.entity, function(value) {
    dockState.entity = value;
    renderDockGallery();
  }, labelOf));
  var strip = document.createElement('div');
  host.append(bar, strip);
  // Two rows to begin with: the column is wide, and one row of it is a lot of empty card.
  renderGallery(strip, dockState.entity, kind === 'file' ? 'file' : undefined,
                { rows: GALLERY_ROWS[1] });
}

/**
 * Where a structure's gallery goes: with geometry, in the viewer, and here only a way to
 * open it there; without, the gallery's own explanation of how to get some.
 */
function galleryOrPointer(container, entity, rowType) {
  if (!hasGeometry()) { renderGallery(container, entity, rowType); return; }
  container.replaceChildren();
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'explain-release';
  button.style.cssText = 'font-size:.78rem;padding:.25rem .8rem;margin-bottom:.8rem';
  button.textContent = 'Browse the ' + entity + (rowType === 'file' ? ' of every ' + noun()
    : ' instances') + ' in the viewer →';
  button.addEventListener('click', function(){ showDockTab('gallery', entity); });
  container.appendChild(button);
}

/**
 * The gallery strip for one structure, inside its block.
 *
 * `rowType` says what a card is. For a structure segmented instance by instance it is one
 * instance; for one segmented as a single piece it is that piece, one card per object -
 * a strip of six livers side by side, which is the only place in the report the shapes
 * themselves can be compared, not only their numbers.
 */
function renderGallery(container, entity, rowType, opts) {
  container.replaceChildren();
  var masks = rowType === 'file';
  var thing = masks ? 'mask' : 'instance';
  var objects = filteredGeometry();
  if (!objects.length) {
    geometryHint(container, 'the ' + thing + 's are drawn here');
    return;
  }

  var metrics = geometryMetrics();
  var state = { metric: metrics[0], order: 'highest',
                rows: (opts && opts.rows) || GALLERY_ROWS[0] };
  var bar = document.createElement('div');
  bar.className = 'ctrl-bar';
  var grid = document.createElement('div');
  grid.className = 'gallery-grid';
  // Under the cards, filled once they are read and the scale's ends are known.
  var rampHost = document.createElement('div');
  rampHost.className = 'metric-scale';
  var footer = document.createElement('div');
  footer.className = 'chart-note';
  // What the border of a card means: the group (or cell) it belongs to, in the colour the charts give it.
  var borderKey = document.createElement('div');
  borderKey.className = 'chart-legend';
  container.append(bar, borderKey, grid, rampHost, footer);

  var wanted = function() {
    var columns = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(' ')
      .filter(Boolean).length);
    return thumbsWanted(columns, state.rows);
  };

  var draw = async function() {
    grid.replaceChildren();
    var loading = document.createElement('p');
    loading.className = 'chart-note';
    loading.textContent = 'Reading geometry…';
    grid.appendChild(loading);
    try {
      var source = sourceOf(objects);
      var totals = await queryRows(
        galleryCountSql(source, entity, state.metric, rowType));
      var rows = await queryRows(
        gallerySql(source, entity, state.metric, state.order, wanted(), rowType));

      grid.replaceChildren();
      if (!rows.length) {
        footer.textContent = 'No ' + entity + ' ' + thing + ' has a '
          + labelFor(state.metric) + '.';
        return;
      }
      // Two encodings: the shape takes the colour the sort metric gives it - the same
      // scale, the same colours as the 3D scene - and the border says which object (or
      // group) it came from, the colour the charts above use for it.
      //
      // The scale spans the whole population of that structure, not the cards on screen:
      // the ten largest granules of thirteen thousand are all "large", and scaled to each
      // other they would be drawn as the full range of the colour map.
      var scale = metricSpan(entity, objects, state.metric, rows);
      var colourFor = function(value) {
        return isFinite(value) ? viridisHexAt((value - scale.low) / (scale.high - scale.low))
                               : '#9aa0a6';
      };
      rampHost.replaceChildren();
      rampHost.appendChild(colourRamp({ label: labelFor(state.metric),
                                        low: scale.low, high: scale.high }));
      // A mask card is named after its object, and object names are folder paths that
      // differ in the middle: trimming to a fixed width leaves them all reading the same.
      // The shared prefix and suffix come off instead, as they do on the chart ticks.
      var owners = uniq(rows.map(function(r){ return String(r.object_id); }));
      var shortOwner = {};
      shortFacetLabels(owners).forEach(function(short, i){ shortOwner[owners[i]] = short; });
      var seenBorders = {};
      borderKey.replaceChildren();
      for (const row of rows) {
        var drawable = decodeDrawable(row);
        if (!drawable) continue;
        var owner = String(row.object_id);
        var object = objects.find(function(o){ return o.id === owner; });
        var card = document.createElement('figure');
        card.className = 'gallery-card';
        card.style.borderColor = gColor(plotGroupBy === 'group' && object
          ? object.group : owner);
        var thumb = document.createElement('canvas');
        thumb.width = THUMB_PX * 2;
        thumb.height = THUMB_PX * 2;
        var caption = document.createElement('figcaption');
        var value = numOrNull(row.value);
        var meshHex = colourFor(value);
        // A mask is one per object, so the object names the card; an instance is one of
        // thousands in an object, so its label number does.
        // The full name is the hover.
        var name = masks ? shortOwner[owner] : '#' + row.label;
        caption.textContent = name + ' · ' + (isFinite(value) ? value.toPrecision(3) : '-');
        caption.title = owner + ': ' + labelFor(state.metric) + ' ' + value;
        card.append(thumb, caption);
        var skeleton = decodePayload(row.skeleton, 2);
        var item = { geometry: drawable.geometry, flat: drawable.flat, skeleton: skeleton };
        card.addEventListener('click', (function(item, title, value) {
          return function() {
            openInstance(Object.assign({ hex: meshHex }, item),
              title + ' · ' + labelFor(state.metric) + ' '
              + (isFinite(value) ? value.toPrecision(4) : '-'));
          };
        })(item, owner + ' · ' + entity + (masks ? '' : ' #' + row.label), value));
        grid.appendChild(card);
        var borderOwner = plotGroupBy === 'group' && object ? object.group : owner;
        if (!seenBorders[borderOwner]) {
          seenBorders[borderOwner] = true;
          borderKey.insertAdjacentHTML('beforeend', '<span class="d-inline-flex align-items-center gap-1">'
            + '<span style="display:inline-block;width:11px;height:11px;border-radius:3px;border:2px solid '
            + gColor(borderOwner) + '"></span>' + escHtml(plotGroupBy === 'group' ? String(borderOwner) : shortOwner[owner]) + '</span>');
        }
        // Sequential: one WebGL stage draws them all, and each needs the canvas to itself.
        await paintThumb(thumb, item, meshHex);
      }
      footer.textContent = rows.length + ' of '
        + Number((totals[0] || {}).n || 0).toLocaleString() + ' ' + entity + ' '
        + thing + 's, '
        + (state.order === 'random' ? 'a random sample' : state.order) + ' by '
        + labelFor(state.metric).toLowerCase() + ', across ' + objects.length + ' '
        + noun(objects.length !== 1) + '. Click one to look at it properly'
        // Only instances are skeletonised, so only their strip is worth the caveat.
        + (masks ? '.' : '; one with a skeleton is drawn see-through.');
    } catch (error) {
      grid.replaceChildren();
      footer.textContent = 'Could not read the geometry: ' + geometryProblem(error);
    }
  };

  bar.appendChild(selector('Sort by', metrics, state.metric, function(value) {
    state.metric = value;
    draw();
  }, new Map(metrics.map(function(m){ return [m, labelFor(m)]; }))));
  bar.appendChild(selector('Pick', ['highest', 'lowest', 'random'], state.order, function(value) {
    state.order = value;
    draw();
  }));
  bar.appendChild(selector('Rows', GALLERY_ROWS, state.rows, function(value) {
    state.rows = Number(value);
    draw();
  }));
  draw();
}
