// ── the colour a lit surface has to start from ────────────────────────────────
//
// A mesh colour is not shown, it is *multiplied by a light*: the intensities sum to about
// one on the brightest face and the ambient term is 0.22, so a face turned away arrives at
// roughly a quarter of the colour it was given. Handed the chart palette raw, a structure
// that reads as a clear blue in a swatch comes out as a muted slate in the 3D view, and the
// object as a whole looks like it is behind glass.
//
// So the hue is kept exactly - a structure is recognisably the same colour as its swatch,
// which is the point of the palette - and the chroma is pushed up, with the lightness
// pulled towards the middle where there is room to shade in both directions without
// clipping to white or crushing to black.

function toHsl(hex) {
  var rgb = toRgb(hex);
  var r = rgb[0], g = rgb[1], b = rgb[2];
  var max = Math.max(r, g, b), min = Math.min(r, g, b);
  var l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l: l };   // grey has no hue to keep
  var d = max - min;
  var s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  var h;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return { h: h, s: s, l: l };
}

function hslToHex(h, s, l) {
  var hue2rgb = function(p, q, t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  var r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    var p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  var byte = function(v) {
    var n = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16);
    return n.length === 1 ? '0' + n : n;
  };
  return '#' + byte(r) + byte(g) + byte(b);
}

var _litCache = {};

/**
 * The same hue, with enough chroma to survive being lit.
 *
 * Grey stays grey: a colour with no hue has none to keep, and saturating it would invent
 * one. Everything else keeps its own lightness ordering - a dark structure stays the darker
 * of two - just compressed into the band that shades well.
 */
function litColour(hex) {
  if (_litCache[hex]) return _litCache[hex];
  // The same colour the charts show, so a structure is one colour wherever it appears. Only a colour
  // the run itself supplied that is too dark to be seen on the stage is lifted, keeping its hue.
  var hsl = toHsl(hex), out = hex;
  if (hsl.l < 0.42) out = hslToHex(hsl.h, hsl.s, 0.42);
  _litCache[hex] = out;
  return out;
}

/** linear → sRGB, per channel: the inverse of toLinear, for showing a colour as a colour. */
function fromLinear(channel) {
  var c = Math.max(0, Math.min(1, channel));
  return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

/**
 * The metric scale at t, as a hex.
 *
 * Through viridisAt and back out of linear and not interpolated again in sRGB, so a
 * legend, a thumbnail and a surface in the 3D scene are the same colours at the same
 * values - which is the whole point of showing the scale.
 */
/** Distances use the same cool_r scale as every other metric: magenta is near, cyan is far. */
function distRampHexAt(t) { return viridisHexAt(t); }

function distRampAt(t) { return viridisAt(t); }

/**
 * What a distance is measured to is always this one colour. It is not on the cool_r scale, so it
 * can never be mistaken for a value, and it does not change with the structure.
 */
var REFERENCE_COLOUR = '#ffb000';

function viridisHexAt(t) {
  return '#' + viridisAt(t).map(function(channel) {
    return Math.round(fromLinear(channel) * 255).toString(16).padStart(2, '0');
  }).join('');
}

/**
 * The legend a metric colouring needs: the scale, and the values at its ends.
 *
 * Colour is the one encoding in this report that cannot be read off the chart itself - a
 * box plot has its axis, a card coloured by its volume has nothing - so anywhere a metric
 * decides a colour, this says which colour was which value.
 */
function colourRamp(opts) {
  var el = document.createElement('div');
  el.className = 'colour-ramp';
  var stops = [];
  var hexAt = opts.ramp === 'distance' ? distRampHexAt : viridisHexAt;
  for (var i = 0; i <= 10; i++) stops.push(hexAt(i / 10) + ' ' + (i * 10) + '%');
  var number = function(v) {
    if (v == null || !isFinite(v)) return '–';
    // Counts are whole numbers: "6.50e+3" is not how anyone reads a group of 6,496.
    return opts.whole ? Math.round(v).toLocaleString() : Number(v).toPrecision(3);
  };
  el.innerHTML = (opts.label ? '<span class="ramp-label">' + escHtml(opts.label) + '</span>' : '')
    + '<span>' + number(opts.low) + '</span>'
    + '<span class="ramp-bar" style="background:linear-gradient(to right,'
    + stops.join(', ') + ')"></span>'
    + '<span>' + number(opts.high) + '</span>';
  el.title = (opts.label || 'metric') + ': ' + number(opts.low) + ' to ' + number(opts.high);
  return el;
}

/** #rrggbb → [r, g, b] in 0..1, the form a vertex-colour attribute wants. */
function toRgb(hex) {
  var value = String(hex).replace('#', '');
  var full = value.length === 3 ? value.split('').map(function(c){ return c + c; }).join('') : value;
  var int = parseInt(full, 16);
  return [((int >> 16) & 255) / 255, ((int >> 8) & 255) / 255, (int & 255) / 255];
}

/**
 * sRGB → linear, per channel: what a *vertex colour* has to be in.
 *
 * three's working space is linear, and it converts linear to sRGB on the way out. A
 * material's `color` is converted for you when it is set from a hex; a plain
 * `BufferAttribute` has nobody to do it, so a hex written in raw is read as if it were
 * already linear and comes back out double-encoded - lighter, flatter, and with the
 * saturation gone, which is precisely the "not poppy" the palette got blamed for. It costs
 * most in the mid-tones, where the two curves are furthest apart: `#1f77b4` rendered as
 * `#62b6db`.
 */
function toLinear(channel) {
  return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
}

/** #rrggbb → linear [r, g, b], ready for a vertex-colour attribute. */
function toLinearRgb(hex) {
  return toRgb(hex).map(toLinear);
}

// matplotlib's cool_r: magenta at the low end to cyan at the high end, sampled in sRGB where it is defined.
var VIRIDIS = ['#ff00ff', '#df20ff', '#bf40ff', '#9f60ff', '#8080ff', '#609fff', '#40bfff', '#20dfff', '#00ffff'];

function viridisAt(t) {
  var clamped = Math.max(0, Math.min(1, isFinite(t) ? t : 0));
  var scaled = clamped * (VIRIDIS.length - 1);
  var i = Math.min(VIRIDIS.length - 2, Math.floor(scaled));
  var f = scaled - i;
  // Interpolated in the space it is written in, which is the linear one.
  var a = toLinearRgb(VIRIDIS[i]), b = toLinearRgb(VIRIDIS[i + 1]);
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
