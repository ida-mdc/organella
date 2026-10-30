/** Objects the report says have geometry, whether or not it has been handed over. */
function objectsClaimingGeometry() {
  return OBJECTS.filter(function(r){ return r.mesh_geometry_file != null; });
}

/** Every file in a dropped/chosen directory tree that is an object's geometry. */
function geometryFilesIn(fileList, named) {
  return [...fileList].filter(function(f) {
    // A folder arrives as a whole tree, so the geometry has to be picked out of it by name.
    // Files someone chose or dropped are taken as meant, whatever they are called - the
    // object each one holds is read out of the file itself either way.
    return named ? /\.parquet$/i.test(f.name)
                 : /(^|\/)geometry\.parquet$/.test(f.webkitRelativePath || f.name);
  });
}

/**
 * Register a directory of geometry files, and ask each which object it holds.
 *
 * The folder name beside a geometry.parquet is the object id, but the id is a column of
 * the file too, so it is read from there: a renamed folder then still lines up with the
 * report instead of quietly dropping out of the object selector.
 */
async function useGeometryFiles(fileList, named) {
  var files = geometryFilesIn(fileList, named);
  if (!files.length) {
    throw new Error('No geometry found there. Pick the report_meshes directory a '
      + '--with-mesh run wrote (it holds one folder per object), or the '
      + 'geometry.parquet files themselves.');
  }
  var db = await getDuckDB();
  var registered = [];
  for (var i = 0; i < files.length; i++) {
    var name = 'geom_' + i + '_' + Math.random().toString(36).slice(2) + '.parquet';
    await db.registerFileBuffer(name, new Uint8Array(await files[i].arrayBuffer()));
    registered.push(name);
  }
  var owners = await queryRows(geometryOwnersSql(registered));
  var groupOf = {};
  OBJECTS.forEach(function(r){ groupOf[r.object_id] = r.group_id; });
  GEOMETRY = owners.map(function(r) {
    return { id: String(r.id), group: groupOf[String(r.id)], path: String(r.filename) };
  }).sort(function(a, b){ return a.id.localeCompare(b.id); });
  return GEOMETRY;
}

/**
 * Read the geometry over HTTP instead, from `<base>/<object_id>/geometry.parquet`.
 *
 * Resolved against the report's own URL, falling back to the page's when the report was
 * handed over as a file. That is what makes `?geometry=report_meshes` mean "beside the
 * parquet", so a report uploaded with its meshes folder works from any copy of the page.
 * Made absolute here because DuckDB hands the string straight to XMLHttpRequest, which
 * refuses a relative one.
 */
async function useGeometryBase(base) {
  var db = await getDuckDB();
  var against = REPORT_URL || window.location.href;
  var trimmed = new URL(String(base).replace(/\/+$/, '') + '/', against).href;
  GEOMETRY = [];
  for (const object of objectsClaimingGeometry()) {
    var name = 'geom_url_' + object.object_id.replace(/[^A-Za-z0-9_.-]/g, '_') + '.parquet';
    // directIO asks DuckDB to read only what a query touches. duckdb-wasm 1.32.0 ignores
    // it for HTTP and still GETs the whole file - measured: opening eight alpha cells pulls
    // 62 MB of geometry before anything is drawn, with the flag either way. Left on so a
    // version that honours it needs no change here.
    await db.registerFileURL(name,
      trimmed + encodeURIComponent(object.object_id) + '/geometry.parquet',
      _duckdbModule.DuckDBDataProtocol.HTTP, true);
    GEOMETRY.push({ id: object.object_id, group: object.group_id, path: name });
  }
  return GEOMETRY;
}

/** `<name>_meshes/` beside a report URL: what `process --with-mesh` writes next to it. */
function defaultGeometryBase(reportUrl) {
  var name = reportUrl.split('/').pop().split('?')[0].split('#')[0];
  var stem = name.replace(/\.parquet$/i, '');
  return stem ? stem + '_meshes' : null;
}

function hasGeometry() { return GEOMETRY.length > 0; }

/** The geometry of these objects as one queryable source. */
function sourceOf(objects) {
  return 'read_parquet([' + objects.map(function(o){ return esc(o.path); }).join(', ') + '])';
}

/**
 * What to tell the reader when the geometry will not read.
 *
 * Two of DuckDB's errors are one situation - geometry written before the surface kinds,
 * which stored a `mesh` where this page reads a `surface` - and neither says so. Whether
 * every file is that old or only one of them is the difference between the two, and
 * between the two things to do about it:
 *
 *   all of them   the folder predates the surface kinds; mesh the batch again
 *   one of them   a mixed read, which on a served report is nearly always the browser
 *                 answering out of its cache: `organella view` serves every report on
 *                 the same port at the same paths, so one object's geometry.parquet has
 *                 the same URL whichever run wrote it
 *
 * Anything else is passed through: a message nobody wrote is better than a wrong guess.
 */
function geometryProblem(error) {
  var message = error && error.message ? error.message : String(error);
  var first = String(message).split('\n')[0];
  if (/schema mismatch in glob/.test(message)) {
    return 'These geometry files were not all written by the same run: one of them has no '
      + '"surface" column, which is what a run before the surface kinds wrote. If the '
      + 'report is being served, reload past the cache first (Ctrl-Shift-R) - the same URL '
      + 'serves every run\u2019s geometry for an object. If it survives that, mesh the '
      + 'batch again so every object comes from one run. (' + first + ')';
  }
  if (/Referenced column "surface" not found/.test(message)) {
    return 'This geometry was written before the surface kinds, so it holds a mesh where '
      + 'this page reads a surface. Mesh the batch again: organella mesh <report> '
      + '(or process --with-mesh). (' + first + ')';
  }
  return message;
}

// A row has a surface (a volume) or an outline (a plane); a mixed batch has both kinds.
// The surface may be a mesh, an ellipsoid or a tube - surface_kind says which, and
// SURFACE_BUILDERS turns each into the same positions-and-indices the scene merges.
var HAS_GEOMETRY_SQL = '("surface" IS NOT NULL OR "outline" IS NOT NULL)';

// The size to rank by, whichever the object has. Both columns exist in every geometry file.
var GEOMETRY_SIZE_SQL = 'COALESCE("volume_um3", "area_um2")';

// ── the queries the geometry sections build ───────────────────────────────────
//
// Lifted out of the sections that run them so they can be checked against a real
// geometry.parquet (tests/report_page_check.mjs prints them, and the Python suite runs
// every one through DuckDB). A binder error is then a failing test, not a message
// in somebody's browser console.

/** Which object each registered geometry file holds. */
function geometryOwnersSql(files) {
  return 'SELECT DISTINCT "object_id" AS id, filename FROM read_parquet(['
    + files.map(esc).join(', ') + '], filename = true)';
}

/** What is in an object's geometry file, and how big: the cheap query. */
function sceneSummarySql(source) {
  return 'SELECT "entity_name" AS name, "row_type" AS row_type, COUNT(*) AS n,'
    + ' SUM("surface_elements") AS faces'
    + ' FROM ' + source + ' WHERE ' + HAS_GEOMETRY_SQL
    + ' GROUP BY 1, 2 ORDER BY 1';
}

/** The widest gap any pair in this object was recorded at, which sizes the gap slider. */
function sceneGapSql(source) {
  return 'SELECT MAX("gap_um") AS widest FROM ' + source + " WHERE \"row_type\" = 'contact'";
}

/**
 * The geometry to draw: the largest instances of the chosen structures, bounded, with the
 * columns the colouring and the explode offset need beside the payload.
 */
function sceneGeometrySql(source, names, metricCols) {
  return 'SELECT "entity_name" AS entity, "row_type" AS row_type, "label_id" AS label, '
    + metricCols.map(function(m){ return '"' + m + '"'; }).join(', ')
    + ', "surface" AS surface, "surface_kind" AS surface_kind, "outline" AS outline'
    + ' FROM ' + source
    + ' WHERE ' + HAS_GEOMETRY_SQL
    + ' AND "entity_name" IN (' + names.map(esc).join(', ') + ')'
    // Biggest first: nothing is left out any more, but the order still decides what a
    // reader sees first while the rest is still being built.
    + ' ORDER BY ' + GEOMETRY_SIZE_SQL + ' DESC NULLS LAST';
}

/** The contact edges at one gap, which is what "colour by contact group" unions over. */
function sceneEdgesSql(source, gap) {
  return 'SELECT "entity_a" AS ea, "label_a" AS la, "entity_b" AS eb, "label_b" AS lb'
    + ' FROM ' + source
    + " WHERE \"row_type\" = 'contact' AND \"gap_um\" <= " + Number(gap);
}

/**
 * One structure's drawable shapes that have the metric the gallery is sorting by.
 *
 * `rowType` picks which shapes: the label instances of the structure, or - for a structure
 * segmented as one piece - the whole-structure masks, one per object.
 */
function galleryWhereSql(entity, metric, rowType) {
  return ' WHERE "row_type" = ' + esc(rowType || 'instance') + ' AND ' + HAS_GEOMETRY_SQL
    + ' AND "entity_name" = ' + esc(entity) + ' AND isfinite("' + metric + '")';
}

/** How many there are in total, which is what the caption is a fraction of. */
function galleryCountSql(source, entity, metric, rowType) {
  return 'SELECT COUNT(*) AS n FROM ' + source + galleryWhereSql(entity, metric, rowType);
}

/**
 * The instances to draw: the tails of a metric, or a fair sample of it.
 *
 * "random" picks the sample first and sorts it afterwards, so the grid reads in metric
 * order either way — the tails are a different question from a fair sample.
 */
function gallerySql(source, entity, metric, order, limit, rowType) {
  var pick = order === 'random'
    ? 'ORDER BY random() LIMIT ' + limit
    : 'ORDER BY "' + metric + '"' + (order === 'lowest' ? ' ASC' : ' DESC') + ' LIMIT ' + limit;
  return 'SELECT * FROM (SELECT "object_id" AS object_id, "label_id" AS label, "'
    + metric + '" AS value, "surface" AS surface, "surface_kind" AS surface_kind,'
    + ' "outline" AS outline, "skeleton" AS skeleton'
    + ' FROM ' + source + galleryWhereSql(entity, metric, rowType) + ' ' + pick
    + ') ORDER BY value DESC';
}
