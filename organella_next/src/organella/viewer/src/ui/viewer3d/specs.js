//
// A chart says "median distance 0.2 µm" or "V = 0.4" about a whole population; what that is
// *made of* is a shape in a cell. So a chart can hand the viewer a small description of
// itself - a spec - and the viewer draws the same quantity on one object: the instances
// coloured by the very column the chart plots, the groups at the very gap the slider sits
// at, the axis and the cone an angle is measured against. The spec is data, not a callback,
// so the viewer owns how it is drawn and a chart only has to say what it is.
//
//   kind   'metric'    one column of an instance, coloured on the instances
//          'distance'  the same, to a target structure (which is drawn faint behind)
//          'both'      the larger of two distances: close to two structures at once
//          'group'     contact groups at the contacts slider's gap
//          'polarity'  the reference axis and the directions measured against it
//          'structure' one whole-structure mask, on its own

/** Two panels are the same panel when their specs agree on what they are about. */
function explainKey(spec) {
  return [spec.kind, spec.aspect || '', spec.entity || '', spec.metric || '',
          (spec.targets || []).join(','), spec.ref || '', spec.structure || '',
          spec.objectId || '', spec.radius || ''].join('|');
}

/** The structures a spec needs loaded, besides the mask that bounds the object. */
function explainEntities(spec) {
  var names;
  if (spec.kind === 'polarity' && (spec.aspect === 'map' || spec.aspect === 'cellmap')) {
    names = polarSubjects(spec.ref).concat([spec.ref]);
  } else if (spec.kind === 'polarity') names = [spec.structure, spec.ref];
  else names = [spec.entity].concat(spec.targets || []);
  var bounding = boundingMaskName();
  if (bounding) names.push(bounding);
  return uniq(names.filter(Boolean));
}

/** The structures drawn solid; every other loaded one is drawn faint behind them. */
function explainShown(spec) {
  if (spec.kind === 'polarity' && (spec.aspect === 'map' || spec.aspect === 'cellmap')) {
    return new Set(polarSubjects(spec.ref).concat([spec.ref]));
  }
  return new Set(spec.kind === 'polarity' ? [spec.structure, spec.ref] : [spec.entity]);
}

/** One instance's value from the joined columns: the larger of two for 'both', null if either is missing. */
function explainedValue(maps, key) {
  var values = maps.map(function(m){ return key in m ? m[key] : null; });
  if (values.some(function(v){ return v === null; })) return null;
  return Math.max.apply(null, values);
}

/** The report columns whose value colours the instances: one, or two for 'both'. */
function explainValueCols(spec) {
  return spec.metrics || (spec.metric ? [spec.metric] : []);
}

function objectHasStructure(objectId, name) {
  var matches = function(r){ return String(r.object_id) === String(objectId) && r.entity_name === name; };
  return INSTANCES.some(matches) || ENTS.some(matches);
}

/** The structures with a direction of their own from the centre, other than the reference. */
function polarityStructures(ref) {
  return uniq(INSTANCES.filter(function(r){ return r.entity_name !== ref && polarUnit(r); })
                       .map(function(r){ return r.entity_name; }));
}

/** Which structure a distance column measures to, whichever end or mean of it. */
function targetOfColumn(col) {
  var found = null;
  DIST_TARGETS.forEach(function(t) {
    var c = distColumnsFor(t);
    if (c.min === col || c.mean === col || c.end === col) found = t;
  });
  return found;
}

// What each kind of chart says about itself. Prose only: what is drawn, in this report's own
// words, and no figures - the viewer's readout is where counts are.

function metricSpec(entity, col) {
  var label = labelFor(col);
  var named = dataName(entity);
  return { kind: 'metric', entity: entity, metric: col, valueLabel: label,
           unit: axisColUnit(col) || '',
           title: entity + ' · ' + label.replace(/ \(.*\)$/, ''),
           text: 'Every ' + named + ' instance of this ' + noun() + ', coloured by its '
             + escHtml(label.replace(/ \(.*\)$/, '').toLowerCase()) + ' - the value each'
             + ' point in the chart is made of.' };
}

function distanceSpec(entity, col) {
  var target = targetOfColumn(col);
  var tip = /_end_/.test(col);
  var named = dataName(entity);
  var spec = { kind: 'distance', entity: entity, metric: col, unit: 'µm',
               targets: target ? [target] : [] };
  spec.valueLabel = labelFor(col);
  spec.title = entity + (tip ? ' ends' : '') + ' to ' + (target || distColLabel(col));
  spec.text = target
    ? 'Every ' + named + ' instance is shaded by how far '
      + (tip ? 'its nearest end' : 'it') + ' is from the nearest ' + dataName(target)
      + ': magenta is near, cyan is far. '
      + dataName(target) + ' is drawn see-through in amber. Slide the distance to keep only'
      + ' the instances within it.'
    : 'Every ' + named + ' instance is shaded by how far it is from the nearest other '
      + named + ': magenta is near, cyan is far. Slide the distance to keep only the instances within it.';
  return spec;
}

function bothSpec(entity, colA, colB) {
  // The nearest of the structure's own kind has no target but itself.
  var own = 'distance_to_closest_same_type_um';
  var a = colA === own ? entity : targetOfColumn(colA);
  var b = colB === own ? entity : targetOfColumn(colB);
  var named = dataName(entity);
  return { kind: 'both', entity: entity, metric: colA + '+' + colB, metrics: [colA, colB],
           targets: [a, b].filter(Boolean), unit: 'µm',
           valueLabel: 'Distance to the further of ' + (a || colA) + ' and ' + (b || colB) + ' (µm)',
           title: entity + ' near ' + (a || colA) + ' and ' + (b || colB),
           text: 'Every ' + named + ' instance is shaded by the <b>larger</b> of its two distances - to '
             + dataName(a || colA) + ' and to ' + dataName(b || colB) + ' - which is the distance at which'
             + ' it is close to both: magenta is near both, cyan is far from at least one. The two structures'
             + ' are drawn see-through in amber. Slide the distance to keep the instances within'
             + ' it of both.' };
}

/**
 * The group charts are five different questions about the same groups, so five pictures:
 * how large they are (`size`), whether an instance is in one at all (`share`), how close a
 * group gets to a structure (`reach`, and `both` for two of them at once), and that same
 * closeness set against groups of the same size (`baseline`).
 */
function groupSpec(entity, aspect, cols) {
  aspect = aspect || 'size';
  var named = dataName(entity);
  var targets = (cols || []).map(function(c){ return targetOfColumn(c); }).filter(Boolean);
  var spec = { kind: 'group', aspect: aspect, entity: entity, metrics: cols || null,
               metric: (cols || []).join('+') || null, targets: targets, unit: 'µm' };
  var gap = ' Instances closer than the gap to one another chain into a group, and the slider is the'
    + ' chart’s own gap, so moving either moves both.';
  if (aspect === 'share') {
    spec.title = '% of ' + entity + ' reaching another';
    spec.text = 'Each ' + named + ' instance is either in a group of two or more - drawn in its own colour - or'
      + ' touches nothing and is dim. The chart’s number is the share of instances that are in a group.' + gap;
  } else if (aspect === 'reach' || aspect === 'baseline') {
    var t = targets[0];
    spec.valueLabel = 'Closest approach of the group to ' + (t || 'the structure') + ' (µm)';
    spec.title = entity + ' group to ' + (t || (cols && cols[0]) || 'a structure');
    spec.text = 'Each group of ' + named + ' is shaded by its closest approach to ' + dataName(t || 'the structure')
      + ': the nearest any member gets. Magenta is near, cyan is far. '
      + dataName(t || 'It') + ' is drawn see-through in amber; instances touching'
      + ' nothing are not groups and stay faint.'
      + (aspect === 'baseline'
          ? ' The chart scores each distance against groups of the same size that could have formed by chance, because'
            + ' a larger group reaches closer whatever it is made of; the picture is the distance underneath the score.'
          : '') + gap;
  } else if (aspect === 'both') {
    spec.valueLabel = 'Closest approach to the further of ' + targets.join(' and ') + ' (µm)';
    spec.title = entity + ' groups near ' + targets.join(' and ');
    spec.text = 'Each group of ' + named + ' is shaded by the larger of its two closest approaches - to '
      + targets.map(dataName).join(' and ') + ' - the distance at which the group reaches both. Magenta is near both. The'
      + ' two structures are drawn see-through in amber.' + gap;
  } else {
    spec.title = entity + ' groups';
    spec.text = 'Each group of ' + named + ' is shown on its own, coloured by how large it is on the scale below the cell,'
      + ' with everything else grey; the groups play through largest first. Widen the gap'
      + ' and groups merge: the colours shift towards one large network.' + gap;
  }
  return spec;
}

function structureSpec(entity) {
  return { kind: 'structure', entity: entity, title: 'The whole ' + entity,
           text: 'The ' + dataName(entity) + ' as one shape in this ' + noun()
             + ' - the thing each measurement in the chart is taken of, once per '
             + noun() + '.' };
}

/**
 * The polarity charts all start from the same picture - the centre, and the arrow to the
 * reference - and each adds what its own chart is made of.
 */
function polaritySpec(structure, ref, aspect, objectId, radius) {
  var named = dataName(structure);
  var common = 'From the ' + noun() + ' centre (the dot), the grey arrow points at '
    + dataName(ref) + ': that direction is 0°. ';
  var tail = {
    angle: 'The ' + named + ' that lie in the wedge of angles you choose are solid and the rest'
      + ' faint; the height of that wedge in the chart is how many are solid. Slide to step'
      + ' round the wedges. The thick dark arrow is the average direction, and its length is R.',
    index: 'Each dot is one ' + named + '. The orange arrow is their average direction: its'
      + ' length is R, and the orange dot where it reaches along the grey arrow is V. Slide to'
      + ' push every instance out to the same distance: only the directions count here, not'
      + ' how far out they are.',
    where: 'Each dot is one ' + named + '. Slide to lay them flat into the chart: how far round'
      + ' from the grey arrow an instance is becomes its place across, and how far from the'
      + ' centre it sits becomes its height.',
    map: 'Every dot is one instance of one structure, in that structure\u2019s colour. Slide to turn the '
      + noun() + ' into the plot: first each instance is swung round the grey arrow into one half-plane,'
      + ' which keeps its angle to the arrow and its distance and drops only which side it was on - the one'
      + ' thing the plot leaves out; then the instances of each structure gather into one diamond, their'
      + ' average, which is what the plot draws for this ' + noun() + '. The ring on the chart marks this'
      + ' ' + noun() + '\u2019s diamonds, and the faint ones that arrive are the rest of its group.',
    cellmap: 'Each dot is one ' + named + '. Slide to lay the ' + noun() + ' flat into the full'
      + ' circle: each instance is projected onto a plane through the grey arrow, its angle'
      + ' round the circle is its angle in that plane, and its distance from the centre is the'
      + ' radius. Which way is up in that plane is arbitrary.'
  };
  return { kind: 'polarity', structure: structure, ref: ref, aspect: aspect,
           objectId: objectId || null, radius: radius || 'centre',
           title: (aspect === 'map' || aspect === 'cellmap' ? 'Every structure' : structure) + ' towards ' + ref,
           text: common + tail[aspect] };
}
