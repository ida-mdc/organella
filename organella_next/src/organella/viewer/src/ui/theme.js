// Kept in sync with the :root tokens by hand (Plotly needs real values, not
// var() references). CS.series is the validated categorical order: assign by
// index, never cycle a 9th generated hue — past the 8th, series fold to grey.
var CS = {
  serif: 'Organella Serif, Source Serif 4, Iowan Old Style, Palatino Linotype, Palatino, Book Antiqua, Charter, Source Serif 4, Noto Serif, Georgia, Liberation Serif, serif',
  sans:  'system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif',
  ink: '#12161b', ink2: '#414b55', ink3: '#626d79',
  rule: '#e1e6eb', rule2: '#c2cad3', accent: '#1c5cab', grey: '#9aa4b0',
  series: ['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948']
};

var CS_AXIS = {
  gridcolor: CS.rule, zeroline: false, linecolor: CS.rule2, ticks: 'outside',
  tickcolor: CS.rule2, ticklen: 3, automargin: true,
  tickfont: { family: CS.sans, size: 10.5, color: CS.ink3 },
  title: { font: { family: CS.sans, size: 10.5, color: CS.ink3 } }
};

var CS_TEMPLATE = { layout: {
  font: { family: CS.sans, size: 11, color: CS.ink2 },
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  colorway: CS.series,
  title: { font: { family: CS.sans, size: 12, color: CS.ink }, x: 0, xanchor: 'left',
           xref: 'paper' },
  xaxis: CS_AXIS, yaxis: CS_AXIS,
  legend: { font: { family: CS.sans, size: 10, color: CS.ink2 }, bgcolor: 'rgba(0,0,0,0)' },
  hoverlabel: { bgcolor: '#1b2128', bordercolor: '#1b2128',
                font: { family: CS.sans, size: 11, color: '#ffffff' } },
  boxgap: 0.45, bargap: 0.28
} };

// Every chart inherits the theme: wrapping newPlot beats restating fonts and
// grid colours at a dozen call sites (and drifting between them).
if (typeof Plotly !== 'undefined') {
  var _newPlot = Plotly.newPlot;
  Plotly.newPlot = function(gd, data, layout, config) {
    layout = layout || {};
    if (!layout.template) layout.template = CS_TEMPLATE;
    return _newPlot.call(Plotly, gd, data, layout, config);
  };
}

// Colour caches. Identity colours follow the thing (object/group/structure), never
// its rank, so filtering a series out never repaints the survivors.
var _objPal = null, _entPal = null, _grpPal = null;

// Past the hand-picked series the colours are generated, never repeated or greyed
// out: the hue walks the wheel by the golden angle, so however many are wanted, successive
// ones land far apart. Coming round the wheel they eventually land near a hand-picked hue
// anyway, so the generated ones alternate between a pale and a dark band, either side of
// the mid lightness the hand-picked eight all sit at - a pale salmon next to the palette's
// orange reads as a different series where a second orange would not.
function seriesColour(i) {
  if (i < CS.series.length) return CS.series[i];
  var n = i - CS.series.length;
  var dark = n % 2 === 1;
  return hslToHex(((n * 137.508 + 95) % 360) / 360, dark ? 0.70 : 0.80, dark ? 0.30 : 0.66);
}

function assignSeries(keys) {
  var m = {};
  keys.forEach(function(k, i){ m[k] = seriesColour(i); });
  return m;
}

// Series colours are opaque hex; area fills want the same hue, translucent.
function withAlpha(hex, a) {
  var h = String(hex).replace('#', '');
  if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
  var n = parseInt(h, 16);
  return 'rgba(' + [(n>>16)&255, (n>>8)&255, n&255].join(',') + ',' + a + ')';
}

function objectPalette() {
  if (!_objPal) _objPal = assignSeries(uniq(OBJECTS.map(function(r){ return r.object_id; }).filter(Boolean)));
  return _objPal;
}

var OBJECT_MASK_GREY = '#9a9a9a';

// What a structure is called, to the colour it is usually shown in; first match wins.
var ENT_HINTS = [[/nucle/i, '#8da0cb'], [/mito/i, '#fc8d62'], [/golgi/i, '#e78ac3'], [/granule|vesicle|insulin|secretory/i, '#ffd92f'],
                 [/microtub|tubulin|filament|actin/i, '#66c2a5'], [/centri|basal/i, '#a6d854'], [/cilium|mtoc|axoneme/i, '#e5c494']];

// ColorBrewer's Set2 (matplotlib's "Set2"), a qualitative palette, without its grey.
// Each structure takes the Set2 colour nearest the hue it is usually drawn in; the rest take the next
// unused one in this order.
var ENT_CYCLE = ['#ffd92f', '#66c2a5', '#fc8d62', '#8da0cb', '#a6d854', '#e78ac3', '#e5c494'];

// A structure's colour is the report's own if the run was given one (--colours lands it on
// the entity rows), so a swatch here and a mesh in the 3D view are the same colour.
function entPalette() {
  if (!_entPal) {
    // Curated, not generated: the names a cell is made of get the colour a microscopist expects
    // of them where there is one, and the rest take the next from a list that is meant to sit
    // together on a dark stage. No blue and no cyan anywhere in it - those are the distance ramp's
    // - and none of the two group colours (blue, orange) of the charts.
    _entPal = {};
    var taken = {}, cycle = ENT_CYCLE.slice();
    var names = entityNames();
    names.forEach(function(name) {
      for (var k = 0; k < ENT_HINTS.length; k++) {
        if (ENT_HINTS[k][0].test(name) && !taken[ENT_HINTS[k][1]]) {
          taken[ENT_HINTS[k][1]] = true; _entPal[name] = ENT_HINTS[k][1]; return;
        }
      }
    });
    var next = 0;
    names.forEach(function(name) {
      if (_entPal[name]) return;
      while (next < cycle.length && taken[cycle[next]]) next++;
      _entPal[name] = next < cycle.length ? cycle[next] : seriesColour(names.indexOf(name) + 2);
      taken[_entPal[name]] = true;
    });
    // The mask that bounds the object is the cell's own outline, not one structure among
    // others: a neutral grey, so the colours are left to what is inside it. A colour the
    // run was given for it still wins, below.
    OBJECTS.forEach(function(o) {
      if (o.object_mask_name) _entPal[o.object_mask_name] = OBJECT_MASK_GREY;
    });
    ENTS.forEach(function(r){ if (r.entity_colour) _entPal[r.entity_name] = r.entity_colour; });
  }
  return _entPal;
}

function groupPalette() {
  if (!_grpPal) _grpPal = assignSeries(groupNames());
  return _grpPal;
}

function objectColor(c) { return (objectPalette()[c] || '#aaa'); }

function entColor(e)    { return (entPalette()[e]    || '#aaa'); }

function groupColor(g)  { return (groupPalette()[g]  || '#aaa'); }

function gColor(g) { return plotGroupBy === 'group' ? groupColor(g) : objectColor(g); }

/** A structure's colour, as a lit surface needs it. */
function litEntColour(name) { return litColour(entColor(name)); }
