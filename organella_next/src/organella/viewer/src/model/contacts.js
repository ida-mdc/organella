//
// Both instances of a contact are of the same structure, so this section is about one
// structure at a time - one `contact_entity` per pair is all a contact needs.
//
// A group is a chain of them at the slider's gap, and once it exists it is a thing with a
// position, so the last two panels ask a group exactly what the section above asks an
// instance: how far is it from each other structure, and is it close to two at once. The
// answer for a group is its *closest approach* - the nearest any of its members gets -
// because that is what "this network reaches the membrane" means.

/**
 * Structures this section can say anything about: ones with a recorded contact, and with
 * more than one instance to form a group from.
 *
 * A batch of whole-structure masks, or of structures segmented as a single instance each,
 * has no group to describe - the whole section is then an elaborate way of saying "one".
 */
function contactStructures() {
  var instances = {};
  INSTANCES.forEach(function(r) {
    instances[r.entity_name] = (instances[r.entity_name] || 0) + 1;
  });
  return uniq(CONTACTS.map(function(e){ return e.entity_name; }).filter(Boolean))
    .filter(function(name){ return (instances[name] || 0) > 1; });
}

// Largest gap actually recorded - drives the slider range so it always fits the data
// (--contact-max-um may be well below the 0.5 µm the slider used to assume).
function contactMaxGap() {
  var m = 0;
  CONTACTS.forEach(function(e){ if (e.gap_um != null && e.gap_um > m) m = e.gap_um; });
  return m > 0 ? m : 0.5;
}

/**
 * Union-find over one object's instances of one structure.
 *
 * Seeded with every instance, so an instance touching nothing is a group of one: that is
 * what makes "in contact" a share of the population and not a share of the pairs.
 */
function contactClusters(objectId, entity, thr) {
  var parent = {};
  var find = function(x) { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  var ensure = function(k) { if (!(k in parent)) parent[k] = k; };
  INSTANCES.forEach(function(r) {
    if (r.entity_name !== entity) return;
    if (objectId !== undefined && r.object_id !== objectId) return;
    ensure(String(r.label_id));
  });
  CONTACTS.forEach(function(e) {
    if (e.entity_name !== entity) return;
    if (objectId !== undefined && e.object_id !== objectId) return;
    if (e.gap_um == null || e.gap_um > thr) return;
    var ka = String(e.label_a), kb = String(e.label_b);
    ensure(ka); ensure(kb);
    parent[find(ka)] = find(kb);
  });
  var members = {};
  Object.keys(parent).forEach(function(k) {
    var root = find(k);
    (members[root] = members[root] || []).push(k);
  });
  var groups = Object.keys(members).map(function(r){ return members[r]; });
  return {
    groups: groups.filter(function(m){ return m.length > 1; }),
    sizes: groups.filter(function(m){ return m.length > 1; }).map(function(m){ return m.length; }),
    instances: Object.keys(parent).length
  };
}

/**
 * Every group in scope as a row of its own, positioned like an instance.
 *
 * One row per group, carrying its object and its object's experimental group - so the
 * facets work the same as anywhere else - its size, and one distance column per target
 * holding the group's closest approach to it. That is what lets the two panels below be
 * the same code the instance section draws through.
 */
function groupRows(scope, entity, thr) {
  // Not the distance to the nearest instance of the same structure: for a group that is
  // one of its own members, a voxel step away by construction, and the same tiny number
  // for every group. It says something about an instance and nothing about a network.
  var targets = distCols(INSTANCES.filter(function(r){ return r.entity_name === entity; }))
    .filter(function(col){ return col !== 'distance_to_closest_same_type_um'; });
  var groupOf = {};
  OBJECTS.forEach(function(r){ groupOf[r.object_id] = r.group_id; });
  var rows = [];
  scope.forEach(function(oid) {
    var byLabel = {};
    INSTANCES.forEach(function(r) {
      if (r.entity_name === entity && r.object_id === oid) byLabel[String(r.label_id)] = r;
    });
    contactClusters(oid, entity, thr).groups.forEach(function(members) {
      var row = { object_id: oid, group_id: groupOf[oid], size: members.length };
      targets.forEach(function(col) {
        var closest = Infinity;
        members.forEach(function(label) {
          var member = byLabel[label];
          if (!member) return;
          var v = member[col];
          if (v != null && isFinite(v) && v < closest) closest = v;
        });
        if (isFinite(closest)) row[col] = closest;
      });
      rows.push(row);
    });
  });
  return { rows: rows, targets: targets };
}

/**
 * Every group scored against the groups of its own size the same object could have formed.
 *
 * The pool is that object's own instances of the structure - a group is a chain of one
 * structure, so that is what a group of size k could have been - measured to the same
 * target. `<target>__reach` keeps the concrete distance beside the score, because the score
 * alone does not say how close "closer than chance" actually is.
 */
function groupBaselineRows(scope, entity, thr) {
  var built = groupRows(scope, entity, thr);
  var pool = {};
  INSTANCES.forEach(function(r) {
    if (r.entity_name !== entity || scope.indexOf(r.object_id) < 0) return;
    var byTarget = pool[r.object_id] = pool[r.object_id] || {};
    built.targets.forEach(function(col) {
      var v = r[col];
      if (v == null || !isFinite(v)) return;
      (byTarget[col] = byTarget[col] || []).push(v);
    });
  });
  var rows = built.rows.map(function(group) {
    var out = { object_id: group.object_id, group_id: group.group_id, size: group.size };
    built.targets.forEach(function(col) {
      var distances = (pool[group.object_id] || {})[col];
      if (!distances) return;
      var score = baselineScore(distances, group.size, group[col]);
      if (score == null) return;
      out[col] = score * 100;
      out[col + '__reach'] = group[col];
    });
    return out;
  });
  return { rows: rows, targets: built.targets };
}
