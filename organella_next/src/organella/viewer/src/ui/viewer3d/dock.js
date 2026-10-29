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
 * Put a "Show in 3D" button on a chart cell. Pressing it again lets go.
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
  button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"'
    + ' stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z"/>'
    + '<path d="M2 4.8 8 8l6-3.2M8 8v6.5"/></svg><span>3D</span>';
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
    add('span', 'hint', 'Press <b>3D</b> on any chart to see what it measures on a ' + escHtml(noun()) + '.');
  }
}

/**
 * What is drawn over the cell for one polarity chart, in a group so a slider can swap it
 * without touching the meshes.
 *
 * `o.aspect` says which chart. All of them have the centre, and the arrow to the
 * reference. The rose adds a line to every instance and the shell of angles one wedge
 * stands for. The two indices add the average direction, as an arrow as long as R whose
 * reach along the axis is V, with the lines shortening to unit length to show that
 * distance is not what they are about. The three plots add a point for each instance, and
 * the grid of the chart they are on, both of which are moved by `o.t` from the cell to the
 * chart.
 */
function polarityOverlay(THREE, frame, o) {
  var group = new THREE.Group();
  var c = frame.centre, a = frame.axis, L = frame.reach * 1.1;
  // Overlays are neutral: a hue on this stage means a structure, and blue and orange are the two
  // groups everywhere else in the report.
  var BLUE = new THREE.Color('#1b222b');
  var vec = function(p){ return new THREE.Vector3(p[0], p[1], p[2]); };
  // A word on the picture: colour is for structures, so what an arrow or a ring *is* is written.
  var textAt = function(text, p, colour, width, opacity) {
    // Drawn at twice the size it is shown at, on a canvas as wide as the words need, so it is sharp and never cut.
    var canvas = document.createElement('canvas');
    var g2 = canvas.getContext('2d');
    g2.font = '600 68px system-ui, sans-serif';
    var wide = Math.max(768, Math.ceil(g2.measureText(text).width) + 40);
    canvas.width = wide; canvas.height = 144;
    g2.font = '600 68px system-ui, sans-serif';
    g2.textAlign = 'center'; g2.textBaseline = 'middle';
    g2.lineJoin = 'round'; g2.lineWidth = 9; g2.strokeStyle = 'rgba(255,255,255,.9)'; g2.strokeText(text, wide / 2, 76);
    g2.fillStyle = colour; g2.fillText(text, wide / 2, 76);
    var texture = new THREE.CanvasTexture(canvas);
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture,
      depthTest: false, transparent: true, opacity: opacity === undefined ? 1 : opacity }));
    sprite.position.set(p[0], p[1], p[2]);
    sprite.scale.set(width * wide / 768, width * 144 / 768, 1);
    sprite.renderOrder = 12;
    group.add(sprite);
  };
  // A diamond that always faces the viewer: a flat solid seen from the side turns into a sliver or a tilted square.
  var diamondAt = function(p, colour, half, opacity, outlined, order) {
    var canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    var g2 = canvas.getContext('2d');
    g2.beginPath();
    g2.moveTo(64, 6); g2.lineTo(122, 64); g2.lineTo(64, 122); g2.lineTo(6, 64); g2.closePath();
    g2.fillStyle = colour; g2.fill();
    if (outlined) { g2.lineJoin = 'round'; g2.lineWidth = 8; g2.strokeStyle = '#1b222b'; g2.stroke(); }
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas),
      depthTest: false, transparent: true, opacity: opacity }));
    sprite.position.set(p[0], p[1], p[2]);
    sprite.scale.set(half * 2, half * 2, 1);
    sprite.renderOrder = order;
    group.add(sprite);
  };
  var line = function(points, colour, opacity, parent) {
    var geometry = new THREE.BufferGeometry().setFromPoints(points.map(vec));
    var made = new THREE.Line(geometry, new THREE.LineBasicMaterial({
      color: colour, transparent: opacity < 1, opacity: opacity, depthTest: false }));
    made.renderOrder = 4;
    (parent || group).add(made);
    return made;
  };
  var segments = function(pairs, colour, opacity) {
    var positions = [];
    pairs.forEach(function(p){ positions.push(p[0][0], p[0][1], p[0][2], p[1][0], p[1][1], p[1][2]); });
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    var made = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
      color: colour, transparent: true, opacity: opacity, depthTest: false }));
    made.renderOrder = 4;
    group.add(made);
    return made;
  };
  var sT0 = function(oo){ return POLAR_FLATTENS[oo.aspect] ? oo.t : 0; };
  var morphing = !!POLAR_LAYOUTS[o.aspect];
  var layout = POLAR_LAYOUTS[o.aspect];
  var t = morphing ? o.t : 0;

  // The shell of angles between two cones about the axis: what one wedge of the rose counts.
  if (o.aspect === 'angle') {
    var loRad = o.wedge * Math.PI / POLAR_ANGLE_BINS, hiRad = loRad + Math.PI / POLAR_ANGLE_BINS;
    var across = o.planar ? [[-a[1], a[0], 0]] : acrossAxis(a);
    var K = o.planar ? 2 : 48, J = 4, wrap = !o.planar, rings = [];
    for (var j = 0; j <= J; j++) {
      var tt = loRad + (hiRad - loRad) * j / J, ring = [];
      for (var k = 0; k < K; k++) {
        var phi = o.planar ? (k ? Math.PI : 0) : 2 * Math.PI * k / K;
        var sideways = o.planar
          ? polScaled(across[0], Math.cos(phi))
          : polAdded(polScaled(across[0], Math.cos(phi)), polScaled(across[1], Math.sin(phi)));
        var dir = polAdded(polScaled(a, Math.cos(tt)), polScaled(sideways, Math.sin(tt)));
        ring.push([c[0] + dir[0] * L, c[1] + dir[1] * L, c[2] + dir[2] * L]);
      }
      rings.push(ring);
    }
    var tri = [];
    for (var r = 1; r <= J; r++) {
      for (var q = 0; q < (wrap ? K : K - 1); q++) {
        var q2 = (q + 1) % K;
        tri.push(rings[r - 1][q], rings[r - 1][q2], rings[r][q]);
        tri.push(rings[r - 1][q2], rings[r][q2], rings[r][q]);
      }
    }
    var shell = new THREE.BufferGeometry();
    shell.setAttribute('position', new THREE.Float32BufferAttribute([].concat.apply([], tri), 3));
    group.add(new THREE.Mesh(shell, new THREE.MeshBasicMaterial({
      color: BLUE, transparent: true, opacity: 0.14, side: THREE.DoubleSide, depthWrite: false })));
    [rings[0], rings[J]].forEach(function(rim) {
      var loop = new THREE[wrap ? 'LineLoop' : 'Line'](
        new THREE.BufferGeometry().setFromPoints(rim.map(vec)),
        new THREE.LineBasicMaterial({ color: BLUE }));
      group.add(loop);
    });
  }

  // The points that become the chart, and the chart's own grid under them.
  if (morphing) {
    var whole = o.aspect === 'map' || o.aspect === 'cellmap';
    // The group plot is one point per structure, so after the cell has been laid flat its
    // instances gather into that point; the circle and the scatter keep every instance.
    var collapses = o.aspect === 'map';
    var ease = function(x){ return x * x * (3 - 2 * x); };
    var s0 = collapses ? Math.min(1, t / 0.6) : t;
    var sT = ease(s0);
    var kT = collapses ? ease(Math.max(0, Math.min(1, (t - 0.6) / 0.4))) : 0;
    var groups = whole ? frame.groups
      : [{ name: null, units: frame.units, diamond: frame.diamond }];
    var colourOf = function(name) {
      return new THREE.Color((o.colours && name && o.colours[name]) || o.colour || '#1b222b');
    };
    var radiusOfDiamond = function(d) {
      return o.radiusMode === 'membrane' && d.depth != null && frame.depthReach != null
        ? frame.depthReach - d.depth : d.dist;
    };
    var gemSize = Math.max(frame.reach * 0.05, 1e-6);
    var R = frame.plotReach;
    var flat = !!POLAR_FLATTENS[o.aspect];
    var at = function(x, y) {
      return [c[0] + a[0] * x + frame.u[0] * y, c[1] + a[1] * x + frame.u[1] * y,
              c[2] + a[2] * x + frame.u[2] * y];
    };

    groups.forEach(function(g) {
      var tint = colourOf(g.name);
      var target = null;
      if (g.diamond) {
        target = new Float32Array(3);
        polarMorphPoint(frame, layout, g.diamond.n, g.diamond.dist, radiusOfDiamond(g.diamond), 1,
                        target, 0, g.diamond.theta);
      }
      var coords = new Float32Array(g.units.length * 3);
      g.units.forEach(function(unit, i) {
        polarMorphPoint(frame, layout, unit.n, unit.dist,
                        polarPlotRadius(frame, unit, o.radiusMode), sT, coords, 3 * i);
        if (kT > 0 && target) {
          for (var q = 0; q < 3; q++) coords[3 * i + q] += (target[q] - coords[3 * i + q]) * kT;
        }
      });
      var dots = new THREE.BufferGeometry();
      dots.setAttribute('position', new THREE.Float32BufferAttribute(coords, 3));
      // Invisible at the start - they are the cell's own instances, and the cell is drawn - and
      // faint at the end, when only the point they gathered into should be read.
      var points = new THREE.Points(dots, new THREE.PointsMaterial({
        color: tint, size: 3.2, sizeAttenuation: false, transparent: true,
        opacity: Math.min(1, s0 * 6) * (1 - 0.88 * kT), depthTest: false }));
      points.renderOrder = 6;
      group.add(points);

      // The cell's average for the structure - its middle distance, its mean direction: the
      // one point a plot per group is made of.
      // Not before the cell starts turning into the plot: until then a diamond would sit at a mean that
      // is not a place in the data, and appears faded in as the points gather.
      if (g.diamond && (layout === 'half' || layout === 'circle') && sT > 0.02) {
        var where = new Float32Array(3);
        polarMorphPoint(frame, layout, g.diamond.n, g.diamond.dist, radiusOfDiamond(g.diamond),
                        sT, where, 0, g.diamond.theta);
        diamondAt(where, '#' + new THREE.Color(tint).getHexString(),
                  gemSize * (0.8 + 0.7 * Math.max(sT, kT)), Math.min(1, sT * 3), true, 9);
      }
    });

    // Axis labels, on the plot itself: the plane has to say what its directions are.
    var label = function(text, x, y, colour, width) {
      textAt(text, at(x, y), colour, width, Math.min(1, sT * 1.5));
    };

    var grid = [];
    var arc = function(radius, from, to) {
      var pieces = [], steps = 48;
      for (var i = 0; i < steps; i++) {
        var p = from + (to - from) * i / steps, q = from + (to - from) * (i + 1) / steps;
        pieces.push([at(radius * Math.cos(p), radius * Math.sin(p)),
                     at(radius * Math.cos(q), radius * Math.sin(q))]);
      }
      return pieces;
    };
    if (!flat) {
      // Nothing to lay a grid on: the dots only move along their own directions.
    } else if (layout === 'cart') {
      grid.push([at(-R, 0), at(R, 0)], [at(-R, 0), at(-R, R)]);
      [1 / 3, 2 / 3, 1].forEach(function(f){ grid.push([at(-R, R * f), at(R, R * f)]); });
      [0, 1, 2, 3, 4].forEach(function(i) {
        var x = -R + 2 * R * i / 4;
        grid.push([at(x, 0), at(x, R)]);
      });
    } else {
      var sweep = layout === 'half' ? [0, Math.PI] : [0, 2 * Math.PI];
      [1 / 3, 2 / 3, 1].forEach(function(f){ grid = grid.concat(arc(R * f, sweep[0], sweep[1])); });
      grid.push([at(0, 0), at(R, 0)], [at(0, 0), at(0, R)]);
      if (layout === 'circle') grid.push([at(0, 0), at(-R, 0)], [at(0, 0), at(0, -R)]);
      else grid.push([at(0, 0), at(-R, 0)]);
    }
    if (grid.length) {
      var gridLines = segments(grid, 0x6b7c93, 0.75 * sT);
      gridLines.renderOrder = 3;
    }
    if (flat && layout === 'cart') {
      var ink = '#1b222b', soft = '#3c4a5d';
      [[-R, '180\u00b0'], [0, '90\u00b0'], [R, '0\u00b0']].forEach(function(e) { label(e[1], e[0], -R * 0.1, ink, R * 0.6); });
      label('angle to ' + (o.refName || 'the reference') + '  (0\u00b0 = towards it)', 0, -R * 0.24, ink, R * 0.65);
      [1 / 3, 2 / 3, 1].forEach(function(f) { label(fmtValue(R * f) + ' \u00b5m', -R * 0.84, R * f + R * 0.06, soft, R * 0.6); });
      label('distance from the ' + noun() + ' centre', -R * 0.45, R * 1.12, ink, R * 0.65);
    }
    if (flat && whole) {
      var inkText = '#1b222b', wide = R * 1.0;
      label('towards ' + (o.refName || 'the reference') + '  0\u00b0', R * 0.62, -R * 0.13, inkText, wide * 0.9);
      label('away  180\u00b0', -R * 0.78, -R * 0.13, inkText, wide * 0.7);
      label('90\u00b0', 0, R * 1.12, inkText, wide * 0.35);
      [1 / 3, 2 / 3, 1].forEach(function(f) {
        var value = o.radiusMode === 'membrane' && frame.depthReach
          ? frame.depthReach * (1 - f) : R * f;
        label(fmtValue(value) + ' µm' + (o.radiusMode === 'membrane' ? ' in' : ''),
              R * 0.2, R * f, '#3c4a5d', wide * 0.5);
      });
      // The reference itself, a star on the 0° line where it sits.
      var starAt = new Float32Array(3);
      var refRadius = o.radiusMode === 'membrane' && frame.refDepth != null && frame.depthReach != null
        ? frame.depthReach - frame.refDepth : frame.refDist;
      polarMorphPoint(frame, layout, frame.axis, frame.refDist, refRadius, sT, starAt, 0);
      diamondAt(starAt, '#' + new THREE.Color(colourOf(o.refName)).getHexString(),
                gemSize * 1.1, 1, true, 11);
    }
  }

  // An arrow is a shaft and a head: a line is one pixel wide however it is asked for, and
  // an average a tenth the length of the axis one cannot be read through that.
  var head = Math.max(L * 0.05, 1e-6);
  var arrow = function(dir, length, colour, order, width) {
    var shaft = Math.max(length - head * 1.6, 0);
    var material = new THREE.MeshBasicMaterial({ color: colour, depthTest: false });
    var made = new THREE.Group();
    var rod = new THREE.Mesh(new THREE.CylinderGeometry(head * width, head * width, shaft, 8),
                             material);
    rod.position.y = shaft / 2;
    var tip = new THREE.Mesh(new THREE.ConeGeometry(head * 0.55, head * 1.6, 14), material);
    tip.position.y = shaft + head * 0.8;
    made.add(rod, tip);
    made.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), vec(dir).normalize());
    made.position.copy(vec(c));
    made.traverse(function(node){ node.renderOrder = order; });
    return made;
  };
  group.add(arrow(a, L, 0x5a6e88, 6, 0.1));
  if (!POLAR_FLATTENS[o.aspect] || sT0(o) < 0.05) {
    textAt('towards ' + (o.refName || 'the reference') + '  0\u00b0',
           [c[0] + a[0] * L * 0.78, c[1] + a[1] * L * 0.78 + L * 0.09, c[2] + a[2] * L * 0.78], '#2b3644', L * 0.62);
  }
  if ((o.aspect === 'angle' || o.aspect === 'index') && frame.mean && frame.R > 0.01) {
    group.add(arrow(frame.mean, frame.R * L, 0x1b222b, 7, 0.26));
    textAt('average  R ' + frame.R.toFixed(2), [c[0] + frame.mean[0] * frame.R * L * 1.05, c[1] + frame.mean[1] * frame.R * L * 1.05 - L * 0.1,
           c[2] + frame.mean[2] * frame.R * L * 1.05], '#1b222b', L * 0.5);
  }
  // V: how far that average reaches along the axis. Dropped to it from the arrow's tip.
  if (o.aspect === 'index' && frame.mean && frame.R > 0.01) {
    var tip = [c[0] + frame.mean[0] * frame.R * L, c[1] + frame.mean[1] * frame.R * L,
               c[2] + frame.mean[2] * frame.R * L];
    var foot = [c[0] + a[0] * frame.V * L, c[1] + a[1] * frame.V * L, c[2] + a[2] * frame.V * L];
    line([tip, foot], 0x1b222b, 0.9);
    var bar = line([c, foot], 0x1b222b, 1);
    bar.material.linewidth = 2;
    var mark = new THREE.Mesh(new THREE.SphereGeometry(head * 0.45, 12, 8),
      new THREE.MeshBasicMaterial({ color: 0x1b222b, depthTest: false }));
    mark.position.set(foot[0], foot[1], foot[2]);
    textAt('V ' + frame.V.toFixed(2), [foot[0], foot[1] - L * 0.1, foot[2]], '#1b222b', L * 0.4);
    mark.renderOrder = 9;
    group.add(mark);
  }

  var dot = new THREE.Mesh(new THREE.SphereGeometry(head * 0.7, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0x1b222b, depthTest: false }));
  dot.position.copy(vec(c));
  dot.renderOrder = 8;
  group.add(dot);
  return group;
}

async function renderScene() {
  var controls = document.getElementById('scene-controls');
  var holderWrap = document.getElementById('scene-holder');
  var noteEl = document.getElementById('scene-note');
  var explainEl = document.getElementById('scene-explain');
  controls.replaceChildren();
  holderWrap.replaceChildren();
  explainEl.replaceChildren();
  noteEl.textContent = '';
  // With geometry the viewer is a column of its own, beside the charts (see the stylesheet);
  // without it, this section is where the page says how to get some.
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
  var explain = sceneState.explain;
  renderDockStatus();
  var dockEl = document.getElementById('s-3d');
  if (dockEl) dockEl.classList.toggle('explaining', !!explain);
  syncDock();
  // Two renders can overlap - a second pin before the first has read anything - and the
  // older must not paint over the newer.
  var run = ++_sceneRun;
  var isLatest = function(){ return run === _sceneRun; };

  // Only the objects the filter leaves, so "the object in 3D" is one the rest of the page
  // is also about.
  var objects = filteredGeometry();
  if (!objects.length) {
    geometryHint(holderWrap, 'the ' + noun() + ' is drawn here');
    return;
  }

  var current = objects.find(function(o){ return o.id === sceneState.objectId; }) || objects[0];
  sceneState.objectId = current.id;
  // Both kinds: what the geometry file carries, and what only the report does.
  var fromReport = reportOnlyMetrics(current.id);
  var metrics = geometryMetrics().concat(fromReport);
  if (!sceneState.metric || metrics.indexOf(sceneState.metric) < 0) sceneState.metric = metrics[0];

  // The same canvas every time: a new one would need a new WebGL context, and a browser
  // gives up the oldest of those after a handful.
  var stageParts = _sceneHolder;
  if (stageParts) {
    holderWrap.replaceChildren(stageParts.holder);
    stageParts.holder.querySelectorAll('.stage-busy').forEach(function(el){ el.remove(); });
    stageParts.holder.style.display = '';
  } else {
    stageParts = _sceneHolder = stageCanvas(holderWrap, 430);
  }
  var holder = stageParts.holder, canvas = stageParts.canvas;
  // Under the scene, filled by the painting - which is where the scale's ends are known,
  // since they come from the instances on screen and not from the whole report.
  var rampHost = document.getElementById('scene-scale');
  rampHost.replaceChildren();
  var loaded = null;

  /**
   * What is in this object's geometry file, and how big: the cheap query. One structure can
   * contribute both row kinds (a whole-structure mask and a mesh per instance), so counts
   * are folded into one entry per name, which is what the reader toggles.
   */
  async function readSummary() {
    // Keyed on the file: another object, or geometry added since, starts the cache over.
    var key = sourceOf([current]);
    if (_geomCache.key !== key) _geomCache = { key: key, summary: null, byEntity: new Map() };
    if (_geomCache.summary) return _geomCache.summary;
    var rows = await queryRows(sceneSummarySql(sourceOf([current])));
    var gaps = await queryRows(sceneGapSql(sourceOf([current])));
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

  async function readGeometry(summary) {
    var wanted = summary.entities
      .filter(function(e){ return sceneState.entities.has(e.name); })
      .map(function(e){ return e.name; });
    if (!wanted.length) return { instances: [], masks: [], edges: [] };

    // Every metric the file carries, not only the one on screen: what is read is kept, and a
    // different colouring should not mean reading it again.
    var metricCols = [...new Set(geometryMetrics().concat(
      ['polar_dist_um', 'polar_nx', 'polar_ny', 'polar_nz']))]
      .filter(function(col){ return fromReport.indexOf(col) < 0; });
    // A report-only metric is joined on by structure and label instead of selected. A chart
    // being explained always is: it plots a column of the report's own rows, which the
    // geometry file may not carry, and the join works for any of them.
    var joined = explain ? null : fromReport.indexOf(sceneState.metric) >= 0
      ? reportMetricValues(current.id, sceneState.metric) : null;
    var explained = explain ? explainValueCols(explain)
      .map(function(col){ return reportMetricValues(current.id, col); }) : null;

    // Only the structures not already read for this object.
    var source = sourceOf([current]);
    var missing = wanted.filter(function(name){ return !_geomCache.byEntity.has(name); });
    if (missing.length) {
      var rows = await queryRows(sceneGeometrySql(source, missing, metricCols));
      if (!isLatest()) return null;
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
          kind: String(row.surface_kind || 'mesh')
        });
      });
    }

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
               geometry: read.geometry, flat: read.flat, kind: read.kind };
    };
    wanted.forEach(function(name) {
      var read = _geomCache.byEntity.get(name);
      if (!read) return;
      read.instances.forEach(function(item){ instances.push(copy(item)); });
      read.masks.forEach(function(item){ masks.push(copy(item)); });
    });

    var edges = await readEdges();
    // Where the centre is among the meshes, for the arrows a polarity chart is explained by.
    var frame = null;
    if (explain && explain.kind === 'polarity') {
      var whole = explain.aspect === 'map' || explain.aspect === 'cellmap';
      var subjectsHere = whole ? polarSubjects(explain.ref) : [explain.structure];
      frame = polarityFrame(instances.concat(masks), current.id, explain.ref,
                            subjectsHere[0] || explain.structure, explain.radius,
                            subjectsHere.slice(1));
      // The wedge to begin on is the fullest: the tallest bar of the chart it explains.
      if (frame && sceneState.wedge === null) {
        var counts = new Array(POLAR_ANGLE_BINS).fill(0);
        frame.units.forEach(function(u){ counts[polarBinOf(u.angle)]++; });
        sceneState.wedge = counts.indexOf(Math.max.apply(null, counts));
      }
    }
    return { instances: instances, masks: masks, edges: edges, frame: frame };
  }

  async function readEdges() {
    if (sceneState.colourBy !== 'group' || sceneState.gap === null) return [];
    return (await queryRows(sceneEdgesSql(sourceOf([current]), sceneState.gap)))
      .map(function(e){ return { a: e.ea + '|' + e.la, b: e.eb + '|' + e.lb }; });
  }

  // The structure a chart is about, and the ones drawn solid for it. The rest of what is
  // loaded is there to measure against and is drawn faint.
  var subject = explain ? (explain.kind === 'polarity' ? explain.structure : explain.entity) : null;
  var shown = explain ? explainShown(explain) : null;
  // What the overlay of the polarity view was drawn into, so a slider can swap it alone.
  // It lives on the stage, which outlasts this render: an overlay left by the last one is
  // still in the scene.
  function clearOverlay() {
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
  function groupReach() {
    var items = loaded.instances.filter(function(i){ return i.entity === subject; });
    var groups = contactGroups(items.map(function(i){ return i.entity + '|' + i.label; }), loaded.edges);
    var columns = (explain.metrics || []).length, best = new Map();
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

  /**
   * Which instances a chart's slider has put out of play.
   *
   * A chart about "at most this far" or "within this angle" is a count of the instances
   * that qualify, so the ones that do not are drawn faint and kept: they are
   * still there, and where they sit is part of the picture.
   */
  function applyThreshold() {
    if (!explain || !loaded) return;
    var subjects = loaded.instances.filter(function(i){ return i.entity === subject; });
    subjects.forEach(function(item){ item.dim = false; });
    if (explain.kind === 'polarity') {
      // Only the rose puts instances out of play - those outside the wedge. The others turn
      // the whole cell into a chart, and leave none behind.
      if (!loaded.frame || explain.aspect !== 'angle') return;
      var counted = new Set();
      loaded.frame.units.forEach(function(u) {
        if (!u.item) return;
        u.item.dim = polarBinOf(u.angle) !== sceneState.wedge;
        counted.add(u.item);
      });
      // Without a direction there is nothing to be inside the wedge.
      subjects.forEach(function(item){ if (!counted.has(item)) item.dim = true; });
    } else if (explain.kind === 'group' && (explain.aspect === 'reach' || explain.aspect === 'both'
                                            || explain.aspect === 'baseline')) {
      // Only groups have a closest approach: what touches nothing is out of play, and so is a
      // group further than the slider's value.
      var reach = groupReach();
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
  function paintOverlay(andDraw) {
    var stage = _objectStage;
    if (!stage || !loaded || !isLatest()) return;
    clearOverlay();
    if (explain && explain.kind === 'polarity' && loaded.frame) {
      var colours = {};
      polarSubjects(explain.ref).concat([explain.ref]).forEach(function(n){ colours[n] = litEntColour(n); });
      stage.overlay = polarityOverlay(stage.THREE, loaded.frame, {
        aspect: explain.aspect, wedge: sceneState.wedge || 0, t: sceneState.t,
        planar: PLANAR, radiusMode: explain.radius, colour: litEntColour(explain.structure),
        colours: colours, refName: explain.ref });
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
  function applyMorph() {
    var stage = _objectStage;
    if (!stage) return;
    var morph = explain && explain.kind === 'polarity' && POLAR_FLATTENS[explain.aspect]
      && loaded && loaded.frame;
    if (!morph) {
      stage.setPose(null);
      stage.fade(1);
      if (stage.morphFrom) { stage.view(stage.morphFrom, false); stage.morphFrom = null; }
      return;
    }
    var t = sceneState.t;
    stage.setPose(polarMorphPose(stage.THREE, loaded.frame, t));
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
  function refreshPolar() {
    var stage = _objectStage;
    if (!stage || !loaded || !isLatest()) return;
    paintOverlay(false);
    applyMorph();
    stage.draw();
  }

  /** Paint what is loaded, with the current colouring and explode factor. */
  function paint() {
    var stage = _objectStage;
    if (!stage || !loaded || !isLatest()) return;
    var THREE = stage.THREE;
    stage.clear();
    applyThreshold();

    // Colours come from the report's full structure list, not the drawn subset, so turning a
    // structure off does not recolour the ones left.
    var colourOf;
    var offFocus = function(){ return false; };
    rampHost.replaceChildren();
    var inSubject = loaded.instances.filter(function(item){
      return !explain || item.entity === subject;
    });
    if (sceneState.colourBy === 'metric') {
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
        rampHost.appendChild(colourRamp({
          label: explain && explain.valueLabel ? explain.valueLabel : labelFor(sceneState.metric),
          low: low, high: high, ramp: byDistance ? 'distance' : 'viridis' }));
      }
    } else if (sceneState.colourBy === 'group') {
      var keys = inSubject.map(function(i){ return i.entity + '|' + i.label; });
      var groups = contactGroups(keys, loaded.edges);
      var aspect = explain && explain.kind === 'group' ? explain.aspect : 'size';
      var alone = [0.34, 0.37, 0.42].map(toLinear);
      var rootOfItem = function(item){ return groups.rootOf(item.entity + '|' + item.label); };
      if (aspect === 'share') {
        // In a group or not, and nothing more: the chart is the share that are.
        var own = toLinearRgb(litEntColour(subject));
        colourOf = function(item){ return groups.sizeOfRoot(rootOfItem(item)) < 2 ? alone : own; };
      } else if (aspect === 'reach' || aspect === 'both' || aspect === 'baseline') {
        // How close a group gets to the structure, on the hue-free ramp.
        var reach = groupReach(), all = [];
        reach.values.forEach(function(v){ if (v !== null) all.push(v); });
        var lowest = Math.min.apply(null, all), highest = Math.max.apply(null, all);
        colourOf = function(item) {
          var v = reach.values.get(rootOfItem(item));
          return v === null || v === undefined ? alone : distRampAt((v - lowest) / ((highest - lowest) || 1));
        };
        if (all.length) {
          rampHost.appendChild(colourRamp({ label: explain.valueLabel, low: lowest, high: highest, ramp: 'distance' }));
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
        rampHost.appendChild(colourRamp({ label: 'Instances in the group (log scale)', low: 2, high: biggest, whole: true }));
      }
      // One group at a time: the one on show keeps its colour on the scale and every other
      // instance is grey, so a single network that holds nearly everything is one thing to look at.
      if (explain && explain.kind === 'group' && aspect !== 'share') {
        var order = [], seen = {};
        keys.forEach(function(k) {
          var root = groups.rootOf(k);
          if (!seen[root] && groups.sizeOfRoot(root) >= 2) { seen[root] = true; order.push(root); }
        });
        var rank = function(root) {
          if (aspect === 'size' || !aspect) return -groups.sizeOfRoot(root);
          var v = groupReach().values.get(root);
          return v === null || v === undefined ? Infinity : v;
        };
        order.sort(function(a, b){ return rank(a) - rank(b); });
        if (order.length) {
          sceneState.groupAt = ((sceneState.groupAt || 0) % order.length + order.length) % order.length;
          var focus = order[sceneState.groupAt];
          // Every other group stays in the picture, faint, so the one on show is the only thing with weight.
          offFocus = function(item) { return item.entity === subject && rootOfItem(item) !== focus; };
          sceneState.groupLabel = 'Group ' + (sceneState.groupAt + 1) + ' of ' + order.length
              + ' \u00b7 ' + groups.sizeOfRoot(focus).toLocaleString() + ' instances';
          if (groupLabelEl) groupLabelEl.textContent = sceneState.groupLabel;
          sceneState.groupCount = order.length;
        }
      }
    } else {
      colourOf = function(item){ return toLinearRgb(litEntColour(item.entity)); };
    }

    // Faint where the chart being explained is not about it: the structures it only
    // measures against, and whatever a slider has put out of play.
    var ghostly = function(item) {
      return !!explain && (!shown.has(item.entity) || item.dim === true || offFocus(item));
    };
    var asItem = function(item) {
      return { geometry: item.geometry, kind: item.kind, flat: item.flat, rgb: colourOf(item),
               offset: explodeOffset(item.polar, sceneState.explode) };
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
    var solid = loaded.instances.filter(function(i){ return !i.flat && !ghostly(i); })
      .map(asItem);
    var faint = loaded.instances.filter(function(i){ return !i.flat && ghostly(i) && !isTarget(i); })
      .map(asGhost);
    var targetItems = loaded.instances.filter(function(i){ return !i.flat && isTarget(i); })
      .map(function(i) { var o = asItem(i); o.rgb = toLinearRgb(REFERENCE_COLOUR); return o; });

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
    }
    var faintSurfaces = mergeInstances(THREE, faint);
    if (faintSurfaces) {
      stage.scene.add(new THREE.Mesh(faintSurfaces, new THREE.MeshPhongMaterial({
        vertexColors: true, side: THREE.DoubleSide, shininess: 14, specular: 0x0d0d10,
        transparent: true, opacity: 0.16, depthWrite: false })));
    }
    var outlines = mergeInstances(THREE, loaded.instances
      .filter(function(i){ return i.flat && !ghostly(i); }).map(asItem));
    if (outlines) {
      stage.scene.add(new THREE.LineSegments(outlines,
        new THREE.LineBasicMaterial({ vertexColors: true })));
    }
    var targetSurfaces = mergeInstances(THREE, targetItems);
    if (targetSurfaces) {
      stage.scene.add(new THREE.Mesh(targetSurfaces, new THREE.MeshPhongMaterial({
        vertexColors: true, side: THREE.DoubleSide, shininess: 20, specular: 0x0d0d10,
        transparent: true, opacity: 0.5, depthWrite: false })));
    }
    var faintOutlines = mergeInstances(THREE, loaded.instances
      .filter(function(i){ return i.flat && ghostly(i); }).map(asGhost));
    if (faintOutlines) {
      stage.scene.add(new THREE.LineSegments(faintOutlines, new THREE.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.3 })));
    }

    // Only the mask that bounds the object is see-through, and it has to be: it encloses
    // everything else, so drawn solid it is the only thing you would see. The other
    // whole-structure masks are shapes in their own right and are drawn like the instances
    // are - solid, shaded, and lit. Never merged, so each keeps its own material.
    // In a plane a boundary line does the enclosing job without any transparency.
    var bounding = (OBJECTS[0] || {}).object_mask_name;
    loaded.masks.forEach(function(mask) {
      var geometry = mergeInstances(THREE, [{ geometry: mask.geometry,
        rgb: explain ? toLinearRgb(((explain.targets || []).indexOf(mask.entity) >= 0) ? REFERENCE_COLOUR : litEntColour(mask.entity)) : colourOf(mask) }]);
      if (!geometry) return;
      var asContext = ghostly(mask);
      if (mask.flat) {
        stage.scene.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
          vertexColors: true, transparent: true, opacity: asContext ? 0.3 : 0.55 })));
        return;
      }
      var encloses = bounding != null && mask.entity === String(bounding);
      var seeThrough = encloses || asContext;
      var targeted = !!explain && (explain.targets || []).indexOf(mask.entity) >= 0;
      var surface = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
        vertexColors: true, side: THREE.DoubleSide,
        shininess: 14, specular: 0x0d0d10,
        transparent: seeThrough, opacity: encloses ? (targeted ? 0.3 : 0.12) : targeted ? 0.5 : asContext ? 0.16 : 1,
        depthWrite: !seeThrough
      }));
      if (!seeThrough) { surface.castShadow = true; surface.receiveShadow = true; }
      stage.scene.add(surface);
    });

    paintOverlay(false);
    applyMorph();
    stage.frameShadow();
    stage.draw();
    // Still pictures spin gently; a chart being explained holds still, so what the slider
    // does is the only thing that moves.
    stage.spin(!explain);
    writeExplain();
  }

  // The readout under the explanation, kept so a slider can rewrite it as it moves.
  var readoutEl = null;

  /** The range of what a metric-coloured chart plots, over the instances on screen. */
  function subjectValues() {
    return loaded ? loaded.instances.filter(function(i){ return i.entity === subject; })
      .map(function(i){ return i.metric; })
      .filter(function(v){ return v !== null && isFinite(v); }) : [];
  }

  /** The groups on screen: their sizes, largest first, and how many instances touch nothing. */
  function currentGroups() {
    var keys = loaded.instances.filter(function(i){ return i.entity === subject; })
      .map(function(i){ return i.entity + '|' + i.label; });
    var groups = contactGroups(keys, loaded.edges);
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

  /** What the chart's number is, counted on this object, as the slider currently has it. */
  function readoutHtml(live) {
    if (!explain || !loaded) return '';
    var named = dataName(subject || '');
    if (explain.kind === 'polarity') {
      var frame = loaded.frame;
      if (!frame) return 'Nothing to measure from: ' + escHtml(explain.ref)
        + ' has no direction from the centre in this ' + noun() + '.';
      var total = frame.units.length.toLocaleString();
      var indices = 'R <b>' + (frame.R === null ? '–' : frame.R.toFixed(2)) + '</b>, V <b>'
        + (frame.V === null ? '–' : frame.V.toFixed(2)) + '</b> for this ' + noun();
      if (explain.aspect === 'angle') {
        var wedge = live === undefined ? sceneState.wedge : live;
        var width = 180 / POLAR_ANGLE_BINS;
        var here = frame.units.filter(function(u){ return polarBinOf(u.angle) === wedge; });
        var whole = frame.units.reduce(function(n, u){ return n + u.weight; }, 0);
        var share = here.reduce(function(n, u){ return n + u.weight; }, 0) / (whole || 1);
        return '<b>' + here.length.toLocaleString() + '</b> of <b>' + total + '</b> ' + named
          + ' lie between <b>' + Math.round(wedge * width) + '°</b> and <b>'
          + Math.round((wedge + 1) * width) + '°</b> of ' + dataName(explain.ref) + ', <b>'
          + fmtPct(share, 1) + '</b> of them by size: the height of this wedge<br>' + indices;
      }
      if (explain.aspect === 'index') return '<b>' + total + '</b> ' + named + '<br>' + indices;
      if (explain.aspect === 'where') {
        return '<b>' + total + '</b> ' + named + ', each at its angle to ' + dataName(explain.ref)
          + ' and its distance from the centre';
      }
      var counts = frame.groups.filter(function(g){ return g.units.length; }).map(function(g) {
        return '<b>' + g.units.length.toLocaleString() + '</b> ' + escHtml(g.name);
      });
      return counts.join(' \u00b7 ') + '<br>in ' + escHtml(String(current.id))
        + ', each structure one diamond in the plot';
    }
    if (explain.kind === 'group') {
      var gap = live === undefined ? sceneState.gap : live;
      var found = currentGroups();
      var grouped = found.sizes.reduce(function(n, v){ return n + v; }, 0);
      var largest = found.sizes[0] || 0;
      var within = '<b>' + fmtGap(gap) + ' \u00b5m</b>';
      if (explain.aspect === 'share') {
        return '<b>' + grouped.toLocaleString() + '</b> of <b>' + found.total.toLocaleString() + '</b> ' + named
          + ' (<b>' + fmtPct(grouped, found.total) + '</b>) are in a group when touching means within ' + within
          + '<br><b>' + found.alone.toLocaleString() + '</b> touch nothing';
      }
      if (explain.aspect === 'reach' || explain.aspect === 'both' || explain.aspect === 'baseline') {
        var reach = groupReach(), values = [];
        reach.values.forEach(function(v){ if (v !== null) values.push(v); });
        if (!values.length) return 'No group of ' + named + ' has a measured distance here.';
        var at = live !== undefined && explain.__sliding ? live : (sceneState.threshold === null ? Math.max.apply(null, values) : sceneState.threshold);
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
    if (explain.kind === 'structure') return '';
    var values = subjectValues();
    if (!values.length) return 'None of the ' + named + ' here has this measurement.';
    var top = Math.max.apply(null, values);
    var at = live !== undefined ? live : (sceneState.threshold === null ? top : sceneState.threshold);
    var under = values.filter(function(v){ return v <= at; }).length;
    return '<b>' + under.toLocaleString() + '</b> of <b>' + values.length.toLocaleString()
      + '</b> ' + named + ' at or below <b>' + fmtValue(at) + (explain.unit ? ' ' + explain.unit : '')
      + '</b>';
  }

  var stripEl = null;
  // The line naming the group on show, and the update that moves it; set by buildExplain.
  var groupLabelEl = null;
  /**
   * One bar per group, tallest first, in the colour that group has in the cell: the very
   * values the group-size chart plots, set out so a bar can be matched to a blob.
   */
  function drawGroupStrip() {
    if (!stripEl || !loaded) return;
    var found = currentGroups();
    var width = stripEl.clientWidth || 300, height = 64, ratio = window.devicePixelRatio || 1;
    stripEl.width = width * ratio; stripEl.height = height * ratio;
    stripEl.style.height = height + 'px';
    var g = stripEl.getContext('2d');
    g.scale(ratio, ratio);
    g.clearRect(0, 0, width, height);
    var aspect = explain.aspect;
    if (aspect === 'share') {
      // The two parts of the share, as one bar: in a group, and touching nothing.
      var inGroup = found.sizes.reduce(function(n, v){ return n + v; }, 0);
      var wide = found.total ? width * inGroup / found.total : 0;
      g.fillStyle = litEntColour(subject); g.fillRect(0, 16, wide, 32);
      g.fillStyle = '#586070'; g.fillRect(wide, 16, width - wide, 32);
      return;
    }
    if (aspect === 'reach' || aspect === 'both' || aspect === 'baseline') {
      var values = [];
      groupReach().values.forEach(function(v){ if (v !== null) values.push(v); });
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
  function writeExplain(live) {
    if (readoutEl) readoutEl.innerHTML = readoutHtml(live);
    drawGroupStrip();
  }

  /**
   * The panel under the viewer: what the pinned chart is about, the slider that ties the
   * picture to it, and a count of what the picture shows. Built once per load - the
   * sliders keep their grip while the scene repaints behind them.
   */
  function buildExplain(summary) {
    stopWedge();
    explainEl.replaceChildren();
    readoutEl = null;
    stripEl = null;
    groupLabelEl = null;
    // Nothing pinned: the status line above the viewer says what it is showing, and a box here
    // saying the same again was the first thing a reader met and the least useful.
    if (!explain) return;
    var body = document.createElement('p');
    body.className = 'explain-body';
    body.innerHTML = explain.text;
    var scope = document.createElement('p');
    scope.className = 'explain-scope';
    var pooled = scopedObjectNames().length;
    scope.textContent = 'Drawn for ' + current.id + ' alone' + (pooled > 1
      ? '; the chart pools ' + pooled + ' ' + noun(true) + '.' : '.');
    // What to touch and what it says come first, under the picture; the words explaining the
    // picture follow, since they are read once and the slider is used again and again.

    if (explain.kind !== 'polarity') {
      // What is drawn, in what: the cell's boundary, the structure being measured and - for a
      // distance - the one it is measured to, in the colour that structure has everywhere.
      var legend = document.createElement('div');
      legend.className = 'explain-key';
      var chip = function(colour, words, outline) {
        return '<span><i class="sw" style="background:' + colour + (outline ? ';box-shadow:0 0 0 1px rgba(20,24,29,.3)' : '')
          + '"></i>' + words + '</span>';
      };
      var parts = [];
      if (explain.kind === 'distance' || explain.kind === 'both') {
        parts.push('<span><i class="sw ramp ' + (explain.kind === 'metric' ? 'viridis' : 'lum')
          + '"></i>' + escHtml(explain.entity) + (explain.kind === 'metric' ? ', by value' : ', by distance \u2013 magenta is near, cyan is far') + '</span>');
      }
      (explain.targets || []).forEach(function(t) {
        parts.push(chip(REFERENCE_COLOUR, escHtml(t) + ' \u2013 measured to, drawn see-through'));
      });
      if (explain.kind === 'group') {
        var a2 = explain.aspect;
        if (a2 === 'share') parts.push(chip(litEntColour(explain.entity), escHtml(explain.entity) + ' in a group'));
        else if (a2 === 'size' || !a2) parts.push('<span><i class="sw ramp viridis"></i>groups, by size</span>');
        else parts.push('<span><i class="sw ramp lum"></i>groups, by closest approach \u2013 magenta is near</span>');
        parts.push(chip(explain.aspect === 'share' ? '#586070' : '#c5c9d0', explain.aspect === 'share' ? 'touches nothing' : 'other groups, faint', explain.aspect !== 'share'));
      }
      if (explain.kind === 'structure') parts.push(chip(litEntColour(explain.entity), escHtml(explain.entity)));
      if (loaded && explain.kind !== 'metric' && (explain.targets || []).indexOf(boundingMaskName()) < 0
          && loaded.masks.concat(loaded.instances).some(function(i){ return i.entity === boundingMaskName(); })) {
        parts.push(chip(litEntColour(boundingMaskName()), 'the ' + noun() + ' boundary', true));
      }
      legend.innerHTML = parts.join('');
      explainEl.appendChild(legend);
    }
    if (explain.kind === 'polarity' && loaded && loaded.frame) {
      var key = document.createElement('div');
      key.className = 'explain-key';
      var swatch = function(colour, words) {
        return '<span><i style="background:' + colour + '"></i>' + words + '</span>';
      };
      var aspect = explain.aspect;
      key.innerHTML = swatch('#5a6e88', 'towards ' + escHtml(explain.ref) + ' (0\u00b0)')
        + (aspect === 'angle'
            ? swatch('#1b222b', 'the wedge of angles, and the average direction (length R)')
            : aspect === 'index'
              ? swatch(litEntColour(explain.structure), escHtml(explain.structure) + ', one dot each')
                + swatch('#1b222b', 'the average (length R); the dark dot is V')
              : (aspect === 'map' || aspect === 'cellmap')
                ? polarSubjects(explain.ref).map(function(n){ return swatch(litEntColour(n), escHtml(n)); }).join('')
                  + swatch(litEntColour(explain.ref), escHtml(explain.ref) + ' (the reference)')
                  + '<span>\u25c6 one point per structure</span>'
                : swatch(litEntColour(explain.structure), escHtml(explain.structure) + ', one dot each'));
      explainEl.appendChild(key);
      if (aspect === 'angle') {
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
        var shares = [], whole = loaded.frame.units.reduce(function(n, u){ return n + u.weight; }, 0) || 1;
        for (var w = 0; w < POLAR_ANGLE_BINS; w++) shares.push(0);
        loaded.frame.units.forEach(function(u){ shares[polarBinOf(u.angle)] += u.weight / whole; });
        var tallest = Math.max.apply(null, shares) || 1;
        var buttons = shares.map(function(share, i) {
          var bar = document.createElement('button');
          bar.type = 'button';
          bar.className = 'wedge-bar';
          bar.title = Math.round(i * binWidth) + '\u00b0\u2013' + Math.round((i + 1) * binWidth) + '\u00b0: '
            + fmtPct(share, 1) + ' of the ' + explain.structure + ', by size';
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
          paint();
          writeExplain();
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
        explainEl.appendChild(rail);
        show();
        startWedge();
      } else if (aspect !== 'index') {
        explainEl.appendChild(slider('Showing', {
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
          onInput: function(v){ sceneState.t = v; refreshPolar(); }
        }, function(v){ sceneState.t = v; refreshPolar(); }));
      }
    }
    if (explain.kind === 'group' && explain.aspect !== 'share') {
      // The groups are walked through one at a time: it plays by itself, and the arrows step by hand.
      var gRail = document.createElement('div');
      gRail.className = 'wedge-rail';
      var gPlay = document.createElement('button');
      gPlay.type = 'button';
      gPlay.className = 'wedge-play';
      var gBack = document.createElement('button'), gNext = document.createElement('button');
      gBack.type = gNext.type = 'button';
      gBack.className = gNext.className = 'btn btn-sm btn-outline-secondary align-self-center';
      gBack.textContent = '\u25c0'; gNext.textContent = '\u25b6';
      gBack.title = 'Previous group'; gNext.title = 'Next group';
      groupLabelEl = document.createElement('div');
      groupLabelEl.className = 'align-self-center small';
      groupLabelEl.textContent = sceneState.groupLabel || '';
      var gShow = function() {
        gPlay.textContent = sceneState.groupPaused ? '\u25b6' : '\u275a\u275a';
        gPlay.title = sceneState.groupPaused ? 'Play: step through the groups' : 'Pause';
        gPlay.setAttribute('aria-label', gPlay.title);
      };
      var gGo = function(by) { sceneState.groupAt = (sceneState.groupAt || 0) + by; paint(); };
      var gStop = function() { sceneState.groupPaused = true; stopWedge(); gShow(); };
      var gStart = function() {
        stopWedge();
        if (sceneState.groupPaused) return;
        var step = function() {
          _wedgeTimer = null;
          if (!document.body.contains(gRail)) return;
          if (!document.hidden) {
            var began = performance.now();
            gGo(1);
            if (performance.now() - began > 1200) { gStop(); return; }
          }
          _wedgeTimer = setTimeout(step, 1600);
        };
        _wedgeTimer = setTimeout(step, 1600);
      };
      gPlay.addEventListener('click', function() {
        sceneState.groupPaused = !sceneState.groupPaused; gShow(); gStart();
      });
      gBack.addEventListener('click', function(){ gStop(); gGo(-1); });
      gNext.addEventListener('click', function(){ gStop(); gGo(1); });
      if (sceneState.groupPaused === undefined || sceneState.groupPaused === null) {
        sceneState.groupPaused = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      }
      gRail.append(gPlay, gBack, gNext, groupLabelEl);
      explainEl.insertBefore(gRail, explainEl.firstChild);
      gShow();
      gStart();
    }
    // A chart of one measurement is explained by the status line above the viewer and the key
    // under it; a slider, a count and a caption beneath said the same again.
    if (explain.kind !== 'structure') return;
    readoutEl = document.createElement('div');
    readoutEl.className = 'explain-readout';
    explainEl.append(readoutEl, body, scope);
    writeExplain();
  }

  async function draw(refit) {
    var done = busy(holder, _geomCache.summary ? 'Drawing…' : 'Reading geometry…');
    try {
      var summary = await readSummary();
      if (!isLatest()) return;
      if (sceneState.entities === null) {
        // Every structure the report holds is on to begin with.
        sceneState.entities = new Set(summary.entities.map(function(e){ return e.name; }));
      }
      if (sceneState.maxGap === null) {
        sceneState.maxGap = summary.widestGap;
        sceneState.gap = summary.widestGap === null ? null : summary.widestGap * 0.25;
      }
      // A chart of contact groups has a gap of its own; the viewer takes it from there.
      if (explain && explain.kind === 'group' && contactGapUm() !== null) {
        sceneState.gap = contactGapUm();
      }
      var read = await readGeometry(summary);
      if (!read || !isLatest()) return;
      loaded = read;
      if (!_objectStage) {
        _objectStage = makeStage(await loadThree(), canvas);
        new ResizeObserver(function() {
          if (_objectStage) _objectStage.resize(holder.clientWidth, holder.clientHeight);
        }).observe(holder);
      }
      paint();
      _objectStage.resize(holder.clientWidth, holder.clientHeight);
      // Framed once per object: pinning a chart changes what is drawn and how, not where the
      // reader was looking, and turning the camera back each time would undo their work.
      if (refit !== false && _framedFor !== current.id) {
        _objectStage.fit();
        _framedFor = current.id;
      }

      buildControls(summary);
      buildExplain(summary);
      syncRoseMarkers();
      syncMapMarkers();
      // Said, not left as an empty box: the chart is about something this object has no
      // surface for - a structure written without geometry, say.
      if (explain && !loaded.instances.concat(loaded.masks).some(function(i){ return i.entity === subject; })) {
        noteEl.textContent = 'There is no geometry for ' + subject + ' in this ' + noun()
          + ', so there is nothing to draw it from.';
      }
    } catch (error) {
      holder.style.display = 'none';
      noteEl.textContent = 'Could not read the geometry: ' + geometryProblem(error);
    } finally {
      done();
    }
  }

  function buildControls(summary) {
    controls.replaceChildren();
    if (objects.length > 1) {
      var shortOf = new Map(objects.map(function(o) {
        return [o.id, o.id.length > 34 ? '…' + o.id.slice(-32) : o.id];
      }));
      controls.appendChild(selector(Noun(), objects.map(function(o){ return o.id; }),
        current.id, function(value) {
          sceneState.objectId = value;
          sceneState.entities = explain ? new Set(explainEntities(explain)) : null;
          sceneState.maxGap = null;
          renderScene();
        }, shortOf));
    }
    // Which group the cell on show belongs to, in the colour the charts give that group.
    var currentRow = OBJECTS.find(function(o){ return String(o.object_id) === String(current.id); });
    if (currentRow && currentRow.group_id) {
      var badge = document.createElement('span');
      badge.className = 'cell-group';
      badge.innerHTML = '<i style="background:' + groupColor(currentRow.group_id) + '"></i>' + escHtml(currentRow.group_id);
      controls.appendChild(badge);
    }

    // A chart being explained has already said how things are coloured and which structures
    // matter, so the free controls would only contradict it.
    if (explain) return;
    var colourings = ['entity', 'metric'];
    if (summary.widestGap !== null) colourings.push('group');
    controls.appendChild(selector('Colour by', colourings, sceneState.colourBy, function(value) {
      sceneState.colourBy = value;
      buildControls(summary);
      if (value === 'group') draw(false);
      else paint();
    }, new Map([['entity', 'structure'], ['metric', 'a metric'], ['group', 'contact group']])));

    if (sceneState.colourBy === 'metric') {
      controls.appendChild(selector('Metric', metrics, sceneState.metric, function(value) {
        sceneState.metric = value;
        draw(false);
      }, new Map(metrics.map(function(m){ return [m, labelFor(m)]; }))));
    }
    if (sceneState.colourBy === 'group' && sceneState.maxGap) {
      controls.appendChild(slider('Gap ≤', {
        min: 0, max: sceneState.maxGap, step: sceneState.maxGap / 100, value: sceneState.gap,
        format: function(v){ return v.toFixed(3) + ' µm'; }
      }, function(value) {
        sceneState.gap = value;
        draw(false);
      }));
    }

    // Exploding needs each instance's direction from the object centre, which rides in the
    // geometry file. Geometry written before that was carried has none, and a slider that
    // silently does nothing is worse than no slider.
    if (loaded && loaded.instances.some(function(item){ return item.polar.dist > 0; })) {
      controls.appendChild(slider('Explode', {
        min: 0, max: 2, step: 0.1, value: sceneState.explode,
        format: function(v){ return v.toFixed(1) + '×'; }
      }, function(value) {
        sceneState.explode = value;
        paint();
        if (_objectStage) _objectStage.fit();
      }));
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
        draw(false);
      });
      var swatch = document.createElement('span');
      swatch.className = 'dot';
      swatch.style.background = litEntColour(entity.name);
      label.className = 'scene-chip';
      label.append(box, swatch, entity.name);
      chips.appendChild(label);
    });
    controls.appendChild(chips);
  }

  // Only the latest gap is drawn: dragging the chart's slider asks faster than the contacts
  // can be read, and a stale answer arriving last would put the viewer behind the chart.
  var gapAsked = 0;
  _sceneApi = {
    setGap: async function(gap) {
      var mine = ++gapAsked;
      if (!loaded || !explain || explain.kind !== 'group') return;
      sceneState.gap = gap;
      var track = explainEl.querySelector('input[type=range]');
      if (track) {
        track.value = gap;
        track.dispatchEvent(new Event('input'));
      }
      var edges = await readEdges();
      if (mine !== gapAsked) return;
      loaded.edges = edges;
      paint();
    }
  };

  await draw(true);
}
