// What each viewer file defines, and which other files it reads from.
//
//   node dependencies.mjs [--json] [--check]
//
// The files in src/ are chunks of one script, so a name one file declares is a global the others read.
// This parses every file with acorn, resolves scopes with eslint-scope, and matches each name a file
// reads but does not declare against the file that declares it. A read made while the page loads (outside
// any function) is reported apart from one made later: only the first constrains load order.
// --check exits 1 on a name nothing declares, a name declared twice, a load-time read of a name that is
// not yet initialised in the order load-order.txt gives, a read of a file in a higher layer (layer-order.txt lists
// them, lowest first; a trailing / is a folder), or a cycle between files.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as acorn from 'acorn';
import * as eslintScope from 'eslint-scope';
import globals from 'globals';

const viewer = join(dirname(fileURLToPath(import.meta.url)), '../../src/organella/viewer');
const sections = readFileSync(join(viewer, 'load-order.txt'), 'utf8').split('\n').filter(Boolean);

// Provided by the browser or by a library loaded before the script.
const layers = readFileSync(join(viewer, 'layer-order.txt'), 'utf8').split('\n').filter(Boolean);
const layerOf = (file) => {
  const i = layers.findIndex((l) => (l.endsWith('/') ? file.startsWith(l) : file === l));
  if (i < 0) throw new Error(`${file} is in no layer of layer-order.txt`);
  return i;
};

const environment = new Set([
  ...Object.keys(globals.browser), ...Object.keys(globals.builtin),
  'Plotly', 'bootstrap', 'THREE', 'duckdb',
]);

const insideFunction = (scope) => {
  for (let s = scope; s; s = s.upper) if (s.type === 'function') return true;
  return false;
};

function analyse(name) {
  const code = readFileSync(join(viewer, 'src', `${name}.js`), 'utf8');
  const ast = acorn.parse(code, { ecmaVersion: 'latest', sourceType: 'script', locations: true, ranges: true });
  const manager = eslintScope.analyze(ast, { ecmaVersion: 2022, sourceType: 'script' });
  const global = manager.globalScope;
  const defines = new Map();
  for (const v of global.variables) {
    const kinds = v.defs.map((d) => d.type);
    if (v.defs.length) defines.set(v.name, kinds.includes('FunctionName') ? 'function' : kinds[0] === 'ClassName' ? 'class' : 'variable');
  }
  const reads = new Map();
  for (const ref of global.through) {
    const id = ref.identifier.name;
    if (defines.has(id) || environment.has(id)) continue;
    const entry = reads.get(id) ?? { loadTime: false, line: ref.identifier.loc.start.line };
    entry.loadTime ||= !insideFunction(ref.from);
    reads.set(id, entry);
  }
  return { name, defines, reads };
}

const files = sections.map(analyse);
const owners = new Map();
for (const f of files) for (const n of f.defines.keys()) owners.set(n, [...(owners.get(n) ?? []), f.name]);

const problems = [];
if (sections.at(-1) !== 'ui/app/start') problems.push(`ui/app/start must load last, so every initialiser has run before boot(); the last file is ${sections.at(-1)}`);
const order = new Map(sections.map((s, i) => [s, i]));
const result = files.map((f) => {
  const uses = {};
  for (const [id, { loadTime, line }] of f.reads) {
    const from = owners.get(id);
    if (!from) { problems.push(`${f.name}:${line} reads '${id}', which nothing declares`); continue; }
    const owner = from[0];
    (uses[owner] ??= []).push({ name: id, loadTime });
    if (layerOf(owner) > layerOf(f.name)) problems.push(`${f.name}:${line} reads '${id}' from ${owner}, a higher layer`);
    const kind = files.find((x) => x.name === owner).defines.get(id);
    if (loadTime && kind !== 'function' && order.get(owner) > order.get(f.name))
      problems.push(`${f.name}:${line} reads '${id}' at load time, but ${owner} initialises it later`);
  }
  return { file: f.name, defines: Object.fromEntries(f.defines), uses };
});
// a cycle between files means they cannot be told apart as 'below' and 'above' each other
const edges = new Map(result.map((r) => [r.file, Object.keys(r.uses)]));
const visiting = new Set(), done = new Set();
const visit = (file, trail) => {
  if (done.has(file)) return;
  if (visiting.has(file)) { problems.push(`files depend on each other: ${[...trail.slice(trail.indexOf(file)), file].join(' -> ')}`); return; }
  visiting.add(file);
  for (const next of edges.get(file)) visit(next, [...trail, file]);
  visiting.delete(file); done.add(file);
};
for (const f of edges.keys()) visit(f, []);
for (const [n, from] of owners) if (from.length > 1) problems.push(`'${n}' is declared in ${from.join(', ')}`);

if (process.argv.includes('--json')) console.log(JSON.stringify({ files: result, problems }, null, 1));
else {
  for (const f of result) {
    const total = (xs) => xs.length;
    console.log(`${f.file}  defines ${Object.keys(f.defines).length}`);
    for (const [owner, names] of Object.entries(f.uses)) {
      const now = names.filter((n) => n.loadTime).map((n) => n.name);
      console.log(`    <- ${owner}  (${total(names)} names${now.length ? `; at load time: ${now.join(', ')}` : ''})`);
    }
  }
  console.log(problems.length ? `\n${problems.length} problem(s):\n  ${problems.join('\n  ')}` : '\nno problems');
}
if (process.argv.includes('--check') && problems.length) process.exit(1);
