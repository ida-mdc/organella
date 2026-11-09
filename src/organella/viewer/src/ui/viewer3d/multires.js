// Levels of detail in the 3D scene: the big meshes are drawn as the fragments the camera needs.
//
// The scene is merged as it always was, and a mesh that has levels of detail is in it at its coarsest, which is what the
// geometry file holds. Once one is big enough on screen to show the difference, its fragments are fetched by byte range,
// its coarse copy in the merged buffer is emptied, and the fragments the cut of model/lod-select.js picks are drawn in its
// place, each as a mesh of its own. Moving the camera picks again; a fragment that has not arrived yet is covered by the
// nearest coarser one that has, so nothing opens a gap while it is on its way.

// An instance smaller than this on screen (its bounding sphere, in CSS pixels across) stays coarse: it could not show more.
var MULTIRES_MIN_PX = 24;
// How wide the error of a level may look on screen before the next finer one is wanted.
var MULTIRES_TOLERANCE_PX = 1.5;
// Vertices drawn from fragments over the whole scene, shared out among the instances nearest first.
var MULTIRES_VERTEX_BUDGET = 3000000;
// Decoded fragments kept, least recently drawn dropped first.
var MULTIRES_CACHE_BYTES = 400 * 1024 * 1024;
// Camera moves are answered this long after the last one: choosing a cut is cheap, but not once per pointer event.
var MULTIRES_DELAY_MS = 120;
// Fragments asked for at once; the rest wait for the next look, when the camera may have made them pointless.
var MULTIRES_REQUESTS = 48;

var _fragmentCache = new Map();    // key -> { geometry, bytes, used }
var _fragmentBytes = 0;
var _fragmentTick = 0;
var _multires = null;              // the scene on show: its records and what it is waiting for

/** The levels-of-detail manifest in a geometry row, parsed; null for a mesh without them. */
function levelsOf(bytes) {
  if (!bytes || !bytes.length) return null;
  try {
    var manifest = JSON.parse(new TextDecoder().decode(bytes));
    return manifest && manifest.levels && manifest.levels.length ? manifest : null;
  } catch (error) {
    return null;
  }
}

function fragmentKey(m, record, node) { return m.objectId + '|' + record.key + '|' + node.id; }

/** Stop refining the scene that was on show: its fragments go with the stage's next clear. */
function multiresReset(stage) {
  if (_multires && _multires.timer) clearTimeout(_multires.timer);
  _multires = null;
  if (stage) stage.onCamera = null;
}

/**
 * Take over the meshes of a merged group that have levels of detail.
 *
 * `items` are what was merged, in order, and `mesh` the merged Mesh: its material is lent to the fragments so they look
 * the same, and its index array is where a refined instance's coarse copy is emptied.
 */
function multiresAttach(ctx, stage, items, mesh) {
  var ranges = mesh.geometry.userData.ranges || [];
  var records = [];
  items.forEach(function(item, i) {
    if (!item.levels || !ranges[i]) return;
    if (!item.levels.tree) item.levels.tree = buildFragmentTree(item.levels);
    var tree = item.levels.tree, offset = item.offset || [0, 0, 0];
    var lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    tree.roots.forEach(function(root) {
      for (var a = 0; a < 3; a++) { lo[a] = Math.min(lo[a], root.min[a]); hi[a] = Math.max(hi[a], root.max[a]); }
    });
    var centre = [0, 1, 2].map(function(a){ return (lo[a] + hi[a]) / 2; });
    records.push({ mesh: mesh, item: item, tree: tree, offset: offset, key: item.key, range: ranges[i], saved: null,
                   shown: new Map(), min: lo, max: hi, centre: centre,
                   radius: Math.sqrt(Math.pow(hi[0] - lo[0], 2) + Math.pow(hi[1] - lo[1], 2) + Math.pow(hi[2] - lo[2], 2)) / 2 });
  });
  if (!records.length) return;
  var source = fragmentSourceOf(ctx.current, records[0].item.levels.file);
  if (!source) return;
  // Called once per merged group and once per whole-structure mask: they all share one scene's state.
  if (!_multires) {
    _multires = { stage: stage, records: [], source: source, objectId: ctx.current.id,
                  timer: null, lastSignature: null, dirty: false, failed: new Map() };
  }
  _multires.records = _multires.records.concat(records);
  _multires.lastSignature = null;
  stage.onCamera = multiresSchedule;
  multiresSchedule();
}

function multiresSchedule() {
  var m = _multires;
  if (!m || m.timer) return;
  m.timer = setTimeout(function(){ m.timer = null; if (_multires === m) multiresUpdate(m); }, MULTIRES_DELAY_MS);
}

/** What the camera sees, in a form the selection can use: where the eye is, six planes, and how many pixels a unit subtends at one unit. */
function multiresView(stage) {
  var THREE = stage.THREE, camera = stage.camera;
  camera.updateMatrixWorld();
  var frustum = new THREE.Frustum();
  frustum.setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  var height = stage.renderer.domElement.clientHeight || 1;
  return {
    eye: [camera.position.x, camera.position.y, camera.position.z],
    planes: frustum.planes.map(function(p){ return [p.normal.x, p.normal.y, p.normal.z, p.constant]; }),
    focalPx: height / (2 * Math.tan((camera.fov * Math.PI / 180) / 2)),
    signature: camera.matrixWorld.elements.join(',') + '|' + height
  };
}

/** The same view from inside a mesh drawn shifted by `offset` (the explode): the eye and the planes move the other way. */
function multiresViewIn(view, offset) {
  return {
    eye: [view.eye[0] - offset[0], view.eye[1] - offset[1], view.eye[2] - offset[2]],
    planes: view.planes.map(function(p) { return [p[0], p[1], p[2], p[3] + p[0] * offset[0] + p[1] * offset[1] + p[2] * offset[2]]; }),
    focalPx: view.focalPx
  };
}

function multiresUpdate(m) {
  var stage = m.stage;
  if (!stage.renderer.domElement.isConnected) { multiresReset(stage); return; }     // the panel was closed while this waited
  // A turn laid over the scene (a chart's plane laid flat) moves the mesh out of the frame the selection works in.
  if (stage.hasPose && stage.hasPose()) { m.records.forEach(function(r){ multiresRestore(m, r); }); return; }
  var view = multiresView(stage);
  if (view.signature === m.lastSignature && !m.dirty) return;
  m.lastSignature = view.signature;
  m.dirty = false;

  // Which instances are big enough on screen to be worth refining, nearest first.
  var candidates = [];
  m.records.forEach(function(record) {
    var local = multiresViewIn(view, record.offset);
    var gap = Math.sqrt(record.centre.reduce(function(n, c, a){ return n + Math.pow(c - local.eye[a], 2); }, 0));
    var px = record.radius * local.focalPx / Math.max(gap, 1e-9);
    if (px < MULTIRES_MIN_PX || !boxInFrustum(local.planes, record.min, record.max)) { multiresRestore(m, record); return; }
    candidates.push({ record: record, local: local, px: px });
  });
  candidates.sort(function(a, b){ return b.px - a.px; });

  var budget = MULTIRES_VERTEX_BUDGET, asks = [];
  var isLoaded = function(record) { return function(node){ return _fragmentCache.has(fragmentKey(m, record, node)); }; };
  candidates.forEach(function(c) {
    var loaded = isLoaded(c.record);
    // Until every root has arrived the coarse copy in the merged buffer stays: a root is the whole mesh at its coarsest.
    var missing = c.record.tree.roots.filter(function(root){ return !loaded(root); });
    if (missing.length) {
      missing.forEach(function(node){ asks.push({ record: c.record, node: node, priority: c.px * 1e6 }); });
      multiresRestore(m, c.record);
      return;
    }
    var cut = selectCut(c.record.tree, c.local, { tolerancePx: MULTIRES_TOLERANCE_PX, maxVertices: Math.max(budget, 0), isLoaded: loaded });
    budget -= cut.vertices;
    multiresShow(m, c.record, cut.draw);
    cut.request.forEach(function(node) {
      asks.push({ record: c.record, node: node,
                  priority: c.px * node.error * view.focalPx / Math.max(distanceToBox(c.local.eye, node.min, node.max), 1e-9) });
    });
  });

  asks.sort(function(a, b){ return b.priority - a.priority; });
  var issued = new Set();
  asks.slice(0, MULTIRES_REQUESTS).forEach(function(ask) {
    var key = fragmentKey(m, ask.record, ask.node);
    if ((m.failed.get(key) || 0) >= 2) return;
    issued.add(key);
    multiresFetch(m, ask.record, ask.node, key);
  });
  FRAGMENT_QUEUE.keepOnly(issued);
  multiresEvict(m);
  stage.draw();
}

function multiresFetch(m, record, node, key) {
  readFragment(m.source, node, key).then(function(raw) {
    if (_fragmentCache.has(key)) return;
    var geometry = decodePayload(raw, 3);
    if (!geometry) throw new Error('empty fragment');
    geometry.normals = vertexNormals(geometry.positions, geometry.indices);
    var bytes = geometry.positions.byteLength + geometry.indices.byteLength + geometry.normals.byteLength;
    _fragmentCache.set(key, { geometry: geometry, bytes: bytes, used: ++_fragmentTick });
    _fragmentBytes += bytes;
    if (_multires === m) { m.dirty = true; multiresSchedule(); }
  }).catch(function(error) {
    if (error && error.dropped) return;     // the camera moved on before it started
    m.failed.set(key, (m.failed.get(key) || 0) + 1);
    if (m.failed.get(key) === 2) console.warn('a mesh fragment would not load, so its instance stays coarser there:', error);
  });
}

/** Draw exactly these fragments of an instance, and none of the coarse mesh it was merged as. */
function multiresShow(m, record, draw) {
  var THREE = m.stage.THREE;
  if (!record.saved) {
    var index = record.mesh.geometry.index;
    record.saved = index.array.slice(record.range.start, record.range.start + record.range.count);
    index.array.fill(0, record.range.start, record.range.start + record.range.count);   // degenerate triangles: nothing drawn
    index.needsUpdate = true;
  }
  var wanted = new Map();
  draw.forEach(function(node){ wanted.set(node.id, node); });
  record.shown.forEach(function(drawn, id) {
    if (wanted.has(id)) return;
    m.stage.scene.remove(drawn);
    drawn.geometry.dispose();
    record.shown.delete(id);
  });
  wanted.forEach(function(node, id) {
    var entry = _fragmentCache.get(fragmentKey(m, record, node));
    if (!entry) return;
    entry.used = ++_fragmentTick;
    if (record.shown.has(id)) return;
    var source = entry.geometry, offset = record.offset, rgb = record.item.rgb;
    var positions = source.positions, colours = new Float32Array(positions.length);
    if (offset[0] || offset[1] || offset[2]) {      // the explode shifts a vertex, which a Mesh's own position cannot say once the scene is posed
      positions = new Float32Array(source.positions.length);
      for (var i = 0; i < positions.length; i += 3) {
        positions[i] = source.positions[i] + offset[0];
        positions[i + 1] = source.positions[i + 1] + offset[1];
        positions[i + 2] = source.positions[i + 2] + offset[2];
      }
    }
    for (var j = 0; j < colours.length; j += 3) { colours[j] = rgb[0]; colours[j + 1] = rgb[1]; colours[j + 2] = rgb[2]; }
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(source.normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(source.indices, 1));
    var drawn = new THREE.Mesh(geometry, record.mesh.material);
    drawn.castShadow = record.mesh.castShadow;
    drawn.receiveShadow = record.mesh.receiveShadow;
    m.stage.scene.add(drawn);
    record.shown.set(id, drawn);
  });
}

/** Put an instance back as the merged buffer has it: its fragments off the stage, its coarse triangles on again. */
function multiresRestore(m, record) {
  if (!record.saved && !record.shown.size) return;
  record.shown.forEach(function(drawn) { m.stage.scene.remove(drawn); drawn.geometry.dispose(); });
  record.shown.clear();
  if (record.saved) {
    var index = record.mesh.geometry.index;
    index.array.set(record.saved, record.range.start);
    index.needsUpdate = true;
    record.saved = null;
  }
}

/** Forget the fragments least recently drawn once there are more than the cache is meant to hold; never one on screen. */
function multiresEvict(m) {
  if (_fragmentBytes <= MULTIRES_CACHE_BYTES) return;
  var onScreen = new Set();
  m.records.forEach(function(record) { record.shown.forEach(function(_, id){ onScreen.add(m.objectId + '|' + record.key + '|' + id); }); });
  var order = Array.from(_fragmentCache.entries()).filter(function(e){ return !onScreen.has(e[0]); })
    .sort(function(a, b){ return a[1].used - b[1].used; });
  for (var i = 0; i < order.length && _fragmentBytes > MULTIRES_CACHE_BYTES; i++) {
    _fragmentBytes -= order[i][1].bytes;
    _fragmentCache.delete(order[i][0]);
  }
}

/**
 * The finest level of one mesh whole, for the look at a single instance: every fragment of level 0 read, inflated, decoded
 * and joined into one geometry. Read outside the scene's queue, which drops what the camera no longer wants.
 */
async function multiresFinest(entry, levels) {
  var source = fragmentSourceOf(entry, levels.file);
  if (!source) return null;
  var level = levels.levels[0];
  var parts = await Promise.all(level.fragments.map(async function(f) {
    return decodePayload(await inflateRaw(await source.read(f.off, f.size)), 3);
  }));
  parts = parts.filter(Boolean);
  if (!parts.length) return null;
  var vertices = parts.reduce(function(n, p){ return n + p.positions.length / 3; }, 0);
  var positions = new Float32Array(vertices * 3);
  var indices = new Uint32Array(parts.reduce(function(n, p){ return n + p.indices.length; }, 0));
  var at = 0, atIndex = 0;
  parts.forEach(function(p) {
    positions.set(p.positions, at * 3);
    for (var i = 0; i < p.indices.length; i++) indices[atIndex + i] = p.indices[i] + at;
    at += p.positions.length / 3;
    atIndex += p.indices.length;
  });
  return { positions: positions, indices: indices };
}
