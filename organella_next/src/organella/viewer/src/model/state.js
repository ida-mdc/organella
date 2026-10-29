var OBJECTS = [];

      // one per object folder (row_type='object')
var ENTS = [];

         // one per object x structure (row_type='entity')
var INSTANCES = [];

    // one per instance, distances widened onto it
var CONTACTS = [];

     // one per touching pair (row_type='contact')
// object -> target -> the same distance from everywhere in the object (row_type='baseline'):
// the chance distribution every measured distance is read against. Empty for a report
// written before it was measured, and then the panels simply carry no reference line.
var CHANCE = {};

var COLS = [];

         // the columns INSTANCES actually carry
var DIST_TARGETS = [];

 // structures instances were measured to
var PLANAR = false;

    // 2D objects: area and perimeter, not volume and surface
var MIXED_DIMS = false;

var COLUMN_HELP = {};

// What the run called it, if it said. Applied once the rows say whether anything bounds it.
var REPORT_NOUN = null;

// Rows the report held that name no structure, and so could not be placed. Reported,
// never quietly dropped: a report that loses rows should say so on its own face.
var DROPPED = 0;

// What one measured thing is called here. A run can say (--object-noun cell); otherwise it
// depends on whether anything bounds it, which is not a preference but a fact about the
// data: with a bounding mask there is a body with a centroid and an extent, and without one
// there is only a source image with labels lying in it. Calling the second an "object"
// promises a thing that is not there.
var NOUN = { one: 'object', many: 'objects', bounded: false };

/** The noun, capitalised for the start of a sentence or a heading. */
function Noun(plural) {
  var word = plural ? NOUN.many : NOUN.one;
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function noun(plural) { return plural ? NOUN.many : NOUN.one; }

/**
 * More than one of something whose name came out of a folder.
 *
 * A structure called `granules` is already plural, and "% of granuless" is what a bare +'s'
 * gives you. Not a general pluraliser - it only declines to double a trailing s, which is
 * the case that actually turns up.
 */
function pluralise(word) {
  var text = String(word);
  return /s$/i.test(text) ? text : text + 's';
}

/** "a cell", "an object" - the article has to follow a word this page did not choose. */
function aNoun() {
  return ('aeiou'.indexOf(NOUN.one.charAt(0).toLowerCase()) >= 0 ? 'an ' : 'a ') + NOUN.one;
}

/**
 * Settle on the word, from what the run said and what the report turned out to hold.
 *
 * `singular/plural` where the plural is irregular, because guessing one from English
 * spelling is a losing game and the run already knew the answer.
 */
function settleNoun(given, bounded) {
  var fallback = bounded ? 'object' : 'dataset';
  var raw = (given || fallback).trim();
  var slash = raw.indexOf('/');
  var one = slash < 0 ? raw : raw.slice(0, slash);
  var many = slash < 0 ? pluralise(one) : raw.slice(slash + 1);
  NOUN = { one: one, many: many, bounded: bounded };
}

// `only` is one facet value to narrow to, read against whatever plotGroupBy is: a group
// when grouping by group, an object when grouping by object. Empty means all of them.
var st = { only: '', statsEntity: null, polarRef: null, polarRadius: 'centre' };

// Settled against the batch in initFilters, not fixed here: by group wherever there is
// more than one, by object where there is not.
var plotGroupBy = 'object';

// How a population is drawn, everywhere one is: as a distribution (a box per facet, or a
// bar where there is one value per facet) or as a histogram (a binned shape per facet, on
// one shared grid). A box says where the middle and the spread are; a histogram says what
// the shape is, and shows a second mode that a box hides completely. Which reads better
// depends on the structure, so it is one choice for the whole report;
// no panel carries a control of its own.
var plotStyle = 'distribution';

// Mann-Whitney between facets, on or off and nothing else. What it is handed is not the
// reader's choice, though: see pairwiseSignificance - the objects behind a facet, never the
// instances inside them.
var showSignificance = true;

//
// Meshes would multiply the size of a table every chart loads (36 MB for one real
// object against a 1 MB report), so they are written to one geometry.parquet per
// object instead, and the object row carries the path in `mesh_geometry_file`.
//
// That path is absolute and local, which is what DuckDB needs when it runs natively.
// In a browser it means nothing, so geometry arrives one of two ways: the folder is
// handed over (drag it in, or "Add geometry"), or a base URL is given with
// `?geometry=` and the files are read over HTTP. Either way they are registered into
// the same DuckDB the report is read through, so a query joins across both.

var GEOMETRY = [];

     // [{ id, group, path }] — the objects that have geometry
// Where the report itself was read from, absolute, when it came from a URL. The geometry
// sits beside the report, not beside the page, so this is what its base resolves
// against: upload a report and its <name>_meshes folder together and the links hold,
// whichever copy of the page is open.
var REPORT_URL = null;

// What the run said the data is: credits, a citation, a licence. Shown above the first
// section, and empty for a report whose run was not told.
var REPORT_DESCRIPTION = '';

// Grouped once per report: an offset is asked for per object x structure by four rows of
// panels, and each answer walks that structure's instances.
var _POLAR_OFFSETS = {};
