/**
 * Everything the report can say about direction, made comparable between objects.
 *
 * One reference structure turns every measured direction into an angle against something
 * inside the object (see the block above): the distributions of that angle, Polarity-JaM's
 * R and V per object, the joint reading of angle against distance, and one circular map per
 * object. The reference is the reader's to choose, and the section is only drawn where
 * something has a direction at all.
 */
function renderPolarity() {
  var section = document.getElementById('s-polarity');
  ['pol-angle-charts', 'pol-angle-fold', 'pol-index-charts', 'pol-where-charts', 'pol-map-charts', 'pol-map-fold']
    .forEach(function(id){ document.getElementById(id).innerHTML = ''; });

  var references = polarReferences();
  if (!references.length) {
    section.classList.add('d-none');
    return;
  }
  section.classList.remove('d-none');

  // The most off-centre structure to begin with: it makes the best-defined axis, and how
  // far off centre each one is stands beside the choice so a poor one is visible as one.
  if (st.polarRef == null || !references.some(function(r){ return r.name === st.polarRef; })) {
    st.polarRef = references[0].name;
  }
  var ref = st.polarRef;
  var select = document.getElementById('pol-reference');
  select.innerHTML = references.map(function(r) {
    return '<option value="' + escHtml(r.name) + '"' + (r.name === ref ? ' selected' : '')
      + '>' + escHtml(r.name) + ' (' + fmtGap(r.dist) + ' µm off centre, in '
      + (r.objects === r.of ? 'all ' + r.of : r.objects + ' of ' + r.of) + ' '
      + noun(r.of !== 1) + ')</option>';
  }).join('');
  select.onchange = function() {
    st.polarRef = select.value;
    renderPolarity();
  };
  var chosen = references.filter(function(r){ return r.name === ref; })[0];
  setHtml('pol-subtitle',
    'Directions in the data are measured from the centre of the ' + noun() + ', and a ' + noun()
    + ' can lie in any orientation, so an angle on its own cannot be compared between ' + noun(true)
    + '. Here every direction is measured against the line from the centre of the ' + noun()
    + ' to the centre of ' + dataName(ref) + ': <b>0° points towards</b> ' + dataName(ref)
    + ' and <b>180° points away</b> from it. Angles measured this way can be compared between '
    + noun(true) + ' and pooled.');
  // Only the thing the choice does not already say: how far off centre it is stands in
  // the dropdown beside every option.
  setHtml('pol-axis', chosen.objects < chosen.of
    ? '<b>' + (chosen.of - chosen.objects) + ' of ' + chosen.of + ' ' + noun(true)
      + ' have no ' + escHtml(chosen.name) + ' to measure against, and are left out of'
      + ' everything below.</b>'
    : '');

  // ── every direction, as an angle to that axis ──────────────────────────────
  //
  // An instance brings its centroid; a mask brings the one centre it has, which is why it
  // appears as a single reading per object, with no shape.
  var relative = {}, kinds = {};
  var mask = boundingMaskName();
  /**
   * How deep inside the bounding mask this reading sits.
   *
   * An instance has a distance row to that mask. A mask has none - it was measured as one
   * whole structure, with no instance to carry a distance - so its own row carries the
   * depth of its centre instead, which is the same question asked of the one point it has.
   */
  var depthOf = function(row){ return polarDepth(row, mask); };
  var take = function(name, kind, row) {
    var axis = polarOffset(row.object_id, ref);
    var unit = polarUnit(row);
    if (!axis || !unit || row.polar_dist_um == null || !isFinite(row.polar_dist_um)) return;
    kinds[name] = kind;
    var depth = depthOf(row);
    (relative[name] = relative[name] || []).push({
      object_id: row.object_id, group_id: row.group_id, unit: unit, weight: polarWeight(row),
      angle_deg: polarConeDeg(unit, axis.unit), dist_um: row.polar_dist_um,
      depth_um: depth
    });
  };
  // The reference's own centre, for the marker on 0°: the depth of the same centre the
  // axis points at, which its structure row carries whether it has instances or not.
  var refDepth = {};
  filteredEnts().forEach(function(r) {
    if (r.entity_name !== ref) return;
    var depth = r.polar_depth_um;
    if (depth != null && isFinite(depth)) refDepth[r.object_id] = depth;
  });
  filteredInstances().forEach(function(r) {
    if (r.entity_name !== ref) take(r.entity_name, 'label', r);
  });
  filteredEnts().forEach(function(r) {
    if (r.entity_kind !== 'mask' || r.entity_name === ref) return;
    if (OBJECTS.some(function(o){ return o.object_mask_name === r.entity_name; })) return;
    if (relative[r.entity_name] && kinds[r.entity_name] === 'label') return;
    take(r.entity_name, 'mask', r);
  });
  var structures = Object.keys(relative).sort(function(a, b) {
    return (kinds[a] === kinds[b]) ? a.localeCompare(b) : (kinds[a] === 'label' ? -1 : 1);
  });
  if (!structures.length) {
    section.classList.add('d-none');
    return;
  }
  // Offered only where there is a depth to read: without a bounding mask, or on a report
  // written without per-instance distances, there is no second radius to switch to.
  var hasDepth = Object.keys(relative).some(function(name) {
    return relative[name].some(function(r){ return r.depth_um != null; });
  });
  var radiusChoices = [['centre', 'from the ' + noun() + ' centre (µm)']];
  if (hasDepth) radiusChoices.push(['membrane', 'to the ' + mask + ' (µm)']);
  if (!hasDepth) st.polarRadius = 'centre';
  document.getElementById('pol-radius-wrap').style.display = hasDepth ? '' : 'none';
  // Only the maps are drawn against a radius, which is why the control sits with them.
  var radiusSelect = document.getElementById('pol-radius');
  radiusSelect.innerHTML = radiusChoices.map(function(choice) {
    return '<option value="' + choice[0] + '"'
      + (choice[0] === st.polarRadius ? ' selected' : '') + '>'
      + escHtml(choice[1]) + '</option>';
  }).join('');
  radiusSelect.onchange = function() {
    st.polarRadius = radiusSelect.value;
    renderPolarity();
  };
  var toMask = st.polarRadius === 'membrane' && hasDepth;
  // Whether a mask can be placed in that view at all, which decides what the row says.
  var masksHaveDepth = Object.keys(relative).some(function(name) {
    return kinds[name] === 'mask'
      && relative[name].some(function(r){ return r.depth_um != null; });
  });
  var radiusOf = function(r){ return toMask ? r.depth_um : r.dist_um; };

  // In group order, with each object's group to hand: two rows below are one panel per
  // object, and which condition an object belongs to is what a reader compares them for.
  var groupOfObject = {};
  OBJECTS.forEach(function(o){ groupOfObject[String(o.object_id)] = o.group_id || ''; });
  var mapObjects = scopedObjectNames().slice().sort(function(a, b) {
    return String(groupOfObject[a]).localeCompare(String(groupOfObject[b]))
      || String(a).localeCompare(String(b));
  });

  var facetsFor = function(name){ return facetsOf(relative[name]); };
  var legend = facetLegendHtml(facetsFor(structures[0]));
  ['pol-index-legend', 'pol-where-legend']
    .forEach(function(id){ setHtml(id, legend); });

  // The legend of the roses is the page's own, made clickable: a group pressed here is left out of every rose below, and
  // the arrows (one per object, its average direction) are switched on beside it.
  var roseFacets = facetsFor(structures[0]);
  var legendEl = document.getElementById('pol-legend');
  legendEl.innerHTML = (roseFacets.many ? roseFacets.values.map(function(v) {
      var off = !!st.polarHidden[String(v)];
      return '<button type="button" class="legend-toggle' + (off ? ' off' : '') + '" data-group="' + escHtml(String(v))
        + '" aria-pressed="' + !off + '" title="' + (off ? 'Show ' : 'Hide ') + escHtml(String(v)) + ' in these plots">'
        + '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + gColor(v) + '"></span>'
        + escHtml(String(v)) + '</button>';
    }).join('') : '')
    + '<label class="legend-switch"><input type="checkbox" id="pol-arrows"' + (st.polarArrows ? ' checked' : '')
    + '> Show each ' + noun() + '\u2019s average direction</label>';
  legendEl.onclick = function(event) {
    var chip = event.target.closest && event.target.closest('.legend-toggle');
    if (!chip) return;
    st.polarHidden[chip.dataset.group] = !st.polarHidden[chip.dataset.group];
    renderPolarity();
  };
  document.getElementById('pol-arrows').onchange = function() {
    st.polarArrows = this.checked;
    renderPolarity();
  };

  // ── which way it points: the spread and the average in one picture ────────
  //
  // Polarity-JaM's plot (their Fig 2C): the distribution of angles as a rose, with the
  // mean direction over it as an arrow whose *length* is the polarity index. Both readings
  // in one panel, because they are readings of one thing - and a box of angles beside two
  // boxes of indices said the same thing three times, with the box's brackets inviting a
  // test over instances that are not independent of each other.
  //
  // Half a disc: the angle to the axis runs 0° to 180° and the rotation about it is
  // arbitrary (see the maps below), so the missing half is not data. Their hemirose plots
  // are half for the same reason.
  setHtml('pol-angle-label', 'Which way does each structure point, relative to '
    + dataName(ref) + '?');
  setHtml('pol-angle-desc', 'Each <b>wedge</b> shows how much of the structure lies at that angle to the axis,'
    + ' with instances weighted by size. The <b>dotted outline</b>, one step across each wedge, is the share expected if the'
    + ' structure ignored the axis and lay anywhere in the ' + noun() + ': the directions of the ' + noun() + '’s own '
    + (PLANAR ? 'area' : 'volume') + ' seen from its centre, so the shape of the ' + noun() + ' is in it, one outline per group.'
    + ' Where a report did not measure that shape, it is the outline for ' + (PLANAR ? 'a circle' : 'a sphere') + ' instead.'
    + ' Wedge heights are relative: the tallest reaches the rim.'
    + ' With <b>Show each ' + noun() + '’s average direction</b> on, each <b>line with a dot at its end</b> is one ' + noun() + '. It runs from the centre in the average direction of the structure in that '
    + noun() + ', and its length is <b>R</b> measured against the rim as 1, from 0 (the directions cancel) to 1'
    + ' (all directions agree). A structure with a single centre per ' + noun() + ' is drawn as one dot.');
  var angleHost = document.getElementById('pol-angle-charts');
  var ANGLE_BINS = POLAR_ANGLE_BINS;
  // The share chance gives each wedge, as a dotted line stepping across the wedge's own width at its height: a wedge and what it is
  // compared with are the same shape, and the step between two wedges is a radial line.
  var steppedOutline = function(share, top, colour, hidden) {
    var ARC = 6, r = [], theta = [], width = 180 / ANGLE_BINS;
    share.forEach(function(v, b) {
      for (var k = 0; k <= ARC; k++) { r.push(v / top); theta.push((b + k / ARC) * width); }
    });
    return { type: 'scatterpolar', mode: 'lines', showlegend: false, visible: !hidden,
      r: r, theta: theta, line: { color: colour, width: 1.6, dash: 'dot' },
      hovertemplate: 'chance: %{customdata:.1f}%<extra></extra>',
      customdata: share.reduce(function(all, v){ for (var k = 0; k <= ARC; k++) all.push(100 * v); return all; }, []) };
  };
  // One arrow per object: its average direction, length R, at the angle to the axis. On the roses of the groups only when
  // asked for, since an arrow per object over the wedges of every group is more than a rose can carry.
  var arrowsOf = function(name, onlyId) {
    var isMask = kinds[name] === 'mask', traces = [];
    mapObjects.filter(function(id){ return onlyId === undefined || String(id) === String(onlyId); }).forEach(function(id) {
      var axis = polarOffset(id, ref);
      var mine = relative[name].filter(function(r){ return r.object_id === id; });
      var units = mine.map(function(r){ return r.unit; });
      if (!axis || !units.length) return;
      var found = polarityIndices(units, axis.unit, polarWeights(mine));
      if (!found || !found.mean) return;
      var at = polarConeDeg(found.mean, axis.unit);
      var group = groupOfObject[String(id)];
      if (onlyId === undefined && (st.polarHidden[String(group)] || st.polarHidden[String(id)])) return;
      var colour = group ? gColor(group) : entColor(name);
      traces.push(isMask
        ? { type: 'scatterpolar', mode: 'markers', name: String(id), r: [1], theta: [at],
            marker: { size: 9, color: colour, line: { color: '#fff', width: 1 } },
            showlegend: false,
            hovertemplate: String(id) + ': ' + escName(name) + ' at %{theta:.0f}°'
              + '<extra></extra>' }
        : { type: 'scatterpolar', mode: 'lines+markers', name: String(id),
            r: [0, found.R], theta: [at, at],
            line: { color: colour, width: 2 },
            marker: { size: [0, 8], color: colour }, showlegend: false,
            // The tip's R, whichever end of the arrow is hovered: its start has r = 0.
            customdata: [found.R, found.R],
            hovertemplate: String(id) + ': R %{customdata:.2f} at %{theta:.0f}°<br>V '
              + found.V.toFixed(2) + '<extra></extra>' });
    });
    return traces;
  };
  var roseLayout = function(name) {
    return {
      margin: { l: 44, r: 44, t: 40, b: 12 }, height: 340,
      title: { text: panelTitle(name, 'towards ' + ref) }, showlegend: false,
      polar: {
        sector: [0, 180], barmode: 'overlay',
        radialaxis: { range: [0, 1.02], angle: 90, nticks: 3, showticklabels: false },
        angularaxis: { direction: 'counterclockwise', rotation: 0, tickmode: 'array',
                       tickvals: [0, 90, 180], ticktext: [ref, '90°', 'away'],
                       tickfont: { size: 9 } }
      }
    };
  };
  structures.forEach(function(name) {
    var facets = facetsFor(name);
    var fallback = { name: name, color: entColor(name) };
    var isMask = kinds[name] === 'mask';
    var el = document.createElement('div');
    el.className = 'chart-cell';
    var roseEl = document.createElement('div');
    el.appendChild(roseEl);
    var traces = [];
    // The spread, as a share of each facet's own population per 15° bin, and the share an
    // isotropic population would have in the same bin. Both scaled by the same number, so
    // the two shapes can be read against each other.
    var width = 180 / ANGLE_BINS, centres = [], expected = [];
    for (var b = 0; b < ANGLE_BINS; b++) {
      var lo = b * width, hi = lo + width;
      centres.push(lo + width / 2);
      // In a plane every direction is one angle; in a volume a cone of them, and the solid
      // angle of that cone is what an isotropic population fills.
      expected.push(PLANAR ? width / 180
        : (Math.cos(lo * Math.PI / 180) - Math.cos(hi * Math.PI / 180)) / 2);
    }
    var shares = facets.split(relative[name], fallback).map(function(f) {
      // How much of the structure lies in each wedge, an instance counting by its size: three
      // labels of one nucleus, two of them specks, are one nucleus and not three.
      var counts = new Array(ANGLE_BINS).fill(0);
      var sizes = polarWeights(f.rows), whole = 0;
      f.rows.forEach(function(r, i) {
        var at = Math.min(ANGLE_BINS - 1, Math.floor(r.angle_deg / width));
        if (at >= 0) counts[at] += sizes[i];
        whole += sizes[i];
      });
      return { facet: f, share: counts.map(function(c){ return c / (whole || 1); }) };
    });
    // What each facet would have if its structure ignored the axis and lay anywhere in the object: the share of its
    // objects' own volume in each wedge, an object counting by how much of the structure it has - the way the
    // wedges themselves are pooled. Null where the report did not measure the object's shape.
    var chance = shares.map(function(x) {
      var sum = new Array(ANGLE_BINS).fill(0), whole = 0, byObject = {};
      var sizes = polarWeights(x.facet.rows);
      x.facet.rows.forEach(function(r, i){ byObject[r.object_id] = (byObject[r.object_id] || 0) + sizes[i]; });
      var complete = Object.keys(byObject).every(function(id) {
        var axis = polarOffset(id, ref), mine = axis && polarShapeShare(id, axis.unit);
        if (!mine) return false;
        mine.forEach(function(v, k){ sum[k] += v * byObject[id]; });
        whole += byObject[id];
        return true;
      });
      return complete && whole ? sum.map(function(v){ return v / whole; }) : null;
    });
    var top = Math.max.apply(null, [Math.max.apply(null, expected)].concat(
      shares.map(function(x){ return Math.max.apply(null, x.share); }),
      chance.filter(Boolean).map(function(c){ return Math.max.apply(null, c); })));
    if (!isMask) {
      shares.forEach(function(x) {
        traces.push({ type: 'barpolar', name: x.facet.label,
          r: x.share.map(function(v){ return v / top; }), theta: centres, width: width,
          marker: { color: withAlpha(x.facet.color, 0.35),
                    line: { color: x.facet.color, width: 1 } },
          // A group the legend above has switched off is left out, its outline with it.
          showlegend: false, visible: !st.polarHidden[x.facet.name],
          hovertemplate: '%{theta:.0f}°: %{customdata:.1f}% of ' + x.facet.name
            + '<extra></extra>',
          customdata: x.share.map(function(v){ return 100 * v; }) });
      });
      // One outline per facet where the shape of its objects is known; one for a uniform population where it is not.
      var outlines = chance.every(Boolean)
        ? chance.map(function(c, i){ return { share: c, colour: shares[i].facet.color, hidden: !!st.polarHidden[shares[i].facet.name] }; })
        : [{ share: expected, colour: CS.ink3, hidden: false }];
      outlines.forEach(function(o) {
        traces.push(steppedOutline(o.share, top, o.colour, o.hidden));
      });
    }
    // A structure with one centre per object has no wedges, so its readings are all there is to draw; for the others
    // the arrows are the reader's to switch on.
    if (isMask || st.polarArrows) traces = traces.concat(arrowsOf(name));
    if (!traces.length) return;
    angleHost.appendChild(el);
    drawPlot(roseEl, traces, roseLayout(name),
             { responsive: true, displayModeBar: false });
    // The cone marker of the viewer is drawn on this plot, so it has to be findable.
    roseEl.dataset.rose = '1';
    explainButton(el, polaritySpec(name, ref, 'angle'));
  });

  // Each object's own rose, kept out of the way: its wedges, its chance outline and its average vector, with R and V in the title,
  // for the one structure chosen above them. In a plane an object's angle has a side, so its rose is the full circle; in a volume
  // the rotation about the axis is arbitrary and the half rose stays.
  var foldStructures = structures.filter(function(name){ return kinds[name] !== 'mask'; });
  if (foldStructures.length) perObjectFold(document.getElementById('pol-angle-fold'),
    'Show each ' + noun() + '\u2019s own rose, with its average vector',
    function(body) {
      var bar = document.createElement('div');
      bar.className = 'd-flex align-items-center mb-2';
      bar.style.gap = '.4rem';
      var pick = document.createElement('select');
      pick.className = 'form-select form-select-sm';
      pick.style.width = 'auto';
      pick.innerHTML = foldStructures.map(function(n){ return '<option value="' + escHtml(n) + '">' + escHtml(n) + '</option>'; }).join('');
      var word = document.createElement('span');
      word.className = 'small';
      word.style.color = 'var(--ink-3)';
      word.textContent = 'Structure';
      bar.append(word, pick);
      var grid = document.createElement('div');
      grid.className = 'charts-row';
      body.append(bar, grid);
      var BINS = PLANAR ? 2 * ANGLE_BINS : ANGLE_BINS, BIN_DEG = 180 / ANGLE_BINS;
      // Where an angle goes on the circle: a plane's is signed (above the axis on screen is positive); a volume's folds onto 0..180.
      var thetaOf = function(unit, axisUnit) {
        return PLANAR ? polarSignedDeg(unit, axisUnit) : polarConeDeg(unit, axisUnit);
      };
      var draw = function(name) {
        grid.innerHTML = '';
        mapObjects.forEach(function(id) {
          var mine = relative[name].filter(function(r){ return String(r.object_id) === String(id); });
          var axis = polarOffset(id, ref);
          if (!mine.length || !axis) return;
          var weights = polarWeights(mine), share = new Array(BINS).fill(0), whole = 0;
          mine.forEach(function(r, i) {
            share[Math.min(BINS - 1, Math.floor(thetaOf(r.unit, axis.unit) / BIN_DEG))] += weights[i];
            whole += weights[i];
          });
          share = share.map(function(v){ return v / (whole || 1); });
          var group = groupOfObject[String(id)], colour = group ? gColor(group) : entColor(name);
          var chanceShare = (PLANAR ? polarShapeShareFull(id, axis.unit, BINS) : polarShapeShare(id, axis.unit))
            || share.map(function(_, b) {
              return PLANAR ? 1 / BINS : (Math.cos(b * Math.PI / ANGLE_BINS) - Math.cos((b + 1) * Math.PI / ANGLE_BINS)) / 2;
            });
          var found = polarityIndices(mine.map(function(r){ return r.unit; }), axis.unit, weights);
          var top = Math.max(Math.max.apply(null, share), Math.max.apply(null, chanceShare));
          var traces = [{ type: 'barpolar', name: String(id), showlegend: false,
            r: share.map(function(v){ return v / top; }),
            theta: share.map(function(_, b){ return (b + 0.5) * BIN_DEG; }), width: BIN_DEG,
            marker: { color: withAlpha(colour, 0.35), line: { color: colour, width: 1 } },
            hovertemplate: '%{theta:.0f}\u00b0: %{customdata:.1f}%<extra></extra>',
            customdata: share.map(function(v){ return 100 * v; }) },
            steppedOutline(chanceShare, top, colour, false)];
          // The arrow is R long, a stub where the directions mostly cancel: a faint ray carries its direction to the rim.
          if (found && found.mean) {
            var at = thetaOf(found.mean, axis.unit);
            traces.push({ type: 'scatterpolar', mode: 'lines', r: [0, 1], theta: [at, at], showlegend: false, hoverinfo: 'skip',
                          line: { color: '#1b222b', width: 1, dash: 'dash' }, opacity: 0.5 });
            traces.push({ type: 'scatterpolar', mode: 'lines+markers', name: String(id), r: [0, found.R], theta: [at, at],
                          line: { color: colour, width: 2 }, marker: { size: [0, 8], color: colour }, showlegend: false,
                          customdata: [found.R, found.R],
                          hovertemplate: 'R %{customdata:.2f} at %{theta:.0f}\u00b0<br>V ' + found.V.toFixed(2) + '<extra></extra>' });
          }
          var cell = document.createElement('div');
          cell.className = 'chart-cell';
          var plotEl = document.createElement('div');
          cell.appendChild(plotEl);
          grid.appendChild(cell);
          var layout = roseLayout(name);
          layout.title = { text: escHtml(String(id)) + (found ? '<br><span style="font-size:11px">R ' + found.R.toFixed(2) + ' \u00b7 V '
            + found.V.toFixed(2) + '</span>' : ''), y: 0.97, yanchor: 'top' };
          layout.height = 330; layout.margin.t = 84;
          if (PLANAR) {
            layout.margin.b = 24;
            layout.polar.sector = [0, 360];
            layout.polar.angularaxis.tickvals = [0, 90, 180, 270];
            layout.polar.angularaxis.ticktext = [ref, '90\u00b0', 'away', '270\u00b0'];
          }
          drawPlot(plotEl, traces, layout, { responsive: true, displayModeBar: false });
        });
      };
      if (foldStructures.indexOf(st.polarFold) < 0) st.polarFold = foldStructures[0];
      pick.value = st.polarFold;
      pick.onchange = function() { st.polarFold = pick.value; draw(pick.value); };
      draw(st.polarFold);
    });

  // ── the two collective measures, one per object ────────────────────────────
  setHtml('pol-index-label', 'How strongly does each structure lean towards '
    + dataName(ref) + '?');
  setHtml('pol-index-desc', '<b>V</b> is the average direction projected onto the axis, from +1 (all instances'
    + ' on the side of ' + dataName(ref) + ') to −1 (all on the opposite side). <b>R</b> is the length of'
    + ' the average direction, from 0 (the directions cancel) to 1 (all point the same way). A V near 0'
    + ' with a high R means the structure lies to one side that is not the side of ' + dataName(ref)
    + '; with a low R it means the structure is spread evenly around the ' + noun() + '. Each point'
    + ' is one ' + noun() + ', because instances within a ' + noun() + ' are not independent samples.'
    + ' V and R follow Polarity-JaM (Giese et al., <i>Nat Commun</i> 2025).');
  var indexHost = document.getElementById('pol-index-charts');
  structures.forEach(function(name) {
    var byObject = {};
    relative[name].forEach(function(r) {
      (byObject[r.object_id] = byObject[r.object_id] || []).push(r);
    });
    var rows = Object.keys(byObject).map(function(id) {
      var axis = polarOffset(id, ref);
      var found = polarityIndices(byObject[id].map(function(r){ return r.unit; }), axis.unit,
                                  polarWeights(byObject[id]));
      return { object_id: id, group_id: byObject[id][0].group_id,
               V: found.V, R: found.R, n: found.n };
    });
    var facets = facetsOf(rows);
    var fallback = { name: name, color: entColor(name) };
    // A mask is one centre and so one direction: its R is 1 whatever it does, and a point
    // plotted against R would read as a measurement. It keeps the one-number panel for V.
    if (kinds[name] === 'mask') {
      distributionPanel(indexHost, {
        series: facets.valuesOf(rows, 'V', fallback),
        title: panelTitle(name, 'V towards ' + ref), unit: 'V',
        noun: noun(), showFacetTicks: !facets.many,
        explain: polaritySpec(name, ref, 'index'),
        range: [-1, 1],
        help: '<b>V</b>, the signed polarity index: <b>+1</b> every ' + dataName(name)
          + ' towards ' + dataName(ref) + ', <b>0</b> spread evenly about the axis, <b>\u22121</b>'
          + ' every one away.'
      });
      return;
    }
    // V and R each get a distribution plot of their own, as every other measurement does: one value per object.
    distributionPanel(indexHost, {
      series: facets.valuesOf(rows, 'V', fallback),
      title: panelTitle(name, 'V towards ' + ref), unit: 'V',
      noun: noun(), showFacetTicks: !facets.many,
      explain: polaritySpec(name, ref, 'index'),
      range: [-1, 1],
      help: '<b>V</b>, the signed polarity index: <b>+1</b> every ' + dataName(name)
        + ' towards ' + dataName(ref) + ', <b>0</b> spread evenly about the axis, <b>\u22121</b>'
        + ' every one away.'
    });
    distributionPanel(indexHost, {
      series: facets.valuesOf(rows, 'R', fallback),
      title: panelTitle(name, 'R towards ' + ref), unit: 'R',
      noun: noun(), showFacetTicks: !facets.many,
      explain: polaritySpec(name, ref, 'index'),
      range: [0, 1],
      help: '<b>R</b>, how much the directions agree: <b>1</b> every ' + dataName(name) + ' points the same way,'
        + ' <b>0</b> they cancel out.'
    });
  });

  // ── angle against distance ─────────────────────────────────────────────────
  setHtml('pol-where-label', 'How far out does it sit, and which way?');
  setHtml('pol-where-desc', 'Each point is one instance, placed by its angle to the axis towards '
    + dataName(ref) + ' and its distance from the centre of the ' + noun() + '. The line is the median'
    + ' in bins holding equal numbers of instances, and the band covers the middle 50 percent.'
    + ' Structures with a single centre per ' + noun() + ' are not shown, since one point per '
    + noun() + ' gives no trend.');
  var whereHost = document.getElementById('pol-where-charts');
  structures.forEach(function(name) {
    if (kinds[name] !== 'label') return;
    var facets = facetsFor(name);
    var fallback = { name: name, color: entColor(name) };
    var el = document.createElement('div');
    el.className = 'chart-cell';
    var whereEl = document.createElement('div');
    el.appendChild(whereEl);
    whereHost.appendChild(el);
    var traces = [];
    facets.split(relative[name], fallback).forEach(function(f) {
      var points = f.rows.map(function(r){ return [r.angle_deg, r.dist_um]; });
      var trend = binnedTrend(points, 9);
      if (trend) {
        traces = traces.concat(trendTraces(trend, f.label, f.color,
          { band: true, xlabel: 'angle', ylabel: 'distance' }));
      } else {
        traces.push(pointTrace(points, f.label, f.color,
          { xlabel: 'angle', ylabel: 'distance' }));
      }
    });
    drawPlot(whereEl, traces, {
      margin: { l: 54, r: 6, t: 36, b: 44 }, height: PANEL_HEIGHT,
      title: { text: panelTitle(name, 'distance by angle') }, showlegend: false,
      xaxis: { title: 'angle to ' + ref + ' (°)', range: [180, 0], dtick: 45 },
      yaxis: { title: 'µm from the ' + noun() + ' centre', rangemode: 'tozero' },
      shapes: [{ type: 'line', xref: 'x', yref: 'paper', x0: 90, x1: 90, y0: 0, y1: 1,
                 line: { color: CS.ink3, width: 1, dash: 'dot' } }]
    }, { responsive: true, displayModeBar: false });
    explainButton(el, polaritySpec(name, ref, 'where'));
  });

  // ── one circular map per object ────────────────────────────────────────────
  setHtml('pol-map-label', 'Where does each structure sit in the ' + noun() + '?');
  var cellMapDesc = ('One panel per ' + noun() + ', showing the whole layout. The angle around the circle is'
    + ' the angle to the axis towards ' + dataName(ref) + ', with 0° at the right. The radius is '
    + (toMask
        ? 'the depth inside the ' + dataName(mask) + ', with the ' + dataName(mask) + ' at the rim and the'
          + ' deepest instances at the centre'
        : 'the distance from the centre of the ' + noun())
    + '. A diamond marks the centre of each structure.'
    + (toMask && !masksHaveDepth
        ? ' Structures measured as one whole mask are left out, because their depth was not measured.'
        : '')
    + (toMask && masksHaveDepth
        ? ' A whole-structure mask is drawn at the depth of its own centre.'
        : '')
    + ' Only the angle to the axis is fixed by the data. The rotation about the axis is arbitrary and'
    + ' differs from panel to panel, so each panel is read on its own.');
  // Named by colour: every other row titles its panels by the structure, and these are
  // titled by the object, so without this the diamonds are unlabelled. Only what this view
  // can draw - reading it to the boundary leaves the masks out, and a legend naming them
  // would have a reader hunting for a colour that is not there.
  setHtml('pol-map-legend', structures.filter(function(name) {
    return relative[name].some(function(r){ return radiusOf(r) != null; });
  }).map(function(name) {
    return '<span class="d-inline-flex align-items-center gap-1">'
      + '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
      + 'background:' + entColor(name) + '"></span>' + dataName(name) + '</span>';
  }).concat(['<span class="d-inline-flex align-items-center gap-1">'
      + '<span style="color:' + entColor(ref) + '">★</span>' + dataName(ref)
      + ', the reference, on 0°</span>',
    '<span class="d-inline-flex align-items-center gap-1">◆ a structure’s centre in one '
      + noun() + '</span>'
  ]).join(''));
  var mapHost = document.getElementById('pol-map-charts');
  var reach = 0;
  structures.forEach(function(name) {
    relative[name].forEach(function(r) {
      var radius = radiusOf(r);
      if (radius != null && radius > reach) reach = radius;
    });
  });
  // Including the reference's own marker: it is often the most peripheral structure there
  // is - that is what made it the default axis - and left out of this it was drawn off the
  // edge of the plot.
  scopedObjectNames().forEach(function(id) {
    var axis = polarOffset(id, ref);
    var radius = toMask ? refDepth[id] : (axis && axis.dist);
    if (radius != null && isFinite(radius) && radius > reach) reach = radius;
  });
  var drawCellMaps = function(mapHost) {
  mapObjects.forEach(function(id) {
    var axis = polarOffset(id, ref);
    if (!axis) return;
    var frame = polarFrame(axis.unit);
    var traces = [], sampled = [];
    structures.forEach(function(name) {
      var rows = relative[name].filter(function(r) {
        return r.object_id === id && radiusOf(r) != null;
      });
      if (!rows.length) return;
      // A stride, not a random draw: the same panel twice is the same panel.
      var step = Math.max(1, Math.ceil(rows.length / POLAR_DOT_BUDGET));
      var shown = rows.filter(function(_, i){ return i % step === 0; });
      if (step > 1) sampled.push(dataName(name) + ' 1 in ' + step);
      traces.push({ type: 'scatterpolar', mode: 'markers', name: name,
        r: shown.map(radiusOf),
        theta: shown.map(function(r){ return polarAngleDeg(r.unit, frame); }),
        marker: { color: entColor(name), size: rows.length > 1 ? 4 : 9,
                  opacity: rows.length > 1 ? 0.5 : 0.9, line: { width: 0 } },
        hovertemplate: (toMask ? '%{r:.2f} µm inside the ' + mask : '%{r:.2f} µm out')
          + ' at %{theta:.0f}°<extra>' + name + '</extra>' });
      var sizes = polarWeights(rows);
      var found = polarityIndices(rows.map(function(r){ return r.unit; }), axis.unit, sizes);
      if (!found || !found.mean) return;
      traces.push({ type: 'scatterpolar', mode: 'markers', showlegend: false,
        r: [weightedQuantile(rows.map(radiusOf), sizes, 0.5)],
        theta: [centreAngle(rows.map(function(r){ return r.unit; }), frame, sizes, found.mean)],
        marker: { symbol: 'diamond', size: 11, color: entColor(name),
                  line: { color: '#fff', width: 1.5 } },
        hovertemplate: 'centre of ' + name + ': %{r:.2f} µm '
          + (toMask ? 'inside the ' + mask : 'out') + ' at %{theta:.0f}°'
          + '<br>R ' + found.R.toFixed(2) + ', V ' + found.V.toFixed(2) + '<extra></extra>' });
    });
    // The reference on its own axis: 0° by construction, and its distance is the thing
    // that differs between objects - how far out the structure everything is measured
    // against actually sits in this one.
    var refRadius = toMask ? refDepth[id] : axis.dist;
    if (refRadius != null && isFinite(refRadius)) {
      traces.push({ type: 'scatterpolar', mode: 'markers', showlegend: false,
        r: [refRadius], theta: [0],
        marker: { symbol: 'star', size: 13, color: entColor(ref),
                  line: { color: '#fff', width: 1 } },
        hovertemplate: ref + ', the reference: %{r:.2f} µm '
          + (toMask ? 'inside the ' + mask : 'from the centre') + '<extra></extra>' });
    }
    if (!traces.length) return;
    var el = document.createElement('div');
    el.className = 'chart-cell';
    el.style.width = '330px';
    var group = groupOfObject[String(id)];
    if (group) {
      var heading = document.createElement('div');
      heading.style.cssText = 'font-size:.72rem;font-weight:600;color:' + CS.ink
        + ';margin-bottom:2px;display:flex;align-items:center;gap:5px';
      heading.innerHTML = '<span style="display:inline-block;width:9px;height:9px;'
        + 'border-radius:50%;background:' + gColor(group) + '"></span>' + escHtml(group);
      el.appendChild(heading);
    }
    var plotEl = document.createElement('div');
    el.appendChild(plotEl);
    mapHost.appendChild(el);
    drawPlot(plotEl, traces, {
      margin: { l: 52, r: 52, t: 34, b: 26 }, height: 320,
      title: { text: String(id) }, showlegend: false,
      polar: {
        // Ticks up the 45° spoke and only a few of them: along 0° they ran into the dots
        // and into each other, and the radius is the one axis here that is plain µm.
        // Reversed for a depth, so the boundary stays at the rim where it belongs and
        // deeper is further in - the picture the right way round, from the number as it
        // was measured.
        radialaxis: { range: toMask ? [reach * 1.02, 0] : [0, reach * 1.02],
                      angle: 45, nticks: 4,
                      ticksuffix: ' µm', tickfont: { size: 9 } },
        // Only the two directions that mean anything are named. The perpendicular is the
        // arbitrary part of the frame, so a tick saying 90° there would read as a
        // measurement of something.
        angularaxis: { direction: 'counterclockwise', rotation: 0,
                       tickmode: 'array', tickvals: [0, 180],
                       ticktext: [ref, 'away'], tickfont: { size: 9 } }
      }
    }, { responsive: true, displayModeBar: false });
    // The map holds every structure; the viewer draws one at a time, so the first with
    // instances of its own, and the viewer lets the reader change it.
    var mapped = structures.filter(function(n){ return kinds[n] === 'label'; })[0]
      || structures[0];
    explainButton(el, polaritySpec(mapped, ref, 'cellmap', id, toMask ? 'membrane' : 'centre'));
    if (sampled.length) {
      var note = document.createElement('div');
      note.className = 'chart-note';
      note.innerHTML = 'Drawn from a sample: ' + sampled.join(', ') + '. The diamonds are'
        + ' from all of them.';
      el.appendChild(note);
    }
  });
  };

  // ── one point per cell, one plot per group ─────────────────────────────────
  //
  // What the full circles above are made of, kept to what a reader compares: each cell's
  // average for a structure - how far out it sits (the median) and which way it points (the
  // mean direction) - as one diamond, so a group's cells sit in one plot and a group of seven
  // is seven points and not seven circles. Only the angle *to the axis* is drawn, 0° to 180°,
  // never the rotation about it: nothing in the data fixes that rotation, so full circles
  // cannot be laid over one another, and half ones can.
  var cellPoint = function(id, name) {
    var axis = polarOffset(id, ref);
    if (!axis) return null;
    var rows = relative[name].filter(function(r) {
      return r.object_id === id && radiusOf(r) != null;
    });
    if (!rows.length) return null;
    var sizes = polarWeights(rows);
    var found = polarityIndices(rows.map(function(r){ return r.unit; }), axis.unit, sizes);
    if (!found || !found.mean) return null;
    // The middle of the instances as the plot would draw them, each at its angle to the axis and its radius.
    var centre = polarHalfCentre(rows.map(function(r){ return r.angle_deg; }), rows.map(radiusOf), sizes);
    return { id: id, r: centre.radius, cone: centre.angle * 180 / Math.PI,
             R: found.R, V: found.V, n: rows.length };
  };
  setHtml('pol-map-desc', 'One diamond per ' + noun() + ' and structure, one plot per group. The radius is '
    + (toMask ? 'the depth inside the ' + dataName(mask) : 'the distance from the centre of the ' + noun())
    + ' and the angle to the axis towards ' + dataName(ref) + ' (<b>0° towards</b>, <b>180° away</b>) at which the structure lies: the'
    + ' average position of its instances, each placed at its own radius and angle. A star marks '
    + dataName(ref) + '. Only the angle to the axis is fixed by the data; the side of the axis on which a'
    + ' structure falls is arbitrary, so the ' + noun(true) + ' of a group can share a plot.');

  var mapGroups = [];
  mapObjects.forEach(function(id) {
    var g = groupOfObject[String(id)] || '';
    var entry = mapGroups.find(function(e){ return e.group === g; });
    if (!entry) mapGroups.push(entry = { group: g, ids: [] });
    entry.ids.push(id);
  });
  var mappedStructure = structures.filter(function(n){ return kinds[n] === 'label'; })[0]
    || structures[0];
  mapGroups.forEach(function(entry) {
    var traces = [];
    var byCell = {};
    structures.forEach(function(name) {
      var points = entry.ids.map(function(id){ return cellPoint(id, name); })
                            .filter(Boolean);
      points.forEach(function(q) { (byCell[q.id] = byCell[q.id] || []).push({ r: q.r, theta: q.cone }); });
      if (!points.length) return;
      traces.push({ type: 'scatterpolar', mode: 'markers', name: name, showlegend: false,
        r: points.map(function(q){ return q.r; }),
        theta: points.map(function(q){ return q.cone; }),
        text: points.map(function(q){ return String(q.id); }),
        marker: { symbol: 'diamond', size: 11, color: entColor(name),
                  line: { color: '#fff', width: 1.5 } },
        hovertemplate: '%{text}: ' + escName(name) + ' %{r:.2f} µm '
          + (toMask ? 'inside the ' + mask : 'out') + ' at %{theta:.0f}°<extra></extra>' });
    });
    var stars = entry.ids.map(function(id) {
      var axis = polarOffset(id, ref);
      var radius = toMask ? refDepth[id] : (axis && axis.dist);
      return radius != null && isFinite(radius) ? { id: id, r: radius } : null;
    }).filter(Boolean);
    if (stars.length) {
      traces.push({ type: 'scatterpolar', mode: 'markers', showlegend: false,
        r: stars.map(function(q){ return q.r; }), theta: stars.map(function(){ return 0; }),
        text: stars.map(function(q){ return String(q.id); }),
        marker: { symbol: 'star', size: 12, color: entColor(ref),
                  line: { color: '#fff', width: 1 } },
        hovertemplate: '%{text}: ' + escName(ref) + ', the reference, %{r:.2f} µm<extra></extra>' });
    }
    if (!traces.length) return;
    var el = document.createElement('div');
    el.className = 'chart-cell';
    el.style.width = '380px';
    if (entry.group) {
      var heading = document.createElement('div');
      heading.style.cssText = 'font-size:.72rem;font-weight:600;color:' + CS.ink
        + ';margin-bottom:2px;display:flex;align-items:center;gap:5px';
      heading.innerHTML = '<span style="display:inline-block;width:9px;height:9px;'
        + 'border-radius:50%;background:' + groupColor(entry.group) + '"></span>'
        + escHtml(entry.group) + ' · ' + entry.ids.length + ' ' + noun(entry.ids.length !== 1);
      el.appendChild(heading);
    }
    var plotEl = document.createElement('div');
    el.appendChild(plotEl);
    mapHost.appendChild(el);
    drawPlot(plotEl, traces, {
      margin: { l: 52, r: 52, t: 8, b: 26 }, height: 240,
      showlegend: false,
      polar: {
        sector: [0, 180],
        radialaxis: { range: toMask ? [reach * 1.02, 0] : [0, reach * 1.02], angle: 90,
                      nticks: 4, ticksuffix: ' µm', tickfont: { size: 9 } },
        angularaxis: { direction: 'counterclockwise', rotation: 0, tickmode: 'array',
                       tickvals: [0, 90, 180], ticktext: [ref, '90°', 'away'],
                       tickfont: { size: 9 } }
      }
    }, { responsive: true, displayModeBar: false });
    // A group's plot opens one of the group's own cells.
    plotEl.dataset.map = '1';
    plotEl._cells = byCell;
    explainButton(el, polaritySpec(mappedStructure, ref, 'map', entry.ids[0],
                                   toMask ? 'membrane' : 'centre'));
  });

  // The full circles, one per cell: what the points above were taken from, for whoever wants
  // to look at one cell's whole layout.
  perObjectFold(document.getElementById('pol-map-fold'), 'Show each ' + noun() + '’s own map: every instance, round the full circle',
    function(body) {
      var words = document.createElement('p');
      words.className = 'subsection-desc';
      words.innerHTML = cellMapDesc;
      body.appendChild(words);
      var holder = document.createElement('div');
      holder.className = 'charts-row';
      body.appendChild(holder);
      drawCellMaps(holder);
    });
}
