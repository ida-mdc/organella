//
// One table of typed rows becomes the four populations the sections ask about.
// Two things happen on the way, and both are why the sections read from it and
// do not query SQL directly:
//
//   * a deep row carries only `object_id` and what was measured, so the group,
//     the provenance and the structure's kind are joined back on from the row
//     above it — that join is also what makes a deep row honour the filter;
//   * distances are long (one row per instance x target, because target names
//     come from the data), and every question here is per instance: "how far is
//     this mito from the nucleus AND from the membrane" reads two columns of one
//     row, not two rows. So they are widened onto the instance they belong to.
//
// After this the sections read plain objects with the columns their charts name.

var INSTANCE_PREFIX = 'instance_';

/**
 * A name from the report, as a string, or '' where there is none.
 *
 * `String(undefined)` is the literal word "undefined", and a name is exactly where that
 * ends up on screen: as a structure in a selector, in the heading it fills, and in every
 * sentence built from it. A row with no name is dropped instead, and said so once.
 */
function nameOf(value) {
  return value === null || value === undefined ? '' : String(value);
}

/** Drop the rows a population cannot be built from, and say how many there were. */
function named(rows, key, what) {
  var kept = rows.filter(function(r){ return nameOf(r[key]) !== ''; });
  if (kept.length !== rows.length) {
    console.warn('organella: ' + (rows.length - kept.length) + ' ' + what
      + ' row(s) name no structure and were left out');
  }
  return kept;
}

/** `distance_target` → the columns that target's readings are widened into. */
function distColumnsFor(target) {
  return {
    min:  'distance_to_' + target + '_um',
    mean: 'distance_to_' + target + '_mean_um',
    histMin: 'distance_to_' + target + '_hist_min_um',
    histMax: 'distance_to_' + target + '_hist_max_um',
    hist: 'distance_to_' + target + '_hist_um',
    // The same distance read at the instance's skeleton tips: how close its *end* gets,
    // where `min` is how close any part of it gets.
    end:  'distance_to_' + target + '_end_um',
    endFar: 'distance_to_' + target + '_end_far_um'
  };
}

/**
 * Refuse a parquet this page cannot read, and say which kind of unreadable it is.
 *
 * Three kinds, and each sends the reader somewhere different: a file that is not a report
 * at all, a report from before the rows were typed - instances and contacts were list
 * columns on the object row - and a report with nothing in it, which a run where every
 * object failed used to write: a valid parquet with no columns, whose own error message
 * ("Need at least one non-root column") says nothing about the run.
 */
function checkReadable(rows, label) {
  var cols = rows.length ? Object.keys(rows[0]) : [];
  if (!rows.length) {
    throw new Error(label + ' holds no rows. A run where every object failed used to write '
      + 'a report like this; re-run it and see what it says about each object.');
  }
  if (!cols.includes('row_type')) {
    var older = cols.includes('object_id') || cols.includes('cell_id')
      || cols.includes('n_entities');
    throw new Error(older
      ? label + ' is an Organella report from an older version: its instances and contacts '
        + 'were list columns on the object row, a layout this '
        + 'page cannot read. Measure the batch again with organella process.'
      : label + ' has no row_type column, so it is not an Organella report. Produce one '
        + 'with: organella process <folder> -o report.parquet --object-mask NAME');
  }
}

function adapt(rows) {
  var objectRows = [], entityRows = [], instanceRows = [], distanceRows = [], contactRows = [];
  var baselineRows = [];
  DROPPED = 0;
  rows.forEach(function(r) {
    if (r.row_type === 'object') objectRows.push(r);
    else if (r.row_type === 'entity') entityRows.push(r);
    else if (r.row_type === 'instance') instanceRows.push(r);
    else if (r.row_type === 'distance') distanceRows.push(r);
    else if (r.row_type === 'contact') contactRows.push(r);
    else if (r.row_type === 'baseline') baselineRows.push(r);
  });

  // What every deep row needs joined back on.
  var groupOf = {}, dimsOf = {}, maskOf = {};
  objectRows.forEach(function(r) {
    groupOf[r.object_id] = r.imported_path_short != null ? String(r.imported_path_short) : '';
    dimsOf[r.object_id] = r.spatial_dims != null ? Number(r.spatial_dims) : null;
    maskOf[r.object_id] = r.object_mask_name != null ? String(r.object_mask_name) : null;
  });
  var kindOf = {};   // structures are one kind throughout a batch
  entityRows.forEach(function(r) { if (r.entity_kind) kindOf[r.entity_name] = r.entity_kind; });

  var dims = uniq(objectRows.map(function(r){ return r.spatial_dims; })
                            .filter(function(d){ return d != null; }).map(Number));
  PLANAR = dims.length > 0 && dims.every(function(d){ return d === 2; });
  MIXED_DIMS = dims.length > 1;
  // Whether anything bounds the thing decides what to call it, and what can be said about
  // it: with no mask it has no extent of its own, and that column is already absent. It
  // still has a centre - the middle of everything segmented - so the polarity columns are
  // there either way.
  settleNoun(REPORT_NOUN,
             objectRows.some(function(r){ return r.object_mask_name != null; }));

  OBJECTS = objectRows.map(function(r) {
    return Object.assign({}, r, {
      object_id: String(r.object_id),
      group_id: groupOf[r.object_id],
      spatial_dims: dimsOf[r.object_id],
      object_mask_name: maskOf[r.object_id]
    });
  });

  var beforeDrop = entityRows.length + instanceRows.length + contactRows.length;
  entityRows = named(entityRows, 'entity_name', 'entity');
  instanceRows = named(instanceRows, 'instance_entity', 'instance');
  contactRows = named(contactRows, 'contact_entity', 'contact');
  DROPPED = beforeDrop - (entityRows.length + instanceRows.length + contactRows.length);

  ENTS = entityRows.map(function(r) {
    return Object.assign({}, r, {
      object_id: String(r.object_id),
      group_id: r.imported_path_short != null ? String(r.imported_path_short) : groupOf[r.object_id],
      entity_name: nameOf(r.entity_name),
      spatial_dims: dimsOf[r.object_id]
    });
  });

  // ── instances, with instance_* read as the metric it is ──────────────────
  var instanceCols = {};
  INSTANCES = instanceRows.map(function(r) {
    var out = {
      object_id: String(r.object_id),
      group_id: groupOf[r.object_id],
      entity_name: nameOf(r.instance_entity),
      entity_kind: kindOf[r.instance_entity] || 'label',
      label_id: r.instance_label,
      spatial_dims: dimsOf[r.object_id]
    };
    for (var key in r) {
      if (key.indexOf(INSTANCE_PREFIX) !== 0) continue;
      var name = key.slice(INSTANCE_PREFIX.length);
      if (name === 'entity' || name === 'label') continue;
      if (r[key] === null || r[key] === undefined) continue;
      out[name] = r[key];
      instanceCols[name] = 1;
    }
    return out;
  });

  // ── distances, widened onto the instance they were measured from ─────────
  var byInstance = {};
  INSTANCES.forEach(function(r) { byInstance[r.object_id + '|' + r.entity_name + '|' + r.label_id] = r; });
  var targets = {};
  distanceRows.forEach(function(d) {
    var host = byInstance[d.object_id + '|' + d.distance_entity + '|' + d.distance_label];
    if (!host) return;                       // a distance whose instance was not measured
    var target = String(d.distance_target);
    targets[target] = 1;
    var col = distColumnsFor(target);
    if (d.distance_um != null) { host[col.min] = d.distance_um; instanceCols[col.min] = 1; }
    if (d.distance_mean_um != null) { host[col.mean] = d.distance_mean_um; instanceCols[col.mean] = 1; }
    if (d.distance_end_min_um != null) {
      host[col.end] = d.distance_end_min_um; instanceCols[col.end] = 1;
    }
    if (d.distance_end_max_um != null) {
      host[col.endFar] = d.distance_end_max_um; instanceCols[col.endFar] = 1;
    }
    if (d.distance_hist_counts != null) {
      host[col.hist] = d.distance_hist_counts;
      host[col.histMin] = d.distance_hist_min_um;
      host[col.histMax] = d.distance_hist_max_um;
      instanceCols[col.hist] = 1;
      instanceCols[col.histMin] = 1;
      instanceCols[col.histMax] = 1;
    }
  });
  DIST_TARGETS = Object.keys(targets).sort();
  COLS = Object.keys(instanceCols);

  // ── the chance distributions, by the object and structure they are of ────
  //
  // Held as a map of its own, not widened onto anything: there is one per object x target,
  // not one per instance, and every panel that reads it reads it for a whole facet at
  // once.
  CHANCE = {};
  // Per object x structure, and both change with the report.
  _POLAR_OFFSETS = {};
  baselineRows.forEach(function(r) {
    if (r.baseline_target == null) return;
    var id = String(r.object_id);
    (CHANCE[id] = CHANCE[id] || {})[String(r.baseline_target)] = {
      counts: r.baseline_hist_counts,
      min: r.baseline_hist_min_um,
      max: r.baseline_hist_max_um,
      mean: r.baseline_mean_um,
      median: r.baseline_median_um,
      voxels: r.baseline_voxels,
      excluded: r.baseline_excluded != null ? String(r.baseline_excluded) : null
    };
  });

  // ── contacts: both instances of a pair are of one structure ──────────────
  CONTACTS = contactRows.map(function(r) {
    return {
      object_id: String(r.object_id),
      group_id: groupOf[r.object_id],
      entity_name: nameOf(r.contact_entity),
      label_a: r.contact_label_a,
      label_b: r.contact_label_b,
      gap_um: r.contact_gap_um
    };
  });

}

function uniq(arr) { return [...new Set(arr)].sort(); }

function objectNames() { return uniq(OBJECTS.map(function(r){ return r.object_id; }).filter(Boolean)); }

function entityNames() { return uniq(ENTS.map(function(r){ return r.entity_name; })); }

function groupNames() { return uniq(OBJECTS.map(function(r){ return r.group_id; }).filter(Boolean)); }

function kindOrder(kind) { return kind === 'mask' ? 0 : 1; }

/** The structures of the batch, masks first, the object's own mask leading them. */
function structureColumns() {
  var maskName = (OBJECTS[0] || {}).object_mask_name;
  return uniq(ENTS.map(function(r){ return r.entity_name + '|' + r.entity_kind; }))
    .map(function(s){ var p = s.split('|'); return { name: p[0], kind: p[1] }; })
    .sort(function(a, b) {
      var ko = kindOrder(a.kind) - kindOrder(b.kind);
      if (ko) return ko;
      if (a.kind === 'mask' && maskName) {
        var am = a.name === maskName ? 0 : 1, bm = b.name === maskName ? 0 : 1;
        if (am !== bm) return am - bm;
      }
      return a.name.localeCompare(b.name);
    });
}
