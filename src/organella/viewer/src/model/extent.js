/** What a batch of this dimensionality calls its size and shape metrics. */
function extentTerms() {
  return PLANAR
    ? { total:'total_area_um2', one:'area_um2', unit:'µm²', word:'area',
        boundary:'perimeter_um', boundaryLabel:'Perimeter (µm)', boundaryUnit:'µm',
        round:'circularity', roundLabel:'Circularity' }
    : { total:'total_volume_um3', one:'volume_um3', unit:'µm³', word:'volume',
        boundary:'surface_area_um2', boundaryLabel:'Surface area (µm²)', boundaryUnit:'µm²',
        round:'sphericity', roundLabel:'Sphericity' };
}

/** A structure's total extent, whichever dimensionality it was measured in. */
function totalExtent(row) {
  var v = row.total_volume_um3;
  return v != null ? v : row.total_area_um2;
}

/** The region a structure's share is measured against, and where that number came from.
 *
 * A bounded object has an extent of its own, and that is the denominator: the share *of the
 * cell*. Without a mask there is no cell, but there is still the region that was analysed,
 * so the denominator is that box and the chart says which it used, and does not quietly
 * normalise to the segmented total and call the result a share of something.
 */
function analysedExtent(object) {
  if (!object) return null;
  var own = object.object_volume_um3 != null ? object.object_volume_um3 : object.object_area_um2;
  if (own != null) return { value: +own, bounded: true };
  var shape = object.object_shape;
  if (shape && shape.length == null && shape.toArray) shape = shape.toArray();  // Arrow list
  if (!shape || !shape.length) return null;
  var sizes = shape.length === 3
    ? [object.pixel_size_Z, object.pixel_size_Y, object.pixel_size_X]
    : [object.pixel_size_Y, object.pixel_size_X];
  var value = 1;
  for (var i = 0; i < shape.length; i++) {
    if (sizes[i] == null) return null;
    value *= (+shape[i]) * (+sizes[i]);
  }
  return { value: value, bounded: false };
}

/**
 * How much of an instance's closeness is its own size, measured on the instances.
 *
 * A point has no extent; an instance touches from its surface. So under "placed wherever
 * there is room" an instance's *closest* point is already nearer any structure than a
 * random sample is, by however much its body reaches - and comparing the two without
 * allowing for that credits every granule with a couple of hundred nanometres it gets for
 * being 400 nm across.
 *
 * The allowance is the gap between an instance's body average and its closest point, which
 * the run measured for every instance against every target. For a sphere that gap is
 * exactly its radius; for a filament running along a structure it is much larger, and for
 * one crossing it much smaller. That is why it is read off the data and not computed
 * from a radius: the shape, the orientation and the target are all already in it.
 *
 * Null without --distance-histograms, which is what measures the body average.
 */
function extentGapUm(rows, target) {
  var col = distColumnsFor(target);
  var gaps = [];
  rows.forEach(function(r) {
    var nearest = r[col.min], body = r[col.mean];
    if (nearest == null || body == null || !isFinite(nearest) || !isFinite(body)) return;
    // The body average cannot sit inside the closest point; anything else is not a gap.
    if (body >= nearest) gaps.push(body - nearest);
  });
  if (!gaps.length) return null;
  gaps.sort(function(a, b){ return a - b; });
  return quantileSorted(gaps, 0.5);
}
