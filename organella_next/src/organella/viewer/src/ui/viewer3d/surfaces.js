// Vertices a scene may tessellate its parametric surfaces into, shared out among them.
// Nothing is left out of a scene any more - a cap left structures missing, and a reader
// cannot tell "this object has no more" from "the viewer stopped drawing" - so the
// tessellation gives way instead. A beta cell is 59,284 granules and 4,972 microtubules:
// at a fixed 12x16 the granules alone are 13 million vertices before a tube is drawn.
var SURFACE_VERTEX_BUDGET = 4000000;

/**
 * Decode one geometry blob into vertex positions and indices.
 *
 * The container is written by analysis/meshes.py and shared with geometry_to_blender.py:
 *   [uint32 nV][uint32 nI][float32x3 min_xyz][float32x3 scale_xyz]
 *   [uint16 x nV*3 quantised XYZ][uint32 x nI*perIndex indices]
 * Vertices are µm in XYZ, dequantised against the min/scale that follow the header.
 */
function decodePayload(bytes, perIndex) {
  perIndex = perIndex || 3;
  if (!bytes || bytes.byteLength < 32) return null;
  // Copy out of the Arrow buffer: a Uint16Array view needs its own alignment, and the
  // blob sits at an arbitrary offset inside the batch's memory.
  var buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  var header = new Uint32Array(buffer, 0, 2);
  var nVerts = header[0], nIndices = header[1];
  if (!nVerts || !nIndices) return null;
  var params = new Float32Array(buffer, 8, 6);
  var quantised = new Uint16Array(buffer, 32, nVerts * 3);
  // Two bytes an index unless the surface has more vertices than that can address. The
  // width follows from nVerts and is not recorded, so writer and reader agree by
  // construction - a triangle mesh has twice as many faces as vertices, which makes the
  // index array three quarters of the payload and worth the branch.
  var indexBytes = buffer.slice(32 + nVerts * 6);
  var indices = nVerts < 65536
    ? new Uint16Array(indexBytes)
    : new Uint32Array(indexBytes);

  var positions = new Float32Array(nVerts * 3);
  for (var i = 0; i < nVerts; i++) {
    positions[i * 3]     = params[0] + (quantised[i * 3] / 65535) * params[3];
    positions[i * 3 + 1] = params[1] + (quantised[i * 3 + 1] / 65535) * params[4];
    positions[i * 3 + 2] = params[2] + (quantised[i * 3 + 2] / 65535) * params[5];
  }
  return { positions: positions, indices: indices.subarray(0, nIndices * perIndex) };
}

// Rings and segments of the sphere an ellipsoid is tessellated from. A rendering choice,
// not a property of the data: the stored surface is 15 floats, and this is how finely they
// are turned into triangles. Raise it for a close-up, lower it for a scene of thousands.
// How finely a parametric surface is turned into triangles. Not constants: a scene of
// 64,000 shapes and a scene of five want different answers, and the stored surface is the
// same either way - an ellipsoid is 60 bytes whether it is drawn with 200 triangles or 30.
// setSurfaceDetail shares SURFACE_VERTEX_BUDGET out among whatever is about to be drawn.
var surfaceDetail = { rings: 12, segments: 16, sides: 12 };

/** Pick a tessellation that draws this many surfaces within the vertex budget. */
function setSurfaceDetail(count) {
  var allowance = SURFACE_VERTEX_BUDGET / Math.max(1, count);
  // An ellipsoid of r rings and 2r segments is about 2r² vertices; a tube is its sides
  // times its nodes, so the same allowance buys fewer sides on a long filament.
  var rings = Math.round(Math.sqrt(Math.max(4, allowance) / 2));
  surfaceDetail = {
    rings: Math.max(3, Math.min(12, rings)),
    segments: Math.max(6, Math.min(16, rings * 2)),
    sides: Math.max(4, Math.min(12, Math.round(allowance / 60))),
  };
  return surfaceDetail;
}

/**
 * An ellipsoid surface as triangles: one unit sphere, scaled by its radii and turned onto
 * its own principal axes.
 *
 * The payload is 15 float32 - centre, radii, then a row-major 3x3 of axes - which is what
 * makes it worth having: a round instance that would be a few hundred vertices on the wire
 * is 60 bytes, and the tessellation happens here where the viewer can choose how fine it
 * needs to be.
 */
// How far an ellipsoid's longest radius may stand out from the sphere of the same volume
// before this page refuses to draw it. The writer refuses to store one past this
// (analysis/primitives.py, MAX_ELLIPSOID_STRETCH), and the same number is here because
// geometry already written cannot be un-written: an instance of a few voxels whose
// principal moments collapsed arrives as radii of 1e-7, 9.2 and 39.2 µm - a speck drawn
// 78 µm across in a cell 10 µm wide. One is enough to set the scene's bounding box, and
// then the object it belongs to is a sub-pixel speck beside it. Refusing it drops that one
// instance from the scene, which is the smaller loss by far.
var MAX_ELLIPSOID_STRETCH = 100;

function ellipsoidOf(payload) {
  if (!payload || payload.byteLength < 60) return null;
  var view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  var f = new Float32Array(15);
  for (var i = 0; i < 15; i++) f[i] = view.getFloat32(i * 4, true);
  for (var j = 0; j < 15; j++) { if (!isFinite(f[j])) return null; }
  var rx = f[3], ry = f[4], rz = f[5];
  if (!(Math.min(rx, ry, rz) > 0)) return null;
  var sphere = Math.cbrt(rx * ry * rz);
  if (Math.max(rx, ry, rz) / sphere > MAX_ELLIPSOID_STRETCH) return null;
  return f;
}

function ellipsoidDrawable(payload) {
  // Through ellipsoidOf and not by reading the floats here, so the bound on a collapsed
  // ellipsoid is stated once, beside the constant that gives it.
  var f = ellipsoidOf(payload);
  if (!f) return null;
  var cx = f[0], cy = f[1], cz = f[2];
  var rx = f[3], ry = f[4], rz = f[5];
  var axes = f.slice(6);            // rows: axis for rx, ry, rz
  var rings = surfaceDetail.rings, segs = surfaceDetail.segments;
  var positions = new Float32Array((rings + 1) * (segs + 1) * 3);
  var p = 0;
  for (var ring = 0; ring <= rings; ring++) {
    var phi = Math.PI * ring / rings;
    var sinPhi = Math.sin(phi), cosPhi = Math.cos(phi);
    for (var seg = 0; seg <= segs; seg++) {
      var theta = 2 * Math.PI * seg / segs;
      // A point on the unit sphere, stretched by the radii, then rotated onto the axes.
      var ux = sinPhi * Math.cos(theta) * rx;
      var uy = sinPhi * Math.sin(theta) * ry;
      var uz = cosPhi * rz;
      positions[p++] = cx + axes[0] * ux + axes[3] * uy + axes[6] * uz;
      positions[p++] = cy + axes[1] * ux + axes[4] * uy + axes[7] * uz;
      positions[p++] = cz + axes[2] * ux + axes[5] * uy + axes[8] * uz;
    }
  }
  var indices = new Uint32Array(rings * segs * 6);
  var k = 0;
  for (var ring2 = 0; ring2 < rings; ring2++) {
    for (var seg2 = 0; seg2 < segs; seg2++) {
      var a = ring2 * (segs + 1) + seg2, b = a + segs + 1;
      indices[k++] = a; indices[k++] = b; indices[k++] = a + 1;
      indices[k++] = b; indices[k++] = b + 1; indices[k++] = a + 1;
    }
  }
  return { positions: positions, indices: indices };
}

// Sides of the ring swept along a tube's centre line, and the resolution of the sphere put
// at each junction. Rendering choices, like ELLIPSOID_SEGMENTS: the stored surface is a
// polyline and this is how round it is drawn.
var JOINT_RINGS = 6;

var JOINT_SEGMENTS = 8;

/**
 * A rotation-minimising frame along a polyline, by double reflection.
 *
 * Computing a frame per segment from a fixed up-vector - which is what this did first -
 * gives each ring its own rotation, so consecutive rings are turned against each other and
 * the tube visibly twists and shears. Propagating one frame instead is the standard fix
 * (Wang et al. 2008): reflect the previous frame onto the next tangent twice, which is the
 * least rotation that keeps it perpendicular.
 */
function minimisingFrames(points, tangents) {
  var n = points.length / 3;
  var normals = new Float32Array(n * 3);
  // Any unit vector perpendicular to the first tangent will do to start.
  var tx = tangents[0], ty = tangents[1], tz = tangents[2];
  var ax = Math.abs(tx) < 0.9 ? 1 : 0, ay = Math.abs(tx) < 0.9 ? 0 : 1, az = 0;
  var rx = ay * tz - az * ty, ry = az * tx - ax * tz, rz = ax * ty - ay * tx;
  var rn = Math.hypot(rx, ry, rz) || 1;
  rx /= rn; ry /= rn; rz /= rn;
  normals[0] = rx; normals[1] = ry; normals[2] = rz;
  for (var i = 0; i < n - 1; i++) {
    var vx = points[(i + 1) * 3] - points[i * 3];
    var vy = points[(i + 1) * 3 + 1] - points[i * 3 + 1];
    var vz = points[(i + 1) * 3 + 2] - points[i * 3 + 2];
    var c1 = vx * vx + vy * vy + vz * vz;
    var t0x = tangents[i * 3], t0y = tangents[i * 3 + 1], t0z = tangents[i * 3 + 2];
    var t1x = tangents[(i + 1) * 3], t1y = tangents[(i + 1) * 3 + 1], t1z = tangents[(i + 1) * 3 + 2];
    var lx = rx, ly = ry, lz = rz, mx = t0x, my = t0y, mz = t0z;
    if (c1 > 0) {
      var d1 = 2 / c1 * (vx * rx + vy * ry + vz * rz);
      lx = rx - d1 * vx; ly = ry - d1 * vy; lz = rz - d1 * vz;
      var d2 = 2 / c1 * (vx * t0x + vy * t0y + vz * t0z);
      mx = t0x - d2 * vx; my = t0y - d2 * vy; mz = t0z - d2 * vz;
    }
    var wx = t1x - mx, wy = t1y - my, wz = t1z - mz;
    var c2 = wx * wx + wy * wy + wz * wz;
    if (c2 > 0) {
      var d3 = 2 / c2 * (wx * lx + wy * ly + wz * lz);
      rx = lx - d3 * wx; ry = ly - d3 * wy; rz = lz - d3 * wz;
    } else { rx = lx; ry = ly; rz = lz; }
    var m = Math.hypot(rx, ry, rz) || 1;
    rx /= m; ry /= m; rz /= m;
    normals[(i + 1) * 3] = rx; normals[(i + 1) * 3 + 1] = ry; normals[(i + 1) * 3 + 2] = rz;
  }
  return normals;
}

/** Unit tangents along a polyline, averaged at the interior nodes so rings meet squarely. */
function polylineTangents(points) {
  var n = points.length / 3;
  var tangents = new Float32Array(n * 3);
  for (var i = 0; i < n; i++) {
    var a = Math.max(0, i - 1), b = Math.min(n - 1, i + 1);
    var dx = points[b * 3] - points[a * 3];
    var dy = points[b * 3 + 1] - points[a * 3 + 1];
    var dz = points[b * 3 + 2] - points[a * 3 + 2];
    var len = Math.hypot(dx, dy, dz);
    if (!(len > 0)) { dx = 0; dy = 0; dz = 1; len = 1; }
    tangents[i * 3] = dx / len; tangents[i * 3 + 1] = dy / len; tangents[i * 3 + 2] = dz / len;
  }
  return tangents;
}

/** The polylines of a skeleton graph: each runs between nodes that are not simple joins. */
function skeletonPaths(nIndices, indices, nVerts) {
  var neighbours = [];
  for (var i = 0; i < nVerts; i++) neighbours.push([]);
  for (var e = 0; e < nIndices; e++) {
    var a = indices[e * 2], b = indices[e * 2 + 1];
    if (a === b) continue;
    neighbours[a].push(b);
    neighbours[b].push(a);
  }
  var seen = {}, paths = [];
  function walk(start, next) {
    var path = [start], prev = start, node = next;
    for (;;) {
      path.push(node);
      if (neighbours[node].length !== 2) break;
      var onward = neighbours[node][0] === prev ? neighbours[node][1] : neighbours[node][0];
      prev = node; node = onward;
      if (node === start) { path.push(node); break; }   // a closed loop
    }
    return path;
  }
  for (var v = 0; v < nVerts; v++) {
    if (neighbours[v].length === 2) continue;           // start only at ends and junctions
    for (var k = 0; k < neighbours[v].length; k++) {
      var key = v < neighbours[v][k] ? v + ':' + neighbours[v][k]
                                     : neighbours[v][k] + ':' + v;
      if (seen[key]) continue;
      seen[key] = 1;
      var path = walk(v, neighbours[v][k]);
      for (var j = 0; j + 1 < path.length; j++) {
        var a2 = path[j], b2 = path[j + 1];
        seen[a2 < b2 ? a2 + ':' + b2 : b2 + ':' + a2] = 1;
      }
      paths.push(path);
    }
  }
  // Anything left is a ring of simple joins with no end to start from.
  for (var v2 = 0; v2 < nVerts; v2++) {
    if (neighbours[v2].length !== 2) continue;
    var key2 = v2 < neighbours[v2][0] ? v2 + ':' + neighbours[v2][0]
                                      : neighbours[v2][0] + ':' + v2;
    if (seen[key2]) continue;
    var loop = walk(v2, neighbours[v2][0]);
    for (var j2 = 0; j2 + 1 < loop.length; j2++) {
      var a3 = loop[j2], b3 = loop[j2 + 1];
      seen[a3 < b3 ? a3 + ':' + b3 : b3 + ':' + a3] = 1;
    }
    paths.push(loop);
  }
  return { paths: paths, neighbours: neighbours };
}

/**
 * A tube surface: the instance's own centre line, swept by its own radius.
 *
 * The payload is a skeleton - quantised vertices and edge pairs - with one float32 radius
 * per vertex on the end, which kimimaro measured while skeletonising. A filament is a
 * polyline and not a deformed cylinder because a polyline can differ in length,
 * curvature and how many branches it has, none of which a fixed base mesh can become.
 *
 * One ring per node, shared by the segments either side of it, carried along a
 * rotation-minimising frame; and a sphere at every node that is not a simple join, which is
 * how a branch or an end is closed without inventing a ring that fits both arms. Emitting
 * an unstitched sleeve per segment instead - which is what this did first - left a visible
 * seam at every node and drew twice the vertices to do it.
 */
function tubeDrawable(payload) {
  var line = decodePayload(payload, 2);
  if (!line) return null;
  var nV = line.positions.length / 3;
  var radiiOffset = payload.byteLength - nV * 4;
  if (radiiOffset < 0) return null;
  var view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  var radii = new Float32Array(nV);
  for (var i = 0; i < nV; i++) radii[i] = view.getFloat32(radiiOffset + i * 4, true);

  var graph = skeletonPaths(line.indices.length / 2, line.indices, nV);
  var positions = [], indices = [], sides = surfaceDetail.sides;

  graph.paths.forEach(function(path) {
    if (path.length < 2) return;
    var pts = new Float32Array(path.length * 3);
    for (var i = 0; i < path.length; i++) {
      pts[i * 3] = line.positions[path[i] * 3];
      pts[i * 3 + 1] = line.positions[path[i] * 3 + 1];
      pts[i * 3 + 2] = line.positions[path[i] * 3 + 2];
    }
    var tangents = polylineTangents(pts);
    var normals = minimisingFrames(pts, tangents);
    var base = positions.length / 3;
    for (var n2 = 0; n2 < path.length; n2++) {
      var tx = tangents[n2 * 3], ty = tangents[n2 * 3 + 1], tz = tangents[n2 * 3 + 2];
      var ux = normals[n2 * 3], uy = normals[n2 * 3 + 1], uz = normals[n2 * 3 + 2];
      var vx = ty * uz - tz * uy, vy = tz * ux - tx * uz, vz = tx * uy - ty * ux;
      var r = radii[path[n2]];
      for (var s2 = 0; s2 < sides; s2++) {
        var a2 = 2 * Math.PI * s2 / sides, ca = Math.cos(a2) * r, sa = Math.sin(a2) * r;
        positions.push(pts[n2 * 3] + ux * ca + vx * sa,
                       pts[n2 * 3 + 1] + uy * ca + vy * sa,
                       pts[n2 * 3 + 2] + uz * ca + vz * sa);
      }
    }
    for (var seg = 0; seg + 1 < path.length; seg++) {
      for (var s3 = 0; s3 < sides; s3++) {
        var nxt = (s3 + 1) % sides;
        var i0 = base + seg * sides + s3, i1 = base + seg * sides + nxt;
        var j0 = i0 + sides, j1 = i1 + sides;
        indices.push(i0, j0, i1, j0, j1, i1);
      }
    }
  });

  // A sphere wherever the centre line ends or divides: at a junction there is no one ring
  // that fits every arm, and overlapping a sphere reads as a solid joint from any angle.
  for (var v = 0; v < nV; v++) {
    var degree = graph.neighbours[v].length;
    if (degree === 2 || degree === 0) continue;
    var r2 = radii[v], cx = line.positions[v * 3];
    var cy = line.positions[v * 3 + 1], cz = line.positions[v * 3 + 2];
    var b2 = positions.length / 3;
    for (var ring = 0; ring <= JOINT_RINGS; ring++) {
      var phi = Math.PI * ring / JOINT_RINGS;
      for (var sg = 0; sg <= JOINT_SEGMENTS; sg++) {
        var th = 2 * Math.PI * sg / JOINT_SEGMENTS;
        positions.push(cx + r2 * Math.sin(phi) * Math.cos(th),
                       cy + r2 * Math.sin(phi) * Math.sin(th),
                       cz + r2 * Math.cos(phi));
      }
    }
    for (var ring2 = 0; ring2 < JOINT_RINGS; ring2++) {
      for (var sg2 = 0; sg2 < JOINT_SEGMENTS; sg2++) {
        var p0 = b2 + ring2 * (JOINT_SEGMENTS + 1) + sg2, p1 = p0 + JOINT_SEGMENTS + 1;
        indices.push(p0, p1, p0 + 1, p1, p1 + 1, p0 + 1);
      }
    }
  }

  if (!indices.length) return null;
  return { positions: new Float32Array(positions), indices: new Uint32Array(indices) };
}

// How a stored surface becomes triangles, one entry per kind the writer can emit. A test
// checks these keys against the writer's own list, so a kind nothing here can draw fails
// the suite instead of quietly vanishing in a browser.
var SURFACE_BUILDERS = {
  mesh: function(payload) { return decodePayload(payload, 3); },
  ellipsoid: ellipsoidDrawable,
  tube: tubeDrawable,
};

/**
 * One row's drawable geometry: a surface for a volume, closed outline loops for a plane.
 * Same container - positions and indices - so the caller only needs `flat` to decide
 * between a Mesh and LineSegments, whichever kind the surface was stored as.
 */
function decodeDrawable(row) {
  if (row.surface) {
    var build = SURFACE_BUILDERS[row.surface_kind || 'mesh'] || SURFACE_BUILDERS.mesh;
    var surface = build(row.surface);
    if (surface) return { geometry: surface, flat: false };
  }
  if (row.outline) {
    var outline = decodePayload(row.outline, 2);
    if (outline) return { geometry: outline, flat: true };
  }
  return null;
}

/**
 * Union-find over one object's contact edges: which instances chain together at this gap.
 * Every drawn instance is seeded as its own group, so something touching nothing is a
 * singleton and never absent — the same rule the contacts section counts by.
 */
function contactGroups(instances, edges) {
  var parent = new Map();
  var find = function(key) {
    while (parent.get(key) !== key) {
      parent.set(key, parent.get(parent.get(key)));
      key = parent.get(key);
    }
    return key;
  };
  var union = function(a, b) {
    var ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };
  for (const key of instances) parent.set(key, key);
  for (const edge of edges) {
    if (parent.has(edge.a) && parent.has(edge.b)) union(edge.a, edge.b);
  }
  var members = new Map();
  for (const key of instances) {
    var root = find(key);
    members.set(root, (members.get(root) || 0) + 1);
  }
  return { rootOf: function(key){ return find(key); },
           sizeOfRoot: function(root){ return members.get(root) || 1; } };
}

/**
 * Where an instance goes when the explode slider is up: out along its own direction from the
 * object centre, by its own distance from it, so a crowded interior opens up without anything
 * crossing anything else.
 *
 * `polar.n` is read as [x, y, z] from polar_nx/ny/nz, which is the order the meshes are in,
 * so nothing is reordered here. Reordering it to ZYX mirrored the offset across x and z and
 * sent every instance somewhere it had never been.
 */
function explodeOffset(polar, factor) {
  var dist = polar && polar.dist;
  if (!factor || !dist) return [0, 0, 0];
  var n = polar.n;
  return [n[0] * dist * factor, n[1] * dist * factor, n[2] * dist * factor];
}

/**
 * Smooth vertex normals of one indexed triangle mesh: each face's normal, area-weighted,
 * summed onto its three vertices. What three's computeVertexNormals does, done once per
 * instance and kept on it - merging is repeated on every recolour, and recomputing the
 * normals of millions of vertices each time was most of what a recolour cost.
 */
function vertexNormals(positions, indices) {
  var normals = new Float32Array(positions.length);
  for (var t = 0; t + 2 < indices.length; t += 3) {
    var a = 3 * indices[t], b = 3 * indices[t + 1], c = 3 * indices[t + 2];
    var ux = positions[b] - positions[a], uy = positions[b + 1] - positions[a + 1],
        uz = positions[b + 2] - positions[a + 2];
    var vx = positions[c] - positions[a], vy = positions[c + 1] - positions[a + 1],
        vz = positions[c + 2] - positions[a + 2];
    var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    normals[a] += nx; normals[a + 1] += ny; normals[a + 2] += nz;
    normals[b] += nx; normals[b + 1] += ny; normals[b + 2] += nz;
    normals[c] += nx; normals[c + 1] += ny; normals[c + 2] += nz;
  }
  for (var i = 0; i < normals.length; i += 3) {
    var length = Math.hypot(normals[i], normals[i + 1], normals[i + 2]);
    if (length > 0) { normals[i] /= length; normals[i + 1] /= length; normals[i + 2] /= length; }
  }
  return normals;
}

/**
 * Merge decoded instances into one geometry with per-vertex colours: one draw call for the
 * whole object, however it is coloured, because the colour rides on the vertices.
 */
function mergeInstances(THREE, items) {
  var vertexCount = 0, indexCount = 0;
  items.forEach(function(item) {
    vertexCount += item.geometry.positions.length / 3;
    indexCount += item.geometry.indices.length;
  });
  if (!vertexCount) return null;

  var positions = new Float32Array(vertexCount * 3);
  var colours = new Float32Array(vertexCount * 3);
  var normals = new Float32Array(vertexCount * 3);
  var indices = vertexCount > 65535 ? new Uint32Array(indexCount) : new Uint16Array(indexCount);
  var vertexOffset = 0, indexOffset = 0;
  items.forEach(function(item) {
    var src = item.geometry.positions, idx = item.geometry.indices;
    var rgb = item.rgb, offset = item.offset || [0, 0, 0];
    // Lines have no surface to shade; a mesh's normals ride on the geometry it was decoded to.
    if (!item.flat && idx.length % 3 === 0) {
      normals.set(item.geometry.normals || (item.geometry.normals = vertexNormals(src, idx)),
                  vertexOffset * 3);
    }
    for (var i = 0; i < src.length; i += 3) {
      var at = vertexOffset * 3 + i;
      positions[at]     = src[i] + offset[0];
      positions[at + 1] = src[i + 1] + offset[1];
      positions[at + 2] = src[i + 2] + offset[2];
      colours[at] = rgb[0]; colours[at + 1] = rgb[1]; colours[at + 2] = rgb[2];
    }
    for (var j = 0; j < idx.length; j++) indices[indexOffset + j] = idx[j] + vertexOffset;
    vertexOffset += src.length / 3;
    indexOffset += idx.length;
  });

  var geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  return geometry;
}
