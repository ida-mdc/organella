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
function screenError(node, eye, focalPx) {
  return node.error * focalPx / Math.max(distanceToBox(eye, node.min, node.max), 1e-9);
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
 *   options  { tolerancePx = 1.5, maxVertices = Infinity, isLoaded(node) }
 *   returns  { draw: [node], request: [node] (worst first), wanted: [node], vertices }
 */
function selectCut(tree, view, options) {
  options = options || {};
  var tolerance = options.tolerancePx == null ? 1.5 : options.tolerancePx;
  var budget = options.maxVertices == null ? Infinity : options.maxVertices;
  var isLoaded = options.isLoaded || function(){ return true; };
  var seen = function(node){ return !view.planes || boxInFrustum(view.planes, node.min, node.max); };

  var heap = [], wanted = [], vertices = 0;
  tree.roots.forEach(function(root) {
    if (!seen(root)) return;
    vertices += root.vertices;
    pushHeap(heap, { node: root, priority: screenError(root, view.eye, view.focalPx) });
  });
  while (heap.length) {
    var top = popHeap(heap), node = top.node;
    var kids = node.children.filter(seen);
    var gain = kids.reduce(function(n, k){ return n + k.vertices; }, 0) - node.vertices;
    // Fine enough, nothing finer, or too dear: this is where the cut stops. A node whose children are all out of view
    // stops too: what it holds that the camera sees is only a corner of its cell.
    if (top.priority <= tolerance || !node.children.length || !kids.length || vertices + gain > budget) {
      wanted.push(node);
      continue;
    }
    vertices += gain;
    kids.forEach(function(k){ pushHeap(heap, { node: k, priority: screenError(k, view.eye, view.focalPx) }); });
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
  request.sort(function(a, b){ return screenError(b, view.eye, view.focalPx) - screenError(a, view.eye, view.focalPx); });
  var drawnVertices = draw.reduce(function(n, node){ return n + node.vertices; }, 0);
  return { draw: draw, request: request, wanted: wanted, vertices: drawnVertices };
}
