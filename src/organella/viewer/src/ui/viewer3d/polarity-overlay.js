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

  // In a plane an angle from the axis is two sectors, one either side of it, and the bins of the
  // rose cut the whole disc into such pairs: every bin is outlined, the one on show is filled.
  if (o.aspect === 'angle' && o.planar) {
    var binRad = Math.PI / POLAR_ANGLE_BINS, side = [-a[1], a[0], 0];
    var rimAt = function(tt, sign) {
      var dir = polAdded(polScaled(a, Math.cos(tt)), polScaled(side, sign * Math.sin(tt)));
      return [c[0] + dir[0] * L, c[1] + dir[1] * L, c[2] + dir[2] * L];
    };
    var edges = [];
    for (var e = 0; e <= POLAR_ANGLE_BINS; e++) {
      [1, -1].forEach(function(sign){ edges.push([c, rimAt(e * binRad, sign)]); });
    }
    segments(edges, BLUE, 0.22);
    [1, -1].forEach(function(sign) {
      var arc = [], fan = [], STEPS = 12;
      for (var n = 0; n <= STEPS; n++) arc.push(rimAt((o.wedge + n / STEPS) * binRad, sign));
      for (var m = 0; m < STEPS; m++) fan.push(c, arc[m], arc[m + 1]);
      var sector = new THREE.BufferGeometry();
      sector.setAttribute('position', new THREE.Float32BufferAttribute([].concat.apply([], fan), 3));
      group.add(new THREE.Mesh(sector, new THREE.MeshBasicMaterial({
        color: BLUE, transparent: true, opacity: 0.2, side: THREE.DoubleSide, depthWrite: false, depthTest: false })));
      line([c].concat(arc, [c]), BLUE, 1);
    });
  // The shell of angles between two cones about the axis: what one wedge of the rose counts.
  } else if (o.aspect === 'angle') {
    var loRad = o.wedge * Math.PI / POLAR_ANGLE_BINS, hiRad = loRad + Math.PI / POLAR_ANGLE_BINS;
    var across = acrossAxis(a);
    var K = 48, J = 4, rings = [];
    for (var j = 0; j <= J; j++) {
      var tt = loRad + (hiRad - loRad) * j / J, ring = [];
      for (var k = 0; k < K; k++) {
        var phi = 2 * Math.PI * k / K;
        var sideways = polAdded(polScaled(across[0], Math.cos(phi)), polScaled(across[1], Math.sin(phi)));
        var dir = polAdded(polScaled(a, Math.cos(tt)), polScaled(sideways, Math.sin(tt)));
        ring.push([c[0] + dir[0] * L, c[1] + dir[1] * L, c[2] + dir[2] * L]);
      }
      rings.push(ring);
    }
    var tri = [];
    for (var r = 1; r <= J; r++) {
      for (var q = 0; q < K; q++) {
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
      var loop = new THREE.LineLoop(
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
