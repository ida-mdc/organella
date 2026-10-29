// Every polarity column in a report is measured from one origin - the object mask's own
// centroid - but it points wherever the volume happened to be oriented, so an azimuth of
// 40° means nothing from one object to the next. What does carry across is an angle
// measured against something *inside* the object: pick a structure, take the direction from
// the centre to its centre, call that 0°, and "towards it" and "away from it" then mean the
// same thing in every object and can be pooled.
//
// That is a rotation of what was already measured, so nothing has to be measured again -
// but only what has a direction can be rotated. An instance has its centroid; a
// whole-structure mask has one centre and nothing more, so a mask is one point here
// and not a shape. Giving a mask an angular extent would mean measuring it per voxel, which
// is a re-run.
//
// R and V are Polarity-JaM's two collective measures (Giese et al., Nat Commun 2025,
// polarityjam.com), which this is deliberately comparable with: R is the length of the mean
// unit vector - 0 when directions cancel, 1 when they all agree - and V = R·cos(mean
// direction − reference) is the same strength signed by whether it leans towards the
// reference or away, so -1 is straight away from it and +1 straight at it. Their point about
// the statistics is worth keeping too: instances inside one object are not independent of
// each other, so the comparable unit is one R and one V *per object*, which is what the
// panels plot.

// A drawing budget for the circular plots, not a rule about the data: 14,000 dots in one
// polar panel is a disc, and eight of them is a minute of scripting time.
var POLAR_DOT_BUDGET = 300;

function polDot(a, b) {
  var sum = 0;
  for (var i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

function polNorm(a) { return Math.sqrt(polDot(a, a)); }

function polScaled(a, k) { return a.map(function(v){ return v * k; }); }

function polAdded(a, b) { return a.map(function(v, i){ return v + b[i]; }); }

/**
 * How much of a structure an instance is: its volume, or its area in a plane. The weight an
 * average over instances gives it, so that three labels of one nucleus - one real, two a few
 * voxels - average to the nucleus and not to a third of it each.
 */
function polarWeight(row) {
  var size = row.volume_um3 != null ? row.volume_um3 : row.area_um2;
  return size != null && isFinite(size) && size > 0 ? Number(size) : null;
}

/**
 * The weights for one population of rows (or entries carrying a `weight`), or all equal where
 * any of them has no size. Half of them sized and half not would give the sized ones all of
 * the say, which is a different average, not a more careful one.
 */
function polarWeights(rows) {
  var sizes = rows.map(function(r) {
    return r.weight !== undefined ? r.weight : polarWeight(r);
  });
  return sizes.every(function(w){ return w !== null && w !== undefined; })
    ? sizes : rows.map(function(){ return 1; });
}

/** The value a fraction `q` of the total weight lies at or below: a median that counts by size. */
function weightedQuantile(values, weights, q) {
  if (!values.length) return null;
  var order = values.map(function(_, i){ return i; })
    .sort(function(a, b){ return values[a] - values[b]; });
  var total = weights.reduce(function(n, w){ return n + w; }, 0), seen = 0;
  for (var k = 0; k < order.length; k++) {
    seen += weights[order[k]];
    if (seen >= q * total - 1e-12) return values[order[k]];
  }
  return values[order[order.length - 1]];
}

/** A row's direction from the object centre, as a unit vector in array axis order. */
function polarUnit(row) {
  var keys = (row.spatial_dims === 2 || (row.spatial_dims == null && PLANAR))
    ? ['polar_ny', 'polar_nx'] : ['polar_nz', 'polar_ny', 'polar_nx'];
  var out = [];
  for (var i = 0; i < keys.length; i++) {
    var value = row[keys[i]];
    if (value == null || !isFinite(value)) return null;
    out.push(Number(value));
  }
  return out;
}

/**
 * Where a structure's centre sits, seen from the object centre: direction and how far.
 *
 * A label structure answers with the mean of its instances' offsets - its centre of
 * instances - and a mask with the one centre it has. Null where the structure sits *on* the
 * centre, which is no direction at all: the object mask itself always does, and a structure
 * spread evenly around the object can too.
 */
function polarOffset(objectId, structure) {
  var key = objectId + '|' + structure;
  if (key in _POLAR_OFFSETS) return _POLAR_OFFSETS[key];
  var rows = [], offsets = [];
  INSTANCES.forEach(function(r) {
    if (r.object_id !== objectId || r.entity_name !== structure) return;
    var unit = polarUnit(r);
    if (!unit || r.polar_dist_um == null || !isFinite(r.polar_dist_um)) return;
    rows.push(r);
    offsets.push(polScaled(unit, r.polar_dist_um));
  });
  var n = rows.length;
  var found = null;
  if (n) {
    // Centred on the instances by size: a stray fragment of a few voxels beside the real
    // thing is a fragment, and moves the centre of the structure by what it weighs.
    var weights = polarWeights(rows), total = 0, sum = null;
    offsets.forEach(function(offset, i) {
      var scaled = polScaled(offset, weights[i]);
      sum = sum ? polAdded(sum, scaled) : scaled;
      total += weights[i];
    });
    var mean = polScaled(sum, 1 / total), length = polNorm(mean);
    if (length > 0) found = { unit: polScaled(mean, 1 / length), dist: length,
                              of: 'instances', n: n };
  } else {
    ENTS.forEach(function(r) {
      if (found || r.object_id !== objectId || r.entity_name !== structure) return;
      var unit = polarUnit(r);
      if (unit && r.polar_dist_um != null && isFinite(r.polar_dist_um) && r.polar_dist_um > 0) {
        found = { unit: unit, dist: r.polar_dist_um, of: 'centre', n: 1 };
      }
    });
  }
  _POLAR_OFFSETS[key] = found;
  return found;
}

/**
 * The structures that can serve as the reference: the ones every object has first, and
 * within those, the most off-centre.
 *
 * Off-centre because that is what makes an axis: a structure whose centre sits almost on
 * the object's centre points nowhere in particular, and every angle measured against it is
 * noise. But coverage comes first, because a reference only some objects have silently
 * takes the rest of the batch out of the section - on a batch of seven cells the most
 * off-centre structure of all sat in one of them, and choosing it by distance alone answered
 * the question for one cell and called it the answer.
 *
 * Ordered, and never filtered: both numbers stand beside every choice, so a one-object
 * reference is available and is visible as one.
 */
function polarReferences() {
  var objects = scopedObjectNames();
  var out = [];
  entityNames().forEach(function(name) {
    if (OBJECTS.some(function(o){ return o.object_mask_name === name; })) return;
    var distances = [];
    objects.forEach(function(id) {
      var offset = polarOffset(id, name);
      if (offset) distances.push(offset.dist);
    });
    if (!distances.length) return;
    distances.sort(function(a, b){ return a - b; });
    out.push({ name: name, dist: quantileSorted(distances, 0.5),
               objects: distances.length, of: objects.length });
  });
  return out.sort(function(a, b) {
    return (b.objects - a.objects) || (b.dist - a.dist);
  });
}

/**
 * The plane a circular plot is drawn in: the reference axis, and one direction across it.
 *
 * In a plane there is nothing to choose - the axis and its perpendicular are the whole of
 * it - and the angles are the real thing. In a volume the rotation about the axis is *not*
 * determined by anything in the data: this picks the axis of the volume that lies furthest
 * from the reference, which is reproducible but arbitrary, and why the circular plots are
 * one per object, never pooled.
 */
function polarFrame(axis) {
  if (axis.length === 2) return { a: axis, u: [axis[1], -axis[0]] };
  var best = null;
  [[1, 0, 0], [0, 1, 0], [0, 0, 1]].forEach(function(candidate) {
    var across = polAdded(candidate, polScaled(axis, -polDot(candidate, axis)));
    var length = polNorm(across);
    if (!best || length > best.length) best = { length: length, u: polScaled(across, 1 / length) };
  });
  return { a: axis, u: best.u };
}

/**
 * The mask that bounds every object, where they all agree on one.
 *
 * The radius of a circular map can be read to it instead of from the centre, which is the
 * comparable way round: cells differ in size, so "0.4 µm inside the membrane" means the
 * same thing in all of them where "5 µm from the centre" does not.
 */
function boundingMaskName() {
  var names = uniq(OBJECTS.map(function(o){ return o.object_mask_name; }).filter(Boolean));
  return names.length === 1 ? names[0] : null;
}

/** Signed angle in degrees from the reference axis, in the plane of that frame. */
function polarAngleDeg(unit, frame) {
  return Math.atan2(polDot(unit, frame.u), polDot(unit, frame.a)) * 180 / Math.PI;
}

/**
 * Where the dots of a circle plot are centred, in degrees: the weighted circular mean of the
 * angles they are drawn at.
 *
 * The plot shows each direction by the angle of its projection into the plane, and the
 * middle of those is what a diamond should mark. Averaging the 3D directions first and
 * projecting after is a different number: a direction that points mostly out of the plane
 * projects to something short, so it barely moves the 3D mean and yet is drawn as far round
 * the circle as any other. Null when the angles cancel and there is no middle.
 */
function polarCentreAngleDeg(units, frame, weights) {
  var x = 0, y = 0;
  units.forEach(function(unit, i) {
    var angle = Math.atan2(polDot(unit, frame.u), polDot(unit, frame.a));
    var w = weights && weights.length === units.length ? weights[i] : 1;
    x += w * Math.cos(angle);
    y += w * Math.sin(angle);
  });
  return Math.hypot(x, y) > 1e-9 ? Math.atan2(y, x) * 180 / Math.PI : null;
}

/** The circle plot's diamond angle, falling back on the 3D mean where the angles cancel. */
function centreAngle(units, frame, weights, mean) {
  var angle = polarCentreAngleDeg(units, frame, weights);
  return angle === null ? polarAngleDeg(mean, frame) : angle;
}

/** Angle in degrees between a direction and the reference axis: 0 towards, 180 away. */
function polarConeDeg(unit, axis) {
  return Math.acos(Math.max(-1, Math.min(1, polDot(unit, axis)))) * 180 / Math.PI;
}

/**
 * Polarity-JaM's R and V for one population of directions against one axis.
 *
 * V is the mean of cos(angle to the axis), which is the same number as R·cos(mean
 * direction − axis) and needs no mean direction to state. `weights` - one per direction, the
 * size of the instance it is of - makes both means weighted ones.
 */
function polarityIndices(units, axis, weights) {
  if (!units.length) return null;
  // Each direction counts by the weight it is given - its instance's size, where the caller
  // has it - and by one where it does not.
  var w = weights && weights.length === units.length ? weights : units.map(function(){ return 1; });
  var sum = null, cosines = 0, total = 0;
  units.forEach(function(unit, i) {
    var scaled = polScaled(unit, w[i]);
    sum = sum ? polAdded(sum, scaled) : scaled;
    cosines += w[i] * polDot(unit, axis);
    total += w[i];
  });
  var mean = polScaled(sum, 1 / total), R = polNorm(mean);
  return { n: units.length, R: R, V: cosines / total,
           mean: R > 0 ? polScaled(mean, 1 / R) : null };
}

// ── polarity, on the meshes ─────────────────────────────────────────────────

/** The mean of a mesh's vertices: where it sits, for working out where the centre is. */
function meshCentroid(geometry) {
  var p = geometry.positions, n = p.length / 3;
  if (!n) return null;
  var sum = [0, 0, 0];
  for (var i = 0; i < p.length; i += 3) { sum[0] += p[i]; sum[1] += p[i + 1]; sum[2] += p[i + 2]; }
  return [sum[0] / n, sum[1] / n, sum[2] / n];
}

function medianOf(values) {
  if (!values.length) return null;
  var sorted = values.slice().sort(function(a, b){ return a - b; });
  return quantileSorted(sorted, 0.5);
}

/**
 * How deep inside the bounding mask a reading sits, where there is one: its distance row to
 * that mask for an instance, and the depth of its centre for a whole-structure mask, which
 * has no instance to carry a distance. The charts and the viewer ask the same question, so
 * it is asked here, once.
 */
function polarDepth(row, maskName) {
  var col = maskName ? distColumnsFor(maskName).min : null;
  var fromDistance = col == null ? null : row[col];
  if (fromDistance != null && isFinite(fromDistance)) return Number(fromDistance);
  var ofCentre = row.polar_depth_um;
  return ofCentre != null && isFinite(ofCentre) ? Number(ofCentre) : null;
}

function polCross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/**
 * Where the object's centre is among the meshes, which way the reference lies from it, and
 * the direction of every instance of the structure being read.
 *
 * The directions and distances are the report's own rows - the very numbers the charts are
 * drawn from, so what the viewer counts is what a chart counts - turned from array order
 * into the x, y, z the meshes are in. What a geometry file does not carry is the centre they
 * are measured from, and the viewer needs one to draw from. Every instance says where it is
 * (its mesh) and where it is from the centre (direction x distance), so each one implies a
 * centre, and the median of those implied centres is where the arrows start. A mesh's mean
 * vertex is not the measured centroid, which is why it is a median and not any one of them.
 *
 * The axis is the reference's offset from the centre, the same thing the charts' axis is,
 * and `u` is the direction across it that the circular charts are drawn in, so a chart's
 * angle is an angle in the plane of `axis` and `u`. `w` completes the three. Per instance:
 * `angle` to the axis (what the rose and the half-circle plots use, however it is turned
 * about the axis), `phi` how far round the axis it is, and `theta` its signed angle once
 * projected onto that plane (what the full circles use).
 *
 * Null when there is nothing to measure towards.
 */
function polarityFrame(items, objectId, ref, structure, radiusMode, extra) {
  var axisAt = polarOffset(objectId, ref);
  if (!axisAt) return null;
  var toXyz = function(u){ return u.length === 3 ? [u[2], u[1], u[0]] : [u[1], u[0], 0]; };
  var axis = toXyz(axisAt.unit);
  var across = toXyz(polarFrame(axisAt.unit).u);
  var third = polCross(axis, across);
  var mask = boundingMaskName();
  var byKey = new Map();
  items.forEach(function(item) {
    byKey.set(item.entity + '|' + (item.label === null ? '' : item.label), item);
  });

  // A structure with instances reads as them; one measured whole has the one row.
  var readings = function(name) {
    var rows = INSTANCES.filter(function(r) {
      return String(r.object_id) === String(objectId) && r.entity_name === name;
    });
    var whole = !rows.length;
    if (whole) {
      rows = ENTS.filter(function(r) {
        return String(r.object_id) === String(objectId) && r.entity_name === name;
      });
    }
    var out = [];
    rows.forEach(function(r) {
      var unit = polarUnit(r), dist = r.polar_dist_um;
      if (!unit || !(dist > 0) || !isFinite(dist)) return;
      var key = name + '|' + (r.label_id === undefined || r.label_id === null ? '' : r.label_id);
      out.push({ n: toXyz(unit), dist: Number(dist), depth: polarDepth(r, mask),
                 weight: polarWeight(r), item: byKey.get(key) || null });
    });
    out.whole = whole;
    return out;
  };
  var names = [structure].concat(extra || []);
  var read = names.map(readings);
  var votes = [[], [], []];
  read.concat([readings(ref)]).forEach(function(list) {
    list.forEach(function(u) {
      var at = u.item && meshCentroid(u.item.geometry);
      if (at) for (var k = 0; k < 3; k++) votes[k].push(at[k] - u.n[k] * u.dist);
    });
  });
  var centre;
  if (votes[0].length) {
    centre = votes.map(medianOf);
  } else {
    // Nothing placed on a mesh: the middle of what is drawn is the next best thing.
    var sum = [0, 0, 0], n = 0;
    items.forEach(function(item) {
      var at = meshCentroid(item.geometry);
      if (at) { for (var k = 0; k < 3; k++) sum[k] += at[k]; n++; }
    });
    if (!n) return null;
    centre = sum.map(function(v){ return v / n; });
  }
  var reach = axisAt.dist, depthReach = null;
  var describe = function(units) {
    var sizes = polarWeights(units);
    units.forEach(function(u, i) {
      u.weight = sizes[i];
      u.angle = polarConeDeg(u.n, axis);
      u.phi = Math.atan2(polDot(u.n, third), polDot(u.n, across));
      u.theta = Math.atan2(polDot(u.n, across), polDot(u.n, axis));
      if (u.dist > reach) reach = u.dist;
      if (u.depth != null && (depthReach === null || u.depth > depthReach)) depthReach = u.depth;
    });
    var found = polarityIndices(units.map(function(u){ return u.n; }), axis, sizes);
    var diamond = null;
    if (found && found.mean) {
      var withDepth = units.filter(function(u){ return u.depth !== null; });
      // The circle plot puts the diamond in the middle of the angles its dots are drawn at.
      var cx = 0, cy = 0;
      units.forEach(function(u, i) { cx += sizes[i] * Math.cos(u.theta); cy += sizes[i] * Math.sin(u.theta); });
      diamond = { n: found.mean,
                  theta: Math.hypot(cx, cy) > 1e-9 ? Math.atan2(cy, cx) : null,
                  dist: weightedQuantile(units.map(function(u){ return u.dist; }), sizes, 0.5),
                  depth: withDepth.length
                    ? weightedQuantile(withDepth.map(function(u){ return u.depth; }),
                                       withDepth.map(function(u){ return u.weight; }), 0.5)
                    : null };
    }
    return { units: units, diamond: diamond, found: found };
  };
  var groups = read.map(function(units, i) {
    var d = describe(units);
    d.name = names[i];
    d.whole = !!units.whole;
    return d;
  });
  // Where the reference itself sits, for the star: its distance, and how deep inside the mask.
  var refUnits = readings(ref);
  var refDepth = null;
  refUnits.forEach(function(u) {
    if (u.depth != null && (refDepth === null || u.depth > refDepth)) refDepth = u.depth;
  });
  if (refDepth !== null && (depthReach === null || refDepth > depthReach)) depthReach = refDepth;
  var first = groups[0];
  return { centre: centre, axis: axis, u: across, w: third, refDist: axisAt.dist, refDepth: refDepth,
           reach: reach, depthReach: depthReach, units: first.units, diamond: first.diamond,
           groups: groups,
           // How far out the chart's own radius goes: what its grid is drawn to.
           plotReach: radiusMode === 'membrane' && depthReach ? depthReach : reach,
           R: first.found ? first.found.R : null, V: first.found ? first.found.V : null,
           mean: first.found && first.found.mean ? first.found.mean : null };
}

/**
 * The structures a map of the whole cell shows, in the chart's order: the ones with instances
 * first, then the ones measured whole. Everything with a direction from the centre, except the
 * reference (which is the axis) and the mask that bounds the cell.
 */
function polarSubjects(ref) {
  var bounding = boundingMaskName();
  var labels = uniq(INSTANCES.filter(function(r){ return polarUnit(r); })
                             .map(function(r){ return r.entity_name; }))
    .filter(function(n){ return n !== ref && n !== bounding; });
  var masks = uniq(ENTS.filter(function(r){ return r.entity_kind === 'mask' && polarUnit(r); })
                       .map(function(r){ return r.entity_name; }))
    .filter(function(n){ return n !== ref && n !== bounding && labels.indexOf(n) < 0; });
  return labels.concat(masks);
}

/**
 * One cell's point for one structure, as the group plot draws it: how far out (the size-
 * weighted median of the radius) and the angle to the axis of the mean direction. Read from the
 * report's rows, so a cell the viewer is not showing can still be placed. Null where the cell
 * has nothing of the structure, or no axis to measure from.
 */
function polarCellPoint(objectId, ref, name, radiusMode) {
  var axis = polarOffset(objectId, ref);
  if (!axis) return null;
  var mask = boundingMaskName();
  var rows = INSTANCES.filter(function(r){ return String(r.object_id) === String(objectId) && r.entity_name === name; });
  if (!rows.length) rows = ENTS.filter(function(r){ return String(r.object_id) === String(objectId) && r.entity_name === name; });
  rows = rows.filter(function(r){ return polarUnit(r) && r.polar_dist_um > 0; });
  var toMask = radiusMode === 'membrane';
  rows = rows.filter(function(r){ return !toMask || polarDepth(r, mask) != null; });
  if (!rows.length) return null;
  var sizes = polarWeights(rows);
  var found = polarityIndices(rows.map(polarUnit), axis.unit, sizes);
  if (!found || !found.mean) return null;
  return { id: objectId, name: name, cone: polarConeDeg(found.mean, axis.unit),
           r: weightedQuantile(rows.map(function(r){ return toMask ? polarDepth(r, mask) : r.polar_dist_um; }), sizes, 0.5) };
}

/** Which way a chart lays the instances out, by what the chart is. */
var POLAR_LAYOUTS = { map: 'half', cellmap: 'circle', where: 'cart', index: 'unit' };

/** The ones that lay the cell flat into a chart, and so turn it to face the camera. */
var POLAR_FLATTENS = { map: true, cellmap: true, where: true };

/** The radius the chart plots for one reading: its distance, or its depth read to the boundary. */
function polarPlotRadius(frame, unit, radiusMode) {
  if (radiusMode === 'membrane' && unit.depth != null && frame.depthReach != null) {
    // The boundary at the rim and the deepest in the middle, the way the chart draws it.
    return frame.depthReach - unit.depth;
  }
  return unit.dist;
}

/**
 * Where one direction and distance is, a fraction `t` of the way from where it sits in the
 * cell (t = 0) to where a chart plots it (t = 1).
 *
 * The three layouts are the three charts. `half` is the plot of angle to the axis against
 * distance: the instance is turned about the axis into the half-plane of `axis` and `u`,
 * which keeps its distance and its angle to the axis and loses only the rotation about it -
 * which is the part the plot leaves out. `circle` is the full circle: the instance's angle
 * once projected onto that plane, at its distance. `cart` is the scatter of angle against
 * distance, angle across and distance up. The last two go there in a straight line.
 * `centreTheta`, where given, is the angle a diamond takes in `circle` in place of its
 * direction's own: the middle of its dots.
 * Writes x, y, z into `out` at `at`.
 */
function polarMorphPoint(frame, layout, n, dist, radius, t, out, at, centreTheta) {
  var c = frame.centre, a = frame.axis, u = frame.u, w = frame.w;
  var na = polDot(n, a), nu = polDot(n, u), nw = polDot(n, w);
  var k;
  if (layout === 'half') {
    var r = dist + (radius - dist) * t;
    var cone = Math.acos(Math.max(-1, Math.min(1, na)));
    var phi = Math.atan2(nw, nu) * (1 - t);
    var side = Math.sin(cone);
    for (k = 0; k < 3; k++) {
      out[at + k] = c[k] + r * (na * a[k] + side * (Math.cos(phi) * u[k] + Math.sin(phi) * w[k]));
    }
    return;
  }
  if (layout === 'unit') {
    // Out along its own direction to one common distance: what is left once distance is
    // set aside, which is all R and V look at.
    var common = frame.reach * 1.1, reachNow = dist + (common - dist) * t;
    for (k = 0; k < 3; k++) out[at + k] = c[k] + n[k] * reachNow;
    return;
  }
  var along, up;
  if (layout === 'cart') {
    var coneAngle = Math.acos(Math.max(-1, Math.min(1, na)));
    var span = frame.plotReach;
    along = span - (coneAngle / Math.PI) * 2 * span;
    up = radius;
  } else {
    var theta = centreTheta != null ? centreTheta : Math.atan2(nu, na);
    along = radius * Math.cos(theta);
    up = radius * Math.sin(theta);
  }
  for (k = 0; k < 3; k++) {
    var here = c[k] + dist * n[k];
    var there = c[k] + along * a[k] + up * u[k];
    out[at + k] = here + (there - here) * t;
  }
}

/** Every instance of the frame at `t`, as a flat x, y, z array. */
function polarMorphPositions(frame, layout, radiusMode, t) {
  var out = new Float32Array(frame.units.length * 3);
  frame.units.forEach(function(unit, i) {
    polarMorphPoint(frame, layout, unit.n, unit.dist,
                    polarPlotRadius(frame, unit, radiusMode), t, out, 3 * i);
  });
  return out;
}

/**
 * The turn that lays the plot's plane flat to the camera: axis to the right, `u` up, `w`
 * towards the viewer - which is how every circular chart is drawn - a fraction `t` of the
 * way, about the object's centre.
 */
function polarMorphPose(THREE, frame, t) {
  var a = frame.axis, u = frame.u, w = frame.w, c = frame.centre;
  var turn = new THREE.Matrix4().set(a[0], a[1], a[2], 0, u[0], u[1], u[2], 0,
                                     w[0], w[1], w[2], 0, 0, 0, 0, 1);
  var full = new THREE.Quaternion().setFromRotationMatrix(turn);
  var part = new THREE.Quaternion().slerpQuaternions(new THREE.Quaternion(), full, t);
  return new THREE.Matrix4().makeTranslation(c[0], c[1], c[2])
    .multiply(new THREE.Matrix4().makeRotationFromQuaternion(part))
    .multiply(new THREE.Matrix4().makeTranslation(-c[0], -c[1], -c[2]));
}

/** Two unit vectors across an axis, so a cone about it can be walked round. */
function acrossAxis(axis) {
  var seed = Math.abs(axis[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  var u = polAdded(seed, polScaled(axis, -polDot(seed, axis)));
  u = polScaled(u, 1 / polNorm(u));
  var v = [axis[1] * u[2] - axis[2] * u[1], axis[2] * u[0] - axis[0] * u[2],
           axis[0] * u[1] - axis[1] * u[0]];
  return [u, v];
}

/** The angular bins of the rose: the wedges a reader counts. */
var POLAR_ANGLE_BINS = 12;

/** Which wedge of the rose an angle to the axis falls in: the last one takes 180° too. */
function polarBinOf(angle) {
  return Math.min(POLAR_ANGLE_BINS - 1, Math.floor(angle / (180 / POLAR_ANGLE_BINS)));
}
