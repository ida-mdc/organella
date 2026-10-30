// One database for the whole page: the report is read through it, and the geometry
// sidecars are registered into the same instance so the 3D sections can query them.
var _duckdbPromise = null;

var _duckdbModule = null;

function getDuckDB() {
  if (!_duckdbPromise) {
    _duckdbPromise = (async function() {
      var duckdb = await import('https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/+esm');
      _duckdbModule = duckdb;
      var bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
      var worker = await duckdb.createWorker(bundle.mainWorker);
      var db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
      return db;
    })();
  }
  return _duckdbPromise;
}

/** Rows out of a query, with BigInt label ids and counts narrowed to numbers. */
async function queryRows(sql) {
  var db = await getDuckDB();
  var conn = await db.connect();
  try {
    var result = await conn.query(sql);
    return result.toArray().map(function(row) {
      var obj = row.toJSON();
      for (var k in obj) { if (typeof obj[k] === 'bigint') obj[k] = Number(obj[k]); }
      return obj;
    });
  } finally {
    await conn.close();
  }
}

/**
 * What each column means, from the footer of the report being opened.
 *
 * One key holding the whole map as JSON. The per-field metadata the file also
 * carries: DuckDB drops field metadata and `parquet_schema()` does not expose it, so the
 * footer is the only copy a browser can get at.
 */
async function readFooter(file) {
  try {
    var rows = await queryRows(
      "SELECT key, value FROM parquet_kv_metadata(" + esc(file) + ")");
    var out = {};
    rows.forEach(function(row) {
      // DuckDB hands a BLOB back as bytes.
      var read = function(v) {
        return typeof v === 'string' ? v : new TextDecoder().decode(v);
      };
      out[read(row.key)] = read(row.value);
    });
    return out;
  } catch (error) {
    console.warn('organella: could not read the report footer', error);
    return {};
  }
}

var _reportFile = null;

async function registerReport(buffer) {
  var db = await getDuckDB();
  if (_reportFile) await db.dropFile(_reportFile);
  _reportFile = 'report_' + Math.random().toString(36).slice(2) + '.parquet';
  await db.registerFileBuffer(_reportFile, new Uint8Array(buffer));
  return _reportFile;
}

/**
 * What a deep row is read for, so it can be asked for by name.
 *
 * A report is mostly deep rows - one real batch is 677k contacts and 197k distances
 * against 49k instances - and `SELECT *` turns every one of them into an object with a
 * key for each of the report's seventy columns, of which `adapt` reads five or nine. The
 * other sixty-odd are null on those rows: they belong to the depths above. Building them
 * anyway was most of the minute a real report took to open, and most of the two gigabytes
 * it took to hold.
 *
 * Baseline rows are not in here on purpose: there are a handful per object, and they come
 * through the whole-row read with the object and entity rows.
 */
var DEEP_COLUMNS = {
  distance: ['object_id', 'distance_entity', 'distance_label', 'distance_target',
             'distance_um', 'distance_mean_um', 'distance_hist_counts',
             'distance_hist_min_um', 'distance_hist_max_um',
             'distance_end_min_um', 'distance_end_max_um'],
  contact: ['object_id', 'contact_entity', 'contact_label_a', 'contact_label_b',
            'contact_gap_um']
};

/** A column name as SQL, quoted: the names come from the file's own schema. */
function escName(name) { return '"' + String(name).replace(/"/g, '""') + '"'; }
