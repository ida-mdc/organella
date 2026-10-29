function boot() {
  dockChangeListeners.push(buildSubnav, renderGlance);
  document.getElementById('load-btn').addEventListener('click', function() {
    document.getElementById('file-input').click();
  });
  document.getElementById('file-input').addEventListener('change', function(e) {
    if (e.target.files[0]) loadFile(e.target.files[0]);
  });
  document.getElementById('geom-pick-folder').addEventListener('click', function() {
    document.getElementById('geom-input').click();
  });
  document.getElementById('geom-pick-files').addEventListener('click', function() {
    document.getElementById('geom-files-input').click();
  });
  document.getElementById('geom-input').addEventListener('change', function(e) {
    if (e.target.files.length) acceptGeometry(e.target.files);
  });
  // Named files, so whatever was chosen is taken at its word.
  document.getElementById('geom-files-input').addEventListener('change', function(e) {
    if (e.target.files.length) acceptGeometry(e.target.files, true);
  });

  // One drop target for both: a .parquet is the report, a folder is the geometry beside it.
  var dz = document.getElementById('drop-zone');
  ['dragenter','dragover'].forEach(function(ev) {
    dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.add('drag-over'); });
  });
  ['dragleave','drop'].forEach(function(ev) {
    dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.remove('drag-over'); });
  });
  dz.addEventListener('drop', function(e) {
    if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]);
  });
  document.body.addEventListener('dragover', function(e) {
    if (OBJECTS.length) e.preventDefault();
  });
  document.body.addEventListener('drop', function(e) {
    if (!OBJECTS.length) return;
    var files = [...e.dataTransfer.files];
    if (!files.length) return;
    e.preventDefault();
    var report = files.find(function(f){ return isParquetName(f.name) && !/geometry\.parquet$/i.test(f.name); });
    if (report) loadFile(report);
    else acceptGeometry(files, true);
  });

  document.getElementById('url-load-btn').addEventListener('click', function() {
    var url = document.getElementById('url-input').value.trim();
    if (url) loadFromUrl(url);
  });
  document.getElementById('url-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { var url = this.value.trim(); if (url) loadFromUrl(url); }
  });

  // ?data=<url of a report> opens straight into it, which is what a link to a report is.
  var dataUrl = new URLSearchParams(window.location.search).get('data');
  if (dataUrl) {
    showMakeAReport(false);
    document.getElementById('url-input').value = dataUrl;
    loadFromUrl(dataUrl);
  }
}
