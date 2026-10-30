// ── which columns of the instance table are what ─────────────────────────────
// The wide distance columns are built here (see distColumnsFor), so which column is
// a distance is known directly and never sniffed out of its name: a structure called
// "distance_to_x" would have fooled a regex.
var NEVER_A_METRIC = new Set(['object_id','group_id','entity_name','entity_kind','label_id',
  'spatial_dims','polar_nz','polar_ny','polar_nx','polar_az_deg','polar_el_deg','polar_angle_deg']);

function distColSet() {
  // The nearest instance of the same structure is a distance too, and belongs with the
  // other distances and not among the shape metrics — otherwise it is drawn twice.
  var s = new Set(['distance_to_closest_same_type_um']);
  DIST_TARGETS.forEach(function(t) {
    var c = distColumnsFor(t);
    s.add(c.min); s.add(c.mean); s.add(c.histMin); s.add(c.histMax); s.add(c.hist);
    s.add(c.end); s.add(c.endFar);
  });
  return s;
}

function hasValue(rows, col) {
  return rows.some(function(r){ return r[col] != null && isFinite(r[col]); });
}

/** Per-instance morphology metrics: no distances, no direction components. */
function nonDistMetricCols(rows) {
  var dists = distColSet();
  return COLS.filter(function(c) {
    return !NEVER_A_METRIC.has(c) && !dists.has(c) && hasValue(rows, c);
  });
}

/** Closest-point distance per instance, one column per target. */
function distCols(rows) {
  return DIST_TARGETS.map(function(t){ return distColumnsFor(t).min; })
    .filter(function(c){ return hasValue(rows, c); })
    .concat(hasValue(rows, 'distance_to_closest_same_type_um')
      ? ['distance_to_closest_same_type_um'] : []);
}

/** For trends: the mean over an instance's body where it was measured, else the closest point. */
function scatterDistCols(rows) {
  var out = [];
  DIST_TARGETS.forEach(function(t) {
    var c = distColumnsFor(t);
    if (hasValue(rows, c.mean)) out.push(c.mean);
    else if (hasValue(rows, c.min)) out.push(c.min);
  });
  if (hasValue(rows, 'distance_to_closest_same_type_um')) out.push('distance_to_closest_same_type_um');
  return out;
}

/**
 * Metrics the report has per instance that a geometry file does not carry.
 *
 * Geometry is written with the instance's own measurements on it - volume, sphericity, the
 * skeleton, where it sits - but a distance belongs to a *pair*, and the pairs are rows of
 * the report, not columns of a surface. So the scene and the gallery read those from
 * the report and join them on by structure and label, which also means a report can be
 * coloured by them without meshing anything again.
 */
function reportOnlyMetrics(objectId) {
  var rows = INSTANCES.filter(function(r) {
    return objectId == null || String(r.object_id) === String(objectId);
  });
  return distCols(rows).filter(function(col) {
    // The nearest of its own kind rides in the geometry file already.
    return col !== 'distance_to_closest_same_type_um';
  }).concat(endDistCols(rows));
}

/**
 * The range a metric colouring should span, for one structure.
 *
 * The report's own instances where it has them - which is the whole population, not the
 * handful of cards on screen - so the colour of a card means the same thing whichever end
 * of the sort you are looking at. A whole-structure mask has no instance rows, and one
 * card per object *is* its population, so there the cards themselves are the range.
 */
function metricSpan(entity, objects, metric, shownRows) {
  var ids = new Set((objects || []).map(function(o){ return String(o.id); }));
  var values = INSTANCES.filter(function(r) {
    return r.entity_name === entity && (!ids.size || ids.has(String(r.object_id)));
  }).map(function(r){ return r[metric]; })
    .filter(function(v){ return v != null && isFinite(v); });
  if (values.length < 2) {
    values = (shownRows || []).map(function(r){ return numOrNull(r.value); })
      .filter(function(v){ return v != null && isFinite(v); });
  }
  if (!values.length) return { low: 0, high: 1 };
  var low = Math.min.apply(null, values), high = Math.max.apply(null, values);
  return { low: low, high: high > low ? high : low + 1 };
}

/** ``{structure|label: value}`` of one report metric, for one object. */
function reportMetricValues(objectId, metric) {
  var out = {};
  INSTANCES.forEach(function(r) {
    if (String(r.object_id) !== String(objectId)) return;
    var value = r[metric];
    if (value != null && isFinite(value)) out[r.entity_name + '|' + r.label_id] = Number(value);
  });
  return out;
}

/** Nearest-tip distance per instance, one column per target, for the structures that have tips. */
function endDistCols(rows) {
  return DIST_TARGETS.map(function(t){ return distColumnsFor(t).end; })
    .filter(function(c){ return hasValue(rows, c); });
}

/** Targets whose per-voxel histograms were recorded. */
function histTargets(rows) {
  return DIST_TARGETS.filter(function(t) {
    var c = distColumnsFor(t);
    return rows.some(function(r){ return r[c.hist] != null; });
  });
}

function distColLabel(col) {
  if (col === 'distance_to_closest_same_type_um') return 'nearest of the same kind';
  return col.replace(/^distance_to_/, '')
    .replace(/_end_far_um$/, ' (far end)')
    .replace(/_end_um$/, ' (nearest end)')
    .replace(/_mean_um$/, ' (mean)')
    .replace(/_um$/, '')
    .replace(/_/g, ' ')
    .trim();
}

function axisColLabel(col) {
  if (col.indexOf('distance_to_') === 0) {
    if (col === 'distance_to_closest_same_type_um') return 'distance to the nearest of the same kind';
    // Said in full: on an axis "membrane (mean)" does not say that it is a distance.
    var target = distColLabel(col).replace(/\s*\([^)]*\)$/, '');
    var how = /_end_far_um$/.test(col) ? 'far end' : /_end_um$/.test(col) ? 'nearest end'
      : /_mean_um$/.test(col) ? 'average over its whole body' : 'nearest point';
    return 'distance to ' + target + ', ' + how;
  }
  // The curated name where the metric has one, with its unit taken off because every
  // caller adds that itself. De-underscoring the column is the fallback, and on its own it
  // titled a panel "aspect ratio major minor" and another "polar dist".
  var known = labelFor(col);
  if (known && known !== col) return known.replace(/\s*\([^)]*\)\s*$/, '').toLowerCase();
  return col.replace(/_um3$|_um2$|_um$|_deg$/, '').replace(/_/g, ' ').trim();
}

function axisColUnit(col) {
  if (col.indexOf('distance_to_') === 0 || col === 'polar_dist_um' || col === 'length_um') return 'µm';
  if (/_um3$/.test(col)) return 'µm³';
  if (/_um2$/.test(col)) return 'µm²';
  if (/_deg$/.test(col)) return '°';
  return '';
}

function axisColTitle(col) {
  var u = axisColUnit(col);
  return axisColLabel(col) + (u ? ' (' + u + ')' : '');
}

function correlationAxisCols(rows) {
  // The end distances are offered as axes but not as pair panels: "length against how far
  // its tip sits from the centrioles" is a question worth an axis, where one panel per
  // pair of them would be sixty panels for the same row of structures.
  return nonDistMetricCols(rows).concat(scatterDistCols(rows)).concat(endDistCols(rows));
}

var METRIC_LABELS = {
  volume_um3: 'Volume (µm³)',
  area_um2: 'Area (µm²)',
  surface_area_um2: 'Surface area (µm²)',
  perimeter_um: 'Perimeter (µm)',
  sphericity: 'Sphericity',
  circularity: 'Circularity',
  aspect_ratio_major_minor: 'Aspect ratio (major/minor)',
  branches: 'Skeleton branches',
  length_um: 'Skeleton length (µm)',
  tortuosity: 'Tortuosity',
  distance_to_closest_same_type_um: 'Distance to nearest same type (µm)',
  // The polarity metrics name the centre they are measured from, which is the thing the
  // run calls whatever it calls it, so they are built and not fixed.
  polar_dist_um: null,
  polar_az_deg: null,
  polar_el_deg: null,
  polar_angle_deg: null
};

var POLARITY_LABELS = {
  polar_dist_um: function(){ return 'Distance from ' + noun() + ' centre (µm)'; },
  polar_az_deg: function(){ return 'Azimuth from ' + noun() + ' centre (°)'; },
  polar_el_deg: function(){ return 'Elevation from ' + noun() + ' centre (°)'; },
  polar_angle_deg: function(){ return 'Angle from ' + noun() + ' centre (°)'; }
};

var METRIC_ORDER = Object.keys(METRIC_LABELS);

// Metrics only one dimensionality has. A geometry file declares every column whether it was
// filled or not, so the switch reads the report instead.
var METRICS_3D_ONLY = new Set(['volume_um3', 'surface_area_um2', 'sphericity',
                               'polar_az_deg', 'polar_el_deg']);

var METRICS_2D_ONLY = new Set(['area_um2', 'perimeter_um', 'circularity', 'polar_angle_deg']);

/** The metrics worth offering for this report, in curated order. */
function geometryMetrics() {
  var drop = PLANAR ? METRICS_3D_ONLY : METRICS_2D_ONLY;
  return METRIC_ORDER.filter(function(m){ return !drop.has(m); });
}

function labelFor(col) {
  if (POLARITY_LABELS[col]) return POLARITY_LABELS[col]();
  if (METRIC_LABELS[col]) return METRIC_LABELS[col];
  // A widened distance column, which is named after the structure it was measured to.
  if (col.indexOf('distance_to_') === 0) {
    return 'Distance to ' + distColLabel(col) + ' (µm)';
  }
  return col;
}

function numOrNull(v) { return v === null || v === undefined ? null : Number(v); }
