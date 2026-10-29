function setText(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }

/**
 * A name that came out of the segmentation, marked as one.
 *
 * "How far is each mito from other structures?" reads like a typo until you know that
 * `mito` is a folder's own word for a structure and not this page's. Every heading and
 * sentence that names one puts it through here.
 */
function dataName(value) {
  return '<span class="data-name">' + escHtml(value) + '</span>';
}

function setHtml(id, html) { var el = document.getElementById(id); if (el) el.innerHTML = html; }

// Percentage with graceful precision: never collapse a real fraction to "0%".
function fmtPct(part, whole) {
  if (!whole) return '0%';
  var p = 100 * part / whole;
  if (p <= 0) return '0%';
  if (p < 1) return '<1%';
  if (p < 10) return p.toFixed(1) + '%';
  return Math.round(p) + '%';
}

// Gap value with precision matched to how small the recorded gaps are.
function fmtGap(v) {
  if (v == null) return '–';
  if (v >= 0.1) return (+v).toFixed(2);
  if (v >= 0.01) return (+v).toFixed(3);
  return (+v).toFixed(4);
}

function escHtml(v) {
  return String(v).replace(/[&<>"]/g, function(c) {
    return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c];
  });
}

// Middle-ellipsis truncation for over-long strings.
function truncMid(s, n) {
  s = String(s);
  if (s.length <= n) return s;
  var h = Math.floor((n - 1) / 2);
  return s.slice(0, h) + '…' + s.slice(s.length - (n - 1 - h));
}

// Shorten a set of facet labels for axis ticks by stripping the prefix and suffix they ALL
// share (trimmed to a separator boundary), so only the distinguishing part shows — e.g.
// "…_27-10-25_1_segmentations" → "1". Full names are kept for hover. Returns an array
// parallel to `labels`.
function shortFacetLabels(labels) {
  var strs = labels.map(String);
  if (strs.length < 2) return strs.map(function(s){ return truncMid(s, 22); });
  var pre = strs[0], suf = strs[0];
  strs.forEach(function(s){ while (pre && s.indexOf(pre) !== 0) pre = pre.slice(0, -1); });
  strs.forEach(function(s){ while (suf && s.slice(s.length - suf.length) !== suf) suf = suf.slice(1); });
  pre = pre.replace(/[^_\- ]*$/, '');   // back off to a separator so tokens stay whole
  suf = suf.replace(/^[^_\- ]*/, '');
  var trimmed = strs.map(function(s){
    return s.slice(pre.length, s.length - suf.length).replace(/^[_\- ]+|[_\- ]+$/g, '') || s;
  });
  // Widen the tick budget until truncation stops mapping two distinct names onto the same
  // label: identical labels become ONE Plotly category, silently summing/merging their bars.
  for (var n = 22; n <= 64; n += 6) {
    var out = trimmed.map(function(s){ return truncMid(s, n); });
    if (uniq(out).length === out.length) return out;
  }
  return trimmed;   // names differ only past 64 chars (or are truly identical) — show them raw
}

/**
 * What a measurement means, in the report's own words, for the caption under its chart.
 *
 * `prefer` says which column the panel is really about, and it matters: `volume_um3` is a
 * whole structure's volume on an entity row and one instance's on an instance row, and the
 * report describes both. A panel about instances that took the first match got told about
 * the mask.
 *
 * Then two more lookups, because the page reshapes the report on the way in: an instance
 * metric loses its `instance_` prefix, and the long distance rows become one wide column
 * per target. Both are answered by the description of the column they came from.
 */
function metricHelp(col, prefer) {
  var text = prefer && COLUMN_HELP[prefer + col] ? COLUMN_HELP[prefer + col]
    : COLUMN_HELP[col] ? COLUMN_HELP[col]
    : COLUMN_HELP['instance_' + col] ? COLUMN_HELP['instance_' + col] : '';
  // Reports written before the worked examples were dropped still carry them in their own footer.
  if (text) return escHtml(text.replace(/\s*Flat or faceted shapes read well below[^]*$/, ''));
  var mean = /^distance_to_(.+)_mean_um$/.exec(col);
  if (mean && COLUMN_HELP.distance_mean_um) {
    return escHtml(COLUMN_HELP.distance_mean_um) + ' Measured to ' + dataName(mean[1]) + '.';
  }
  var end = /^distance_to_(.+)_end_um$/.exec(col);
  if (end && COLUMN_HELP.distance_end_min_um) {
    return escHtml(COLUMN_HELP.distance_end_min_um) + ' Measured to ' + dataName(end[1]) + '.';
  }
  var endFar = /^distance_to_(.+)_end_far_um$/.exec(col);
  if (endFar && COLUMN_HELP.distance_end_max_um) {
    return escHtml(COLUMN_HELP.distance_end_max_um) + ' Measured to ' + dataName(endFar[1]) + '.';
  }
  var closest = /^distance_to_(.+)_um$/.exec(col);
  if (closest && col !== 'distance_to_closest_same_type_um' && COLUMN_HELP.distance_um) {
    return escHtml(COLUMN_HELP.distance_um) + ' Measured to ' + dataName(closest[1]) + '.';
  }
  return '';
}

/**
 * What the reader has to know before believing any distribution below.
 *
 * Objects are segmented by hand, so one is often missing a structure, or was measured
 * without contacts, or is a plane in a batch of volumes. Pooled into a violin all three
 * disappear, so they are read off here and said out loud - and separately from the drawing,
 * so they can be checked.
 */
function overviewSummary() {
  var cols = structureColumns();
  var objects = scopedObjectNames();
  var entRows = filteredEnts();
  var missing = [];
  objects.forEach(function(oid) {
    var have = new Set(entRows.filter(function(r){ return r.object_id === oid; })
                           .map(function(r){ return r.entity_name; }));
    var gone = cols.filter(function(c){ return !have.has(c.name); })
                   .map(function(c){ return c.name; });
    if (gone.length) missing.push({ object: oid, structures: gone });
  });
  var instances = filteredInstances();
  var contacts = CONTACTS.filter(inScope);
  var withInstances = new Set(instances.map(function(r){ return r.object_id; }));
  var withContacts = new Set(contacts.map(function(r){ return r.object_id; }));
  var dims = {};
  filteredObjects().forEach(function(r) {
    var key = r.spatial_dims == null ? 'unknown' : String(r.spatial_dims);
    dims[key] = (dims[key] || 0) + 1;
  });
  return {
    objects: objects.length,
    groups: uniq(filteredObjects().map(function(r){ return r.group_id; }).filter(Boolean)).length,
    structures: cols.map(function(c){ return c.name; }),
    kinds: cols.map(function(c){ return c.kind; }),
    instances: instances.length,
    contacts: contacts.length,
    objectMask: (OBJECTS[0] || {}).object_mask_name || null,
    missing: missing,
    mixedDims: MIXED_DIMS,
    dims: dims,
    // "1 of 2 objects", the same way a column only some rows filled is reported.
    coverage: [
      { label: 'per-instance measurements', present: withInstances.size, of: objects.length },
      { label: 'contacts', present: withContacts.size, of: objects.length }
    ]
  };
}

/**
 * "Not every liver has contacts", for the section that gap is about. Said next to the
 * charts and not at the top of the report: at the top it qualifies nothing the reader
 * has been shown yet; here it qualifies what is on the screen.
 */
function coverageNote(label) {
  var c = overviewSummary().coverage.find(function(e){ return e.label === label; });
  if (!c || !c.of || c.present >= c.of) return '';
  return '<p class="callout callout-warn">Not every ' + noun() + ' has ' + label
    + ', so this section is about those that do, not the whole batch.</p>';
}

/** A number for a readout: three figures, never an exponent for the sizes this page sees. */
function fmtValue(v) {
  if (v === null || v === undefined || !isFinite(v)) return '–';
  var a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : String(Number(v.toPrecision(3)));
}

/**
 * The report's description, as text, with any URL in it made clickable.
 *
 * Built from text nodes and anchors, never from a string of HTML: the description comes out
 * of a file the reader was handed, and nothing in it is markup this page will run. Only
 * http and https match, so an href can only ever be a URL the text also shows - a
 * `javascript:` credit line stays the inert words it looks like.
 */
function renderDescription(into, description) {
  into.textContent = '';
  var urls = /https?:\/\/[^\s<>"')\]]+/g;
  var at = 0, found;
  while ((found = urls.exec(description)) !== null) {
    if (found.index > at) {
      into.appendChild(document.createTextNode(description.slice(at, found.index)));
    }
    // A sentence's punctuation is not part of its URL.
    var url = found[0].replace(/[.,;:]+$/, '');
    var link = document.createElement('a');
    link.href = url;
    link.textContent = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    into.appendChild(link);
    at = found.index + url.length;
  }
  if (at < description.length) {
    into.appendChild(document.createTextNode(description.slice(at)));
  }
}

/** Put the settled noun into the markup that was written before it was known. */
function fillNouns() {
  document.querySelectorAll('[data-noun]').forEach(function(el) {
    el.textContent = NOUN.one;
  });
  document.querySelectorAll('[data-noun-plural]').forEach(function(el) {
    el.textContent = NOUN.many;
  });
  document.querySelectorAll('[data-noun-label]').forEach(function(el) {
    el.textContent = Noun();
  });
}
