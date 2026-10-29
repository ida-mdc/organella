// ── small controls, in the report's own type ──────────────────────────────────
function selector(labelText, values, current, onChange, labels) {
  var wrap = document.createElement('label');
  wrap.append(labelText);
  var select = document.createElement('select');
  select.className = 'form-select form-select-sm';
  select.style.cssText = 'width:auto;font-size:.78rem';
  values.forEach(function(value) {
    var option = document.createElement('option');
    option.value = String(value);
    option.textContent = labels ? (labels.get(value) || String(value)) : String(value);
    if (String(value) === String(current)) option.selected = true;
    select.appendChild(option);
  });
  select.addEventListener('change', function(){ onChange(select.value); });
  wrap.appendChild(select);
  return wrap;
}

function slider(labelText, spec, onCommit) {
  var wrap = document.createElement('label');
  var text = document.createElement('span');
  var input = document.createElement('input');
  input.type = 'range';
  input.className = 'form-range';
  input.style.width = '130px';
  input.min = spec.min; input.max = spec.max; input.step = spec.step; input.value = spec.value;
  var show = function(v){ text.textContent = labelText + ' ' + spec.format(v); };
  show(Number(spec.value));
  input.addEventListener('input', function() {
    show(Number(input.value));
    if (spec.onInput) spec.onInput(Number(input.value));
  });
  input.addEventListener('change', function(){ onCommit(Number(input.value)); });
  wrap.append(text, input);
  return wrap;
}

function stageCanvas(container, height) {
  var holder = document.createElement('div');
  holder.className = 'stage-holder';
  holder.style.height = height + 'px';
  var canvas = document.createElement('canvas');
  holder.appendChild(canvas);
  container.appendChild(holder);
  return { holder: holder, canvas: canvas };
}

function busy(holder, text) {
  var overlay = document.createElement('div');
  overlay.className = 'stage-busy';
  overlay.textContent = text;
  holder.appendChild(overlay);
  return function(){ overlay.remove(); };
}

/**
 * What to say when there is no geometry to draw, and how to get some.
 *
 * `clause` is the whole "and X is drawn here" ending, not just a noun: the two callers are
 * about one object and about many instances, and one shared verb was wrong for one of them.
 */
function geometryHint(container, clause) {
  var claiming = objectsClaimingGeometry().length;
  var hint = document.createElement('p');
  hint.className = 'geometry-hint';
  hint.innerHTML = claiming
    ? 'Geometry for ' + claiming + ' ' + noun(claiming !== 1)
      + ' was written beside this report, but a browser '
      + 'cannot reach a path on your disk. Drop the <code>report_meshes</code> folder in, or '
      + 'press <b>Add geometry</b> above, and ' + clause + '.'
    : 'This report was written without geometry, so there is nothing to draw. Re-run with '
      + '<code>--with-mesh</code>, or mesh an existing run with '
      + '<code>organella mesh</code>, and ' + clause + '.';
  container.appendChild(hint);
}

// What is on screen for the object view, kept at module scope so that pinning a chart, letting
// go of it, or changing a slider recolours what is already loaded instead of reading and
// decoding it again: the stage and its canvas, one object's decoded structures by name, which
// object the camera was last framed on, and which call of renderScene is the current one.
var _sceneHolder = null;

var _geomCache = { key: null, summary: null, byEntity: new Map() };

var _framedFor = null;

var _sceneRun = 0;

// The object view's stage, kept at module scope so a re-render hands the same WebGL context
// on, which avoids opening another one the browser will later drop.
var _objectStage = null;

// What the reader last chose, kept across re-renders so changing a filter does not reset the
// view to the first object with structure colouring.
var sceneState = { objectId: null, colourBy: 'entity', metric: null, entities: null,
                   explode: 0, gap: null, maxGap: null,
                   // The chart the viewer is explaining, if any (see pinToViewer), and the
                   // two things a reader can slide to see how the 3D feeds that chart.
                   explain: null, wedgePaused: null, pinnedIndex: 0, threshold: null, wedge: null, t: 0,
                   groupAt: 0, groupPaused: null };
