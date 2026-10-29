/**
 * Every row of the report, deep ones carrying only the columns they are read for.
 *
 * The rows come back in one array, in the shape `adapt` reads: which depth a row is at is
 * still its own `row_type`, and a deep row still finds its object by `object_id`.
 */
/**
 * Say what is being read, and let the browser paint it before reading it.
 *
 * Every step below holds the main thread for seconds, so without the yield the line would
 * only appear once there was nothing left to say.
 */
function loadingStep(text) {
  setText('loading-step', text);
  return new Promise(function(resolve) {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(function(){ setTimeout(resolve, 0); });
    } else {
      setTimeout(resolve, 0);
    }
  });
}

async function readReportRows(file, label) {
  var source = ' FROM read_parquet(' + esc(file) + ')';
  // One row first, and the readability check against it: a file this page cannot read has
  // to be named as what it is, not reported as a binder error about the `row_type` column
  // the queries below name. It is also where the column list comes from, so a report
  // written without histograms is not asked for the columns it has not got.
  await loadingStep('opening…');
  var probe = await queryRows('SELECT *' + source + ' LIMIT 1');
  checkReadable(probe, label);
  var present = Object.keys(probe[0]);
  var deep = Object.keys(DEEP_COLUMNS);

  // How many rows of each depth there are, which is both what the reader is told and how a
  // depth this report has none of is skipped without being scanned for.
  var counts = {};
  (await queryRows('SELECT "row_type" AS type, COUNT(*) AS n' + source + ' GROUP BY 1'))
    .forEach(function(r) { counts[r.type] = Number(r.n); });
  var shallow = Object.keys(counts).filter(function(type) {
    return deep.indexOf(type) < 0;
  }).reduce(function(n, type) { return n + counts[type]; }, 0);

  // Everything but the deep rows, whole: an object and an entity row are read across, and
  // an instance row carries whichever `instance_*` columns this report has.
  await loadingStep('reading ' + shallow.toLocaleString() + ' object, structure and instance rows…');
  var rows = await queryRows('SELECT *' + source + ' WHERE "row_type" IS NULL'
    + ' OR "row_type" NOT IN (' + deep.map(esc).join(', ') + ')');
  for (var i = 0; i < deep.length; i++) {
    if (!counts[deep[i]]) continue;
    var wanted = ['row_type'].concat(DEEP_COLUMNS[deep[i]]).filter(function(name) {
      return present.indexOf(name) >= 0;
    });
    await loadingStep('reading ' + counts[deep[i]].toLocaleString() + ' ' + deep[i] + ' rows…');
    rows = rows.concat(await queryRows('SELECT ' + wanted.map(escName).join(', ') + source
      + ' WHERE "row_type" = ' + esc(deep[i])));
  }
  await loadingStep('sorting ' + rows.length.toLocaleString() + ' rows into the report…');
  return rows;
}

async function loadParquetBuffer(buffer, label) {
  var file = await registerReport(buffer);
  var footer = await readFooter(file);
  try {
    COLUMN_HELP = JSON.parse(footer.organella_column_descriptions || '{}') || {};
  } catch (error) {
    COLUMN_HELP = {};
  }
  REPORT_NOUN = footer.organella_object_noun || null;
  REPORT_DESCRIPTION = footer.organella_description || '';
  var rows = await readReportRows(file, label);
  adapt(rows);
  resetLoadState();
  setText('report-source', 'Read from ' + label);
  var described = document.getElementById('report-description');
  renderDescription(described, REPORT_DESCRIPTION);
  described.classList.toggle('d-none', !REPORT_DESCRIPTION);
  await loadingStep('drawing…');
  onLoaded();
}

function resetLoadState() {
  _geomCache = { key: null, summary: null, byEntity: new Map() };
  _framedFor = null;
  _objPal = null; _entPal = null; _grpPal = null;
  st.only = ''; st.statsEntity = null;
  _ctInited = false;
}

function isParquetName(name) {
  return /\.parquet$/i.test(name.split('?')[0].split('#')[0]);
}

function showLoadError(err) {
  // Nothing is loading any more, so how to make one is worth saying again.
  showMakeAReport(true);
  document.getElementById('landing-loading').classList.add('d-none');
  document.getElementById('landing-error').classList.remove('d-none');
  var msg = err && err.message ? err.message : String(err);
  if (msg === 'Failed to fetch') msg = 'Could not reach the server. Check the URL and ensure the server allows cross-origin requests (CORS).';
  setText('landing-error-msg', msg);
}

/**
 * Show or hide how a report is made at all.
 *
 * It is the first thing somebody with no report needs and the last thing somebody opening
 * one wants: a link with `?data=`, or `organella view`, says a report is already on its way,
 * and a column of install instructions beside the spinner is answering a question nobody
 * asked. It comes back if the load fails, because then the question is live again.
 */
function showMakeAReport(show) {
  var commands = document.getElementById('landing-commands');
  if (!commands) return;
  if (show) commands.classList.remove('d-none');
  else commands.classList.add('d-none');
}

/** The "Copy" on the commands card: the page cannot make a report, so what to run is the first thing to take away. */
if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('DOMContentLoaded', function() {
  var button = document.getElementById('copy-cmd');
  if (!button) return;
  button.addEventListener('click', function() {
    var text = document.querySelector('#landing-commands code').innerText;
    var done = function(){ button.textContent = 'Copied'; setTimeout(function(){ button.textContent = 'Copy'; }, 1400); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, done); else done();
  });
});

/** Whether a report is on screen, so that opening another should float over it, not replace it. */
function reportIsOpen() {
  return !document.getElementById('main-content').classList.contains('d-none');
}

function showLoadingOverReport() {
  var landing = document.getElementById('landing');
  landing.classList.add('landing-over');
  landing.classList.remove('d-none');
}

/** Close the card floating over the report, leaving the report as it was. */
function dismissOverReport() {
  var landing = document.getElementById('landing');
  landing.classList.remove('landing-over');
  landing.classList.add('d-none');
  document.getElementById('landing-error').classList.add('d-none');
  document.getElementById('landing-loading').classList.add('d-none');
}

function showLandingForm() {
  if (reportIsOpen()) { dismissOverReport(); return; }
  showMakeAReport(true);
  document.getElementById('landing-form').classList.remove('d-none');
  document.getElementById('landing-loading').classList.add('d-none');
  document.getElementById('topbar').classList.add('d-none');
  document.getElementById('load-btn').classList.add('d-none');
  document.getElementById('topbar-privacy').classList.add('d-none');
  document.getElementById('landing-error').classList.add('d-none');
}

function startLoading(label) {
  if (reportIsOpen()) showLoadingOverReport();
  document.getElementById('landing-form').classList.add('d-none');
  document.getElementById('landing-loading').classList.remove('d-none');
  document.getElementById('landing-error').classList.add('d-none');
  setText('loading-url', label);
}

function loadFile(file) {
  if (!isParquetName(file.name)) {
    if (reportIsOpen()) showLoadingOverReport(); else showLandingForm();
    showLoadError(new Error(file.name + ' is not a .parquet. An Organella report is one file, '
      + 'written by: organella process <folder> -o report.parquet'));
    return;
  }
  startLoading(file.name);
  file.arrayBuffer()
    .then(function(buffer) {
      history.replaceState(null, '', window.location.pathname);
      return loadParquetBuffer(buffer, file.name + ' (' + (file.size/1024).toFixed(0) + ' KB)');
    })
    .catch(showLoadError);
}

function loadFromUrl(url) {
  startLoading(url);
  REPORT_URL = new URL(url, window.location.href).href;
  fetch(url)
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
      return r.arrayBuffer();
    })
    .then(function(data) {
      var params = new URLSearchParams(window.location.search);
      params.set('data', url);
      history.replaceState(null, '', '?' + params);
      // Said only when it came from somewhere else: `organella view` serves the page and the
      // report from one origin, and that is a local file.
      var from = new URL(url, window.location.href);
      return loadParquetBuffer(data, url.split('/').pop()
        + (from.origin === window.location.origin ? '' : ' (' + from.host + ')'));
    })
    .catch(showLoadError);
}

/** Take a handed-over geometry folder, then redraw the two sections that use it. */
function acceptGeometry(fileList, named) {
  setText('geom-info', 'reading geometry…');
  useGeometryFiles(fileList, named)
    .then(function() {
      setText('geom-info', GEOMETRY.length + ' ' + noun(GEOMETRY.length !== 1)
        + ' with geometry');
      renderScene();
      renderEntityStats();
    })
    .catch(function(err) {
      setText('geom-info', '');
      alert(geometryProblem(err));
    });
}

if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('resize', syncTopbarHeight);

function onLoaded() {
  document.getElementById('landing').classList.remove('landing-over');
  document.getElementById('landing').classList.add('d-none');
  document.getElementById('main-content').classList.remove('d-none');
  document.getElementById('topbar').classList.remove('d-none');
  document.getElementById('load-btn').classList.remove('d-none');
  document.getElementById('topbar-privacy').classList.remove('d-none');
  document.getElementById('global-filters').classList.remove('d-none');
  fillNouns();
  syncTopbarHeight();
  var geomBtn = document.getElementById('geom-btn');
  if (objectsClaimingGeometry().length) geomBtn.classList.remove('d-none');
  initFilters();
  renderAll();
  // A base URL means the geometry can be read without anyone choosing a folder, which is
  // what `organella view` sets up. A report read from a URL looks beside itself for the
  // folder `--with-mesh` writes, so sharing the two together needs no parameter at all.
  var base = new URLSearchParams(window.location.search).get('geometry')
    || (REPORT_URL ? defaultGeometryBase(REPORT_URL) : null);
  if (base && objectsClaimingGeometry().length) {
    useGeometryBase(base)
      .then(function() {
        setText('geom-info', GEOMETRY.length + ' ' + noun(GEOMETRY.length !== 1)
        + ' with geometry');
        renderScene();
        renderEntityStats();
      })
      .catch(function(err) { console.warn('organella: geometry base unusable', err); });
  }
}
