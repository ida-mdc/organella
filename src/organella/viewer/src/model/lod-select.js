// Which fragments of a multiresolution mesh to draw from where the camera is.
//
// A mesh is stored as levels of detail, level 0 the finest, each cut into fragments on a grid; the grids are nested
// (one origin, a cell twice as wide on each level up), so the fragments form a tree: a fragment's children are the
// fragments of the level below that lie inside its cell. A *cut* through that tree covers the surface once, with no
// overlap and no gap, and is what gets drawn. Pure functions of numbers, so they run the same in a test as in the page.
//
// Everything is in the mesh's own frame (µm): the caller moves the camera into it.

/** The manifest of one mesh as a tree of fragments: nodes by level, parents, children and each one's box. */
function buildFragmentTree(manifest) {
  var levels = manifest.levels.map(function(level, l) {
    var nodes = new Map();
    level.fragments.forEach(function(f, index) {
      var min = [0, 1, 2].map(function(a){ return level.origin[a] + f.ijk[a] * level.cell[a]; });
      var max = [0, 1, 2].map(function(a){ return min[a] + level.cell[a]; });
      nodes.set(f.ijk.join(','), {
        level: l, index: index, id: l + ':' + f.ijk.join(','), ijk: f.ijk, min: min, max: max,
        vertices: f.nv, triangles: f.nt, offset: f.off, size: f.size,
        error: level.error, parent: null, children: []
      });
    });
    return { error: level.error, nodes: nodes };
  });
  // A cell's parent is the cell of the next level up that holds it: its index halved on every axis.
  for (var l = 0; l + 1 < levels.length; l++) {
    levels[l].nodes.forEach(function(node) {
      var parent = levels[l + 1].nodes.get(node.ijk.map(function(v){ return v >> 1; }).join(','));
      if (parent) { node.parent = parent; parent.children.push(node); }
    });
  }
  // The roots are the nodes nothing above holds: the whole top level, and any cell a decimated level lost its triangles in
  // while a finer one still has some, which would otherwise never be reached.
  var roots = [];
  levels.forEach(function(level){ level.nodes.forEach(function(node){ if (!node.parent) roots.push(node); }); });
  return { levels: levels, roots: roots };
}

/** Distance from a point to a box, 0 inside it. */
function distanceToBox(eye, min, max) {
  var sum = 0;
  for (var a = 0; a < 3; a++) {
    var d = Math.max(min[a] - eye[a], 0, eye[a] - max[a]);
    sum += d * d;
  }
  return Math.sqrt(sum);
}

/**
 * Whether a box touches the volume the camera sees. `planes` are [nx, ny, nz, d] with the normal pointing in, as
 * THREE.Frustum keeps them: a box is out when its corner furthest along a plane's normal is still behind that plane.
 */
function boxInFrustum(planes, min, max) {
  for (var i = 0; i < planes.length; i++) {
    var p = planes[i];
    var x = p[0] >= 0 ? max[0] : min[0], y = p[1] >= 0 ? max[1] : min[1], z = p[2] >= 0 ? max[2] : min[2];
    if (p[0] * x + p[1] * y + p[2] * z + p[3] < 0) return false;
  }
  return true;
}

/** How many pixels the error of a node's level spans from here: what decides whether it is fine enough. */
function screenError(node, eye, focalPx, baseError) {
  var error = baseError ? baseError * Math.pow(2, node.level) : node.error;
  return error * focalPx / Math.max(distanceToBox(eye, node.min, node.max), 1e-9);
}

// A binary max-heap on `.priority`, for taking the node that looks worst next.
function pushHeap(heap, item) {
  heap.push(item);
  var i = heap.length - 1;
  while (i > 0) {
    var up = (i - 1) >> 1;
    if (heap[up].priority >= heap[i].priority) break;
    var t = heap[up]; heap[up] = heap[i]; heap[i] = t;
    i = up;
  }
}
function popHeap(heap) {
  var top = heap[0], last = heap.pop();
  if (heap.length) {
    heap[0] = last;
    var i = 0;
    for (;;) {
      var l = 2 * i + 1, r = l + 1, big = i;
      if (l < heap.length && heap[l].priority > heap[big].priority) big = l;
      if (r < heap.length && heap[r].priority > heap[big].priority) big = r;
      if (big === i) break;
      var t = heap[big]; heap[big] = heap[i]; heap[i] = t;
      i = big;
    }
  }
  return top;
}

/**
 * The cut to draw, and the fragments to fetch to improve it.
 *
 * Starts from the coarsest level and keeps replacing the fragment that looks worst, its error the widest on screen, by
 * its children, until every fragment seen is within `tolerancePx`, or `maxVertices` is spent. Fragments outside the view
 * are dropped. The cut is what is *wanted*; what is *drawn* is that where it has been loaded and, where it has not,
 * the nearest loaded ancestor, so a fragment still on its way never leaves a hole.
 *
 *   view     { eye: [x, y, z], planes: [[nx, ny, nz, d] x 6] or null, focalPx }
 *   options  { tolerancePx = 1.5, maxVertices = Infinity, isLoaded(node), minLevel = 0, exactLevel = null }
 *            minLevel is the finest level allowed: a fragment of that level is never split into finer ones (a cap).
 *            baseError, when given, is the error of level 0 for every mesh, doubling per level: the same level is then wanted
 *            at the same distance whichever mesh it is, where each mesh's own measured errors would give neighbours different ones.
 *            levelCap(node), when given, is the finest level a node's region may be drawn at right now (see blockCaps).
 *            exactLevel pins the cut to that level wherever the camera sees it, whatever the distance or the budget.
 *   returns  { draw: [node], request: [node] (worst first), wanted: [node], vertices }
 */
function selectCut(tree, view, options) {
  options = options || {};
  var tolerance = options.tolerancePx == null ? 1.5 : options.tolerancePx;
  var budget = options.maxVertices == null ? Infinity : options.maxVertices;
  var isLoaded = options.isLoaded || function(){ return true; };
  var exact = options.exactLevel == null ? null : options.exactLevel;
  var base = options.baseError || 0;
  var capOf = options.levelCap || null;
  var minLevel = exact == null ? (options.minLevel || 0) : exact;
  var seen = function(node){ return !view.planes || boxInFrustum(view.planes, node.min, node.max); };

  var heap = [], wanted = [], vertices = 0;
  tree.roots.forEach(function(root) {
    if (!seen(root)) return;
    vertices += root.vertices;
    pushHeap(heap, { node: root, priority: screenError(root, view.eye, view.focalPx, base) });
  });
  while (heap.length) {
    var top = popHeap(heap), node = top.node;
    var kids = node.children.filter(seen);
    var gain = kids.reduce(function(n, k){ return n + k.vertices; }, 0) - node.vertices;
    // Fine enough, nothing finer, or too dear: this is where the cut stops. A node whose children are all out of view
    // stops too: what it holds that the camera sees is only a corner of its cell.
    var stop = exact == null ? (top.priority <= tolerance || vertices + gain > budget) : node.level <= exact;
    if (stop || node.level <= minLevel || (capOf && node.level <= capOf(node)) || !node.children.length || !kids.length) {
      wanted.push(node);
      continue;
    }
    vertices += gain;
    kids.forEach(function(k){ pushHeap(heap, { node: k, priority: screenError(k, view.eye, view.focalPx, base) }); });
  }

  // Draw the wanted fragment where it is loaded, else the nearest loaded ancestor.
  var request = [], chosen = new Map();
  wanted.forEach(function(node) {
    var at = node;
    if (!isLoaded(node)) request.push(node);
    while (at && !isLoaded(at)) at = at.parent;
    if (at) chosen.set(at.id, at);
  });
  // An ancestor drawn covers whatever below it was also chosen: drop the covered, or the surface is drawn twice.
  var draw = [];
  chosen.forEach(function(node) {
    for (var up = node.parent; up; up = up.parent) if (chosen.has(up.id)) return;
    draw.push(node);
  });
  request.sort(function(a, b){ return screenError(b, view.eye, view.focalPx, base) - screenError(a, view.eye, view.focalPx, base); });
  var drawnVertices = draw.reduce(function(n, node){ return n + node.vertices; }, 0);
  return { draw: draw, request: request, wanted: wanted, vertices: drawnVertices };
}


/** The keys of the cells of a cubic grid (cell `size`) that a node's box, moved by `offset`, touches. */
function blockKeys(node, offset, size) {
  var lo = [0, 1, 2].map(function(a){ return Math.floor((node.min[a] + offset[a]) / size); });
  var hi = [0, 1, 2].map(function(a){ return Math.max(lo[a], Math.ceil((node.max[a] + offset[a]) / size) - 1); });
  var keys = [];
  for (var x = lo[0]; x <= hi[0]; x++) for (var y = lo[1]; y <= hi[1]; y++) for (var z = lo[2]; z <= hi[2]; z++) keys.push(x + ',' + y + ',' + z);
  return keys;
}

/**
 * How fine each block of space may be drawn right now, so that every mesh in a block moves to a finer level together.
 *
 * A mesh drawn finer than its neighbour in the same block looks like one label swelling over the other, so a block steps down
 * to level L only once every fragment all the meshes want there, at level L or coarser, has arrived.
 *
 *   records  [{ tree, view, offset, loaded(node), options }]  options are those of selectCut
 *   returns  { capOf(node, offset): the finest level that node's region may be drawn at (the coarsest level where unknown),
 *             missing: [{ record, node }] the fragments that stand in the way, coarsest first: they are what has to be fetched }
 */
function blockCaps(records, depth, size) {
  var ready = new Map(), closed = new Set(), missingNodes = [], asked = new Set();
  for (var level = depth - 1; level >= 0; level--) {
    var missing = new Set(), touched = new Set();
    records.forEach(function(r, index) {
      var options = Object.assign({}, r.options, { minLevel: level, isLoaded: function(){ return true; }, levelCap: null });
      selectCut(r.tree, r.view, options).wanted.forEach(function(node) {
        var absent = !r.loaded(node);
        if (absent && !asked.has(index + '|' + node.id)) { asked.add(index + '|' + node.id); missingNodes.push({ record: index, node: node }); }
        blockKeys(node, r.offset, size).forEach(function(key){ touched.add(key); if (absent) missing.add(key); });
      });
    });
    touched.forEach(function(key) {
      if (closed.has(key)) return;
      if (missing.has(key)) closed.add(key); else ready.set(key, level);
    });
  }
  var capOf = function(node, offset) {
    // A big node's box reaches into blocks with nothing wanted in them, and a coarse level covers blocks a finer one splits up:
    // the node may be split as soon as one block under it is ready, and its children are then judged by the blocks they hold.
    var cap = depth - 1;
    blockKeys(node, offset, size).forEach(function(key){ if (ready.has(key)) cap = Math.min(cap, ready.get(key)); });
    return cap;
  };
  return { capOf: capOf, missing: missingNodes };
}
