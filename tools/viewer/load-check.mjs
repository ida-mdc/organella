// Run a built page's script in node with no DOM, as the browser would at load, and list what it defines.
//
//   node load-check.mjs page.html            prints the top-level names, one per line
//
// A reference to a name that is not initialised yet throws here, at load, instead of in front of a reader.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(process.argv[2], 'utf8');
const blocks = [...html.matchAll(/<script>\n([\s\S]*?)<\/script>/g)];
if (blocks.length !== 1) throw new Error(`expected one inline <script>, found ${blocks.length}`);
const context = vm.createContext({ console, setTimeout, clearTimeout });
vm.runInContext(blocks[0][1], context);
const names = vm.runInContext('Object.getOwnPropertyNames(globalThis)', context);
console.log(names.sort().join('\n'));
