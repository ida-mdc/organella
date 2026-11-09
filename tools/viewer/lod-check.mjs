// Checks the fragment-cut selection (model/lod-select.js) on a synthetic three-level mesh, with no browser.
//
//   node lod-check.mjs        exits 1 on the first claim that fails
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const context = vm.createContext({ console, Map, Math, Array });
vm.runInContext(readFileSync(join(here, '../../src/organella/viewer/src/model/lod-select.js'), 'utf8'), context);
const { buildFragmentTree, selectCut, boxInFrustum, distanceToBox } = vm.runInContext(
  '({ buildFragmentTree, selectCut, boxInFrustum, distanceToBox })', context);

// Level 0: a 4x4 plate of cells (z = 0) of side 1; level 1: 2x2 of side 2; level 2: one cell of side 4.
function manifest() {
  const fragments = (n) => { const out = []; for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) out.push({ ijk: [i, j, 0], off: 0, size: 10, nv: 100, nt: 150 }); return out; };
  return { levels: [
    { error: 0.01, origin: [0, 0, 0], cell: [1, 1, 1], fragments: fragments(4) },
    { error: 0.04, origin: [0, 0, 0], cell: [2, 2, 2], fragments: fragments(2) },
    { error: 0.16, origin: [0, 0, 0], cell: [4, 4, 4], fragments: fragments(1) }] };
}
const tree = buildFragmentTree(manifest());
const everything = { planes: null };
const ids = (nodes) => [...nodes.map((n) => n.id).sort()];   // spread: an array of this realm, which deepEqual can compare

// The tree: one root, four children each with four, and every node knows its parent.
assert.equal(tree.roots.length, 1);
assert.equal(tree.roots[0].children.length, 4);
assert.equal(tree.levels[0].nodes.get('3,2,0').parent.id, '1:1,1,0');

// Far away everything is within tolerance: the root alone. Up close, the finest level, and it covers the plate once.
const far = selectCut(tree, { ...everything, eye: [2, 2, 1000], focalPx: 1000 }, {});
assert.deepEqual(ids(far.draw), ['2:0,0,0']);
const near = selectCut(tree, { ...everything, eye: [2, 2, 0.5], focalPx: 1000 }, {});
assert.equal(near.draw.length, 16);
assert.ok(near.draw.every((n) => n.level === 0));

// Detail follows the camera: over one corner of the plate the cells there are the finest and the far ones coarser.
const corner = selectCut(tree, { ...everything, eye: [0.2, 0.2, 0.3], focalPx: 60 }, {});
const levelsDrawn = new Set(corner.draw.map((n) => n.level));
assert.ok(levelsDrawn.has(0) && levelsDrawn.size > 1, 'a mix of levels');
const area = (nodes) => nodes.reduce((s, n) => s + (n.max[0] - n.min[0]) * (n.max[1] - n.min[1]), 0);
assert.equal(area(corner.draw), 16, 'the cut covers the plate exactly once');

// Nothing loaded but the root: the root is drawn in place of what is still on its way, and the rest is asked for, worst first.
const loadedRoot = (n) => n.level === 2;
const waiting = selectCut(tree, { ...everything, eye: [2, 2, 0.5], focalPx: 1000 }, { isLoaded: loadedRoot });
assert.deepEqual(ids(waiting.draw), ['2:0,0,0']);
assert.equal(waiting.request.length, 16);
// A fragment loaded beside one that is not: the ancestor is drawn once, not on top of the loaded one.
const some = (n) => n.level === 2 || n.id === '0:0,0,0';
const mixed = selectCut(tree, { ...everything, eye: [2, 2, 0.5], focalPx: 1000 }, { isLoaded: some });
assert.deepEqual(ids(mixed.draw), ['2:0,0,0']);

// The vertex budget stops the refining: 100 per fragment, 16 would be 1600.
const poor = selectCut(tree, { ...everything, eye: [2, 2, 0.5], focalPx: 1000 }, { maxVertices: 600 });
assert.ok(poor.vertices <= 600 && poor.draw.length < 16);

// Culling: planes that keep only x >= 2 drop the cells left of it.
const half = selectCut(tree, { eye: [3, 2, 0.5], focalPx: 1000, planes: [[1, 0, 0, -2]] }, {});
assert.ok(half.draw.length > 0 && half.draw.every((n) => n.max[0] > 2 - 1e-9));
assert.equal(boxInFrustum([[0, 0, 1, -5]], [0, 0, 0], [1, 1, 1]), false);
assert.equal(distanceToBox([0.5, 0.5, 0.5], [0, 0, 0], [1, 1, 1]), 0);
assert.ok(Math.abs(distanceToBox([3, 0.5, 0.5], [0, 0, 0], [1, 1, 1]) - 2) < 1e-12);

// A cell the coarse levels lost is still reachable: it is a root of its own.
const lost = manifest();
lost.levels[2].fragments = [{ ijk: [0, 0, 0], off: 0, size: 10, nv: 100, nt: 150 }];
lost.levels[1].fragments = lost.levels[1].fragments.filter((f) => f.ijk[0] === 0);
const holey = buildFragmentTree(lost);
assert.ok(holey.roots.length > 1);
console.log('lod-check: ok');
