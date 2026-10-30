/** The objects whose geometry the filter leaves, so what is drawn is what the rest of the page is about. */
function filteredGeometry() {
  return GEOMETRY.filter(function(o) {
    return !st.only || (plotGroupBy === 'group' ? o.group : o.id) === st.only;
  });
}

/** Which column the "Showing" filter reads, which is whichever the charts are grouped by. */
function facetKey() { return plotGroupBy === 'group' ? 'group_id' : 'object_id'; }

/** The values that filter can take, in the order it offers them. */
function facetChoices() {
  return plotGroupBy === 'group' ? groupNames() : objectNames();
}

/** The global filter: everything, or one group / one object. */
function inScope(r) {
  return !st.only || r[facetKey()] === st.only;
}

function filteredInstances() { return INSTANCES.filter(inScope); }

function filteredEnts() { return ENTS.filter(inScope); }

function filteredObjects() { return OBJECTS.filter(inScope); }

/** The objects the filter leaves, by name. */
function scopedObjectNames() {
  return uniq(filteredObjects().map(function(r){ return r.object_id; }).filter(Boolean));
}
