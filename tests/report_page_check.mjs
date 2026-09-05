// Run the report page's own code outside a browser, so the Python suite can check it.
//
//   node report_page_check.mjs <anatomy_report.html> <job.json>
//
// The page is one standalone HTML file — that is the point of it, since it is opened by
// dropping a report onto it — so there is no module to import. The <script> is lifted out
// and imported as a data: URL instead, which runs exactly the code the browser runs. It
// ends by assigning `globalThis.AnatomyReport`, and everything with a contract worth
// pinning is on there: the shape a report is read into, the maths the panels draw, the
// binary container the meshes are in, and the SQL the geometry sections build.
//
// Nothing here renders: no DOM is provided, and the page skips its own wiring when there
// is none.

import { readFileSync } from 'node:fs';

const [htmlPath, jobPath] = process.argv.slice(2);
const html = readFileSync(htmlPath, 'utf8');
const job = JSON.parse(readFileSync(jobPath, 'utf8'));

// The page's own script block: the last <script> with no src.
const blocks = [...html.matchAll(/<script>\n([\s\S]*?)<\/script>/g)];
if (blocks.length !== 1) {
  throw new Error(`expected exactly one inline <script> in ${htmlPath}, found ${blocks.length}`);
}

// ── a DOM, when a test wants the sections actually drawn ──────────────────────
//
// The page has no compiler and nothing imports it, so a typo in the drawing code only
// shows up in a browser - and a typo in the overview once emptied the whole report while
// reporting it as a failure to *load* the parquet. `render` runs every section against a
// stub of just enough DOM to build elements and hand them to Plotly, so that class of
// mistake is a failing test.
const plotted = [];
if (job.render) {
  // Every id the markup has: getElementById answers for those and returns null for the
  // rest, the way a browser does, so an optional element is exercised as optional.
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  const node = (tag = 'div', id = null) => {
    const self = {
      tagName: tag, id, children: [], _html: '', _text: '',
      style: new Proxy({ cssText: '', setProperty() {} },
                       { set: (t, k, v) => (t[k] = v, true) }),
      classList: {
        add(name) { if (name === 'd-none') self._shown = false; },
        remove(name) { if (name === 'd-none') self._shown = true; },
        contains: () => false,
        toggle() {},
      },
      dataset: {},
      width: 0, height: 0, value: '', checked: false, type: '', className: '', title: '',
      selected: false, min: 0, max: 0, step: 0, name: '', href: '',
      clientWidth: 800, clientHeight: 400, offsetParent: {}, isConnected: true,
      appendChild: (child) => (self.children.push(child), child),
      append: (...kids) => self.children.push(...kids),
      insertBefore: (child) => (self.children.push(child), child),
      removeChild: () => {},
      remove: () => {},
      replaceChildren: () => { self.children.length = 0; },
      insertAdjacentHTML: (_where, markup) => { self._html += markup; },
      addEventListener: () => {},
      removeEventListener: () => {},
      setAttribute: () => {},
      getAttribute: () => null,
      setPointerCapture: () => {},
      releasePointerCapture: () => {},
      // A canvas: the gallery only ever clears and blits, and there is nothing to blit.
      getContext: () => ({ clearRect() {}, drawImage() {} }),
      querySelector: (sel) => self._find(sel),
      querySelectorAll: (sel) => {
        const out = [];
        const walk = (n) => { for (const kid of n.children || []) { out.push(kid); walk(kid); } };
        walk(self);
        return out.filter((n) => n._matches?.(sel));
      },
      _matches: (sel) => {
        const slot = /^\[data-slot="([^"]+)"\]$/.exec(sel);
        if (slot) return self.dataset.slot === slot[1];
        if (sel.startsWith('.')) return String(self.className).split(' ').includes(sel.slice(1));
        return false;
      },
      _find: (sel) => {
        const walk = (n) => {
          for (const kid of n.children || []) {
            if (kid._matches?.(sel)) return kid;
            const deeper = walk(kid);
            if (deeper) return deeper;
          }
          return null;
        };
        return walk(self);
      },
    };
    // innerHTML is how the sections build their blocks; the slots they then look up have
    // to come back, so the data-slot attributes are read out of the markup they set.
    Object.defineProperty(self, 'innerHTML', {
      get: () => self._html,
      set: (markup) => {
        self._html = markup;
        self.children.length = 0;
        for (const m of String(markup).matchAll(/data-slot="([^"]+)"/g)) {
          const slot = node('div');
          slot.dataset.slot = m[1];
          self.children.push(slot);
        }
      },
    });
    Object.defineProperty(self, 'textContent', {
      get: () => self._text, set: (v) => { self._text = String(v); },
    });
    return self;
  };

  const byId = new Map();
  globalThis.document = {
    createElement: (tag) => node(tag),
    getElementById: (id) => {
      if (!ids.has(id)) return null;
      if (!byId.has(id)) byId.set(id, node('div', id));
      return byId.get(id);
    },
    body: node('body'),
  };
  globalThis.window = { location: { search: '', pathname: '/', href: 'http://x/' },
                        addEventListener: () => {}, devicePixelRatio: 1 };
  globalThis.history = { replaceState: () => {} };
  globalThis.getComputedStyle = () => ({ gridTemplateColumns: '1fr 1fr 1fr 1fr' });
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.Plotly = {
    newPlot: (gd, data, layout) => {
      plotted.push({
        traces: (data || []).map((t) => t.type || 'scatter'),
        title: layout?.title?.text ?? null,
        // Enough of a stacked bar to check the arithmetic it draws: what each segment is
        // called and how wide it is, plus what the axis says the whole is.
        barmode: layout?.barmode ?? null,
        series: (data || []).map((t) => ({
          name: t.name ?? null,
          x: Array.isArray(t.x) ? t.x.map(Number) : null,
        })),
        xTitle: (typeof layout?.xaxis?.title === 'string'
          ? layout.xaxis.title : layout?.xaxis?.title?.text) ?? null,
        // A bracket is a path shape plus a stars annotation carrying the p-value.
        brackets: (layout?.annotations ?? [])
          .filter((a) => a.hovertext && a.hovertext.includes('Mann-Whitney'))
          .map((a) => ({ stars: a.text, detail: a.hovertext })),
      });
      return Promise.resolve();
    },
    purge: () => {},
  };
}

await import('data:text/javascript;base64,'
  + Buffer.from(blocks[0][1], 'utf8').toString('base64'));

const R = globalThis.AnatomyReport;
if (!R) throw new Error('the page did not export AnatomyReport');

const out = {};

// The series palette past its hand-picked entries: a report with a dozen structures must
// not run out and start drawing the rest in grey.
out.series = Array.from({ length: 20 }, (_, i) => R.seriesColour(i));

// What each column means, as the page reads it out of the report's own footer. Set before
// anything else, because both the panels and metricHelp() read it.
if (job.columnHelp) R.setColumnHelp(job.columnHelp);
// What the run called one measured thing, likewise; adapt() settles it against the rows.
if (job.objectNoun !== undefined) R.setReportNoun(job.objectNoun);

// ── the report, taken apart ───────────────────────────────────────────────────
if (job.rows) {
  R.adapt(job.rows);
  const s = R.state();
  const instanceRows = s.INSTANCES;
  out.adapt = {
    objects: s.OBJECTS.length,
    ents: s.ENTS.length,
    instances: instanceRows.length,
    contacts: s.CONTACTS.length,
    distTargets: s.DIST_TARGETS,
    cols: s.COLS.slice().sort(),
    planar: s.PLANAR,
    mixedDims: s.MIXED_DIMS,
    groups: [...new Set(s.OBJECTS.map((r) => r.group_id))].sort(),
    // Every deep row must have picked up the group of the object above it: a distance row
    // carries only object_id, and a chart that colours by group reads this.
    instancesWithoutGroup: instanceRows.filter((r) => !r.group_id).length,
    contactsWithoutGroup: s.CONTACTS.filter((r) => !r.group_id).length,
    instanceKinds: [...new Set(instanceRows.map((r) => r.entity_kind))].sort(),
    // How many instances actually got a reading for each target, which is what tells us
    // the long distance rows landed on the right instance.
    distanceCoverage: Object.fromEntries(s.DIST_TARGETS.map((t) => [
      t, instanceRows.filter((r) => r[R.distColumnsFor(t).min] != null).length])),
    histTargets: R.histTargets(instanceRows),
    distCols: R.distCols(instanceRows),
    scatterDistCols: R.scatterDistCols(instanceRows),
    metricCols: R.nonDistMetricCols(instanceRows).sort(),
    orderedDims: R.orderDistDims(R.scatterDistCols(instanceRows)),
    extentTerms: R.extentTerms(),
    geometryMetrics: R.geometryMetrics(),
    contactStructures: R.contactStructures(),
    contactMaxGap: R.contactMaxGap(),
  };

  // ── what the overview tells the reader before anything is pooled ────────────
  out.overview = R.overviewSummary();
  out.overview.colours = Object.fromEntries(
    out.overview.structures.map((name) => [name, R.entColor(name)]));
  // The coverage caveats belong to the sections they qualify, not to the front page.
  out.coverageNotes = Object.fromEntries(
    ['per-instance measurements', 'contacts'].map((label) => [label, R.coverageNote(label)]));

  // ── the clustering the contacts section counts by ───────────────────────────
  if (job.clusters) {
    out.clusters = job.clusters.map((c) => {
      const found = R.contactClusters(c.object, c.entity, c.gap);
      return { object: c.object, entity: c.entity, gap: c.gap,
               instances: found.instances, sizes: found.sizes.slice().sort((a, b) => a - b),
               grouped: found.sizes.reduce((a, b) => a + b, 0) };
    });
  }

  // ── the curves and trends the panels draw ───────────────────────────────────
  const entity = job.structure;
  const rows = instanceRows.filter((r) => r.entity_name === entity);
  const target = out.adapt.distCols[0];
  if (rows.length && target) {
    const vals = rows.map((r) => r[target]).filter((v) => v != null && isFinite(v));
    const ecdf = R.ecdfCurve(vals);
    out.curves = {
      n: ecdf.n,
      // A share curve is monotone, ends at 100%, and starts at the origin whatever n is.
      monotone: ecdf.y.every((v, i) => i === 0 || v >= ecdf.y[i - 1]),
      last: ecdf.y[ecdf.y.length - 1],
      first: [ecdf.x[0], ecdf.y[0]],
      median: ecdf.median,
      vertices: ecdf.x.length,
    };
    const hist = R.histTargets(rows)[0];
    if (hist) {
      const span = R.histSpan(rows, hist);
      const agg = R.aggregateHistograms(rows, hist, 30, span);
      out.histogram = { target: hist, bins: agg.counts.length,
                        total: agg.counts.reduce((a, b) => a + b, 0),
                        // Never finer than the sources it was built from.
                        width: agg.width, srcWidth: span.srcWidth,
                        span: agg.max - agg.min,
                        densitySums: agg.density.reduce((a, b) => a + b, 0) * agg.width };
    }
  }
}

// ── the SQL the geometry sections build ───────────────────────────────────────
if (job.geometry && job.geometry.length) {
  const objects = job.geometry.map((path, i) => ({ id: `object_${i}`, path }));
  const source = R.sourceOf(objects);
  const metric = job.metric || R.geometryMetrics()[0];
  out.statements = [
    R.geometryOwnersSql(job.geometry),
    R.sceneSummarySql(source),
    R.sceneGapSql(source),
    R.sceneGeometrySql(source, job.structures || [job.structure],
                       [metric, 'polar_dist_um', 'polar_nx', 'polar_ny', 'polar_nz'], 500),
    R.sceneEdgesSql(source, 0.1),
    R.galleryCountSql(source, job.structure, metric),
    R.gallerySql(source, job.structure, metric, 'highest', 12),
    R.gallerySql(source, job.structure, metric, 'lowest', 12),
    R.gallerySql(source, job.structure, metric, 'random', 12),
    R.galleryCountSql(source, job.structure, metric, 'file'),
    R.gallerySql(source, job.structure, metric, 'highest', 12, 'file'),
  ];
}

// ── the binary container mesh.py writes ───────────────────────────────────────

/** A payload as it arrives from Arrow: a view into a bigger buffer, at some offset. */
function asArrowView(base64, pad = 3) {
  const bytes = Buffer.from(base64, 'base64');
  // Padded on purpose. A Uint16Array view needs its own alignment and the blob sits at an
  // arbitrary offset inside the batch's memory, so decoding it in place threw; the decoder
  // copies out first, and this is what would catch it going back.
  const holder = Buffer.alloc(bytes.byteLength + pad);
  bytes.copy(holder, pad);
  return { buffer: holder.buffer, byteOffset: holder.byteOffset + pad,
           byteLength: bytes.byteLength };
}

// Enough three.js for mergeInstances, which only ever builds a BufferGeometry and hands it
// three typed arrays. Nothing is rendered; the point is the index arithmetic.
const THREE = {
  BufferAttribute: class { constructor(array, itemSize) { this.array = array; this.itemSize = itemSize; } },
  BufferGeometry: class {
    constructor() { this.attributes = {}; this.index = null; this.normals = false; }
    setAttribute(name, attribute) { this.attributes[name] = attribute; }
    setIndex(attribute) { this.index = attribute; }
    computeVertexNormals() { this.normals = true; }
  },
};

const bboxOf = (positions) => {
  const axis = (k) => [...positions].filter((_, i) => i % 3 === k);
  return Object.fromEntries(['x', 'y', 'z'].map((name, k) => {
    const values = axis(k);
    return [name, { min: Math.min(...values), max: Math.max(...values) }];
  }));
};

if (job.merge) {
  const items = job.merge.items.map((item) => ({
    geometry: R.decodePayload(asArrowView(item.base64), item.perIndex ?? 3),
    rgb: item.rgb,
    offset: item.offset,
  }));
  const merged = R.mergeInstances(THREE, items);
  const positions = merged.attributes.position.array;
  const colours = merged.attributes.color.array;
  out.merge = {
    vertices: positions.length / 3,
    indices: merged.index.array.length,
    maxIndex: merged.index.array.reduce((m, v) => Math.max(m, v), 0),
    indexType: merged.index.array.constructor.name,
    normals: merged.normals,
    bbox: bboxOf(positions),
    colours: { first: [...colours.slice(0, 3)],
               last: [...colours.slice(colours.length - 3)] },
  };
}

if (job.explodes) {
  out.explodes = job.explodes.map(
    (e) => R.explodeOffset({ dist: e.dist, n: e.n }, e.factor));
}

// One stored surface per entry, through the page's own builder for its kind: an ellipsoid
// is 15 floats here and triangles by the time the scene sees it, so what it turns into is
// worth pinning.
out.surfaceKinds = R.surfaceKinds;
out.impostorKinds = R.impostorKinds;

// What a stored surface becomes as impostor instances. The shaders need a GPU; the
// arithmetic that feeds them does not, so that part is checked here.
if (job.impostors) {
  out.impostors = job.impostors.map((d) => {
    const bytes = asArrowView(d.base64);
    if (d.kind === 'ellipsoid') {
      const f = R.ellipsoidOf(bytes);
      return f && { centre: [...f.slice(0, 3)], radii: [...f.slice(3, 6)] };
    }
    const caps = R.capsulesOf(bytes);
    return caps && {
      capsules: caps.n,
      firstA: [...caps.a.slice(0, 3)],
      firstB: [...caps.b.slice(0, 3)],
      radius: caps.r[0],
    };
  });
}

if (job.drawables) {
  out.drawables = job.drawables.map((d) => {
    const got = R.decodeDrawable({ surface: asArrowView(d.base64), surface_kind: d.kind });
    if (!got) return null;
    return {
      vertices: got.geometry.positions.length / 3,
      indices: got.geometry.indices.length,
      flat: got.flat,
      bbox: bboxOf(got.geometry.positions),
    };
  });
}

if (job.payloads) {
  out.payloads = job.payloads.map((p) => {
    const decoded = R.decodePayload(asArrowView(p.base64), p.perIndex);
    if (!decoded) return null;
    return {
      vertices: decoded.positions.length / 3,
      elements: decoded.indices.length / p.perIndex,
      indices: decoded.indices.length,
      maxIndex: decoded.indices.reduce((m, v) => Math.max(m, v), 0),
      bbox: bboxOf(decoded.positions),
    };
  });
}

// ── every section, actually drawn ─────────────────────────────────────────────
if (job.render) {
  // The two global choices a reader has, since they change what every panel draws.
  if (job.plotStyle) R.setPlotStyle(job.plotStyle);
  if (job.groupBy) R.setGroupBy(job.groupBy);
  if (job.significance !== undefined) R.setSignificance(job.significance);
  R.renderAll();
  const failed = [];
  for (const id of ['s-overview', 's-3d', 's-entities', 's-groups', 's-structure']) {
    const section = globalThis.document.getElementById(id);
    const complaint = (section?.children ?? [])
      .find((kid) => String(kid.className).includes('callout-warn')
                     && kid.textContent.startsWith('This section could not be drawn'));
    if (complaint) failed.push({ section: id, error: complaint.textContent });
  }
  out.render = {
    failed,
    // Which sections drew at all: one with nothing to say hides itself.
    shown: ['s-overview', 's-3d', 's-entities', 's-groups', 's-structure']
      .filter((id) => globalThis.document.getElementById(id)?._shown !== false),
    // Every question heading and the sentence under it, so a section that had nothing to
    // draw can be checked for saying why. Taken as the markup each node was given rather
    // than by class: a block builds its headings by setting innerHTML in one go, so in this
    // stub they are that string and not nodes of their own.
    prose: (() => {
      const out = [];
      const walk = (n) => {
        if (n.innerHTML) out.push(n.innerHTML);
        for (const kid of n.children || []) walk(kid);
      };
      const host = globalThis.document.getElementById('entity-stats-container');
      if (host) walk(host);
      return out;
    })(),
    // Where a caveat about what was measured ended up. The overview says what the batch
    // holds; a gap in coverage is said by the section it is a gap in.
    caveats: Object.fromEntries(
      ['overview-warnings', 'entity-coverage', 'ct-coverage'].map(
        (id) => [id, globalThis.document.getElementById(id)?.innerHTML ?? null])),
    plots: plotted.length,
    traceTypes: [...new Set(plotted.flatMap((p) => p.traces))].sort(),
    titles: plotted.map((p) => p.title).filter(Boolean),
    // The brackets, as Plotly was actually asked to draw them.
    brackets: plotted.flatMap((p) => p.brackets),
    bracketedPanels: plotted.filter((p) => p.brackets.length).length,
    boxPanels: plotted.filter((p) => p.traces.includes('box')).length,
    // The composition bar in the overview: one stack per object on one percentage scale.
    stacks: plotted.filter((p) => p.barmode === 'stack')
      .map((p) => ({ xTitle: p.xTitle, series: p.series })),
    // What each panel said it was measuring, from the report's own descriptions.
    helps: (() => {
      const out = [];
      const walk = (n) => {
        for (const kid of n.children || []) {
          if (String(kid.className).includes('metric-help')) out.push(kid.innerHTML);
          walk(kid);
        }
      };
      for (const id of ['entity-stats-container', 'overview-volumes', 'ct-far-charts',
                        'ct-baseline-charts', 'ct-compare-charts']) {
        const host = globalThis.document.getElementById(id);
        if (host) walk(host);
      }
      return out;
    })(),
    // A share curve has no room for a bracket, so the same comparison goes under it as text.
    notes: (() => {
      const out = [];
      const walk = (n) => {
        for (const kid of n.children || []) {
          if (String(kid.className).includes('sig-note')) out.push(kid.innerHTML);
          walk(kid);
        }
      };
      // The hosts the sections append into, not the sections: this DOM stub has no real
      // tree, so a child added to #entity-stats-container is not reachable from #s-entities.
      for (const id of ['entity-stats-container', 'ct-compare-charts', 'ct-far-charts',
                        'ct-both-charts']) {
        const host = globalThis.document.getElementById(id);
        if (host) walk(host);
      }
      return out;
    })(),
  };
}

// ── one query at a time, built by the page ────────────────────────────────────
// `build` is a list of {fn, args}: a test that wants one exact query asks the page to
// build it rather than writing the SQL out again beside it.
if (job.build) {
  out.built = job.build.map(({ fn, args }) => {
    if (typeof R[fn] !== 'function') throw new Error(`the page has no ${fn}()`);
    return R[fn](...args);
  });
}

// ── the gallery's thumbnail budget ────────────────────────────────────────────
if (job.thumbBudget) {
  out.thumbBudget = job.thumbBudget.map(({ columns, rows }) => R.thumbsWanted(columns, rows));
  out.galleryRows = R.GALLERY_ROWS;
  out.maxThumbs = R.MAX_THUMBS;
}

// ── the test behind the brackets ──────────────────────────────────────────────
if (job.mannWhitney) {
  out.mannWhitney = job.mannWhitney.map(({ a, b }) => R.mannWhitney(a, b));
}
if (job.stars) out.stars = job.stars.map((p) => R.significanceStars(p));
if (job.dimPairs) out.dimPairs = job.dimPairs.map((dims) => R.dimPairs(dims));
if (job.dataNames) out.dataNames = job.dataNames.map((n) => R.dataName(n));
if (job.pluralise) out.pluralise = job.pluralise.map((w) => R.pluralise(w));
// What the page says about a parquet it cannot read, which is the only thing the reader
// gets to go on.
if (job.loadFailure !== undefined) {
  try {
    R.checkReadable(job.loadFailure, 'that file');
    out.loadFailure = null;
  } catch (error) {
    out.loadFailure = error.message;
  }
}
if (job.noun !== undefined || job.rows) {
  out.noun = R.nounState();
}
if (job.helpFor) {
  out.helpFor = job.helpFor.map((ask) => (Array.isArray(ask)
    ? R.metricHelp(ask[0], ask[1]) : R.metricHelp(ask)));
}
if (job.litColour) {
  out.litColour = job.litColour.map((hex) => ({
    from: hex, to: R.litColour(hex),
    fromHsl: R.toHsl(hex), toHsl: R.toHsl(R.litColour(hex)),
  }));
}

// ── a group as a thing with a position of its own ─────────────────────────────
if (job.groupRows) {
  out.groupRows = job.groupRows.map(
    ({ scope, entity, gap }) => R.groupRows(scope, entity, gap));
}
if (job.groupBaseline) {
  out.groupBaseline = job.groupBaseline.map(
    ({ scope, entity, gap }) => R.groupBaselineRows(scope, entity, gap));
}
if (job.exactU) out.exactU = job.exactU.map(([n1, n2]) => [...R.exactUCounts(n1, n2)]);
if (job.logChoose) out.logChoose = job.logChoose.map(([n, k]) => R.logChoose(n, k));
if (job.histogram) {
  out.histogram = job.histogram.map(({ series }) => {
    const facets = series.map((values, i) => ({ name: `f${i}`, label: `f${i}`,
                                                color: '#000', values }));
    const grid = R.histogramOf(facets, 24);
    if (!grid) return null;
    return { min: grid.min, max: grid.max, bins: grid.bins, width: grid.width,
             centers: grid.centers, edges: grid.edges,
             facets: facets.map((f) => grid.of(f.values)) };
  });
}
if (job.baselineScore) {
  out.baselineScore = job.baselineScore.map(
    ({ distances, k, reach }) => R.baselineScore(distances, k, reach));
}

// ── a name-collision guard for the facet labels ───────────────────────────────
if (job.facetLabels) out.facetLabels = R.shortFacetLabels(job.facetLabels);

// ── the fragments a test would otherwise mirror by hand ───────────────────────
// Handed over rather than copied into the test, so a change here cannot leave a test
// passing against SQL the page no longer builds.
out.constants = {
  hasGeometry: R.HAS_GEOMETRY_SQL,
  geometrySize: R.GEOMETRY_SIZE_SQL,
  metricLabels: R.geometryMetrics(),
};

process.stdout.write(JSON.stringify(out));
