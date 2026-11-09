// Open a served report whose big meshes have levels of detail in headless Chrome, zoom in, and print what was refined, as JSON.
//
//   node multires-check.mjs http://127.0.0.1:8052/?data=/report.parquet [timeoutSeconds]
//
// Exit 77 when there is no Chrome (set CHROME to point at one) or the page's CDN scripts cannot be reached.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, timeoutSeconds = '120'] = process.argv.slice(2);
const chrome = process.env.CHROME ?? ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(existsSync);
const skip = (why) => { console.error(why); process.exit(77); };
if (!chrome) skip('no chrome found');
try { await fetch('https://cdn.jsdelivr.net/npm/plotly.js@2.27.0/dist/plotly.min.js', { method: 'HEAD', signal: AbortSignal.timeout(8000) }); }
catch { skip('the CDN the page loads its libraries from is unreachable'); }

const profile = mkdtempSync(join(tmpdir(), 'cdp'));   // a long user-data-dir makes Chrome refuse its socket path
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-sandbox',
  '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'], detached: true });   // own process group: Chrome forks helpers
const closeBrowser = async () => {
  await new Promise((done) => { browser.once('exit', done); process.kill(-browser.pid); });
  await new Promise((r) => setTimeout(r, 500));   // helpers still write to the profile for a moment
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* a temp dir, not worth failing for */ }
};

const endpoint = await new Promise((resolve, reject) => {
  let seen = '';
  browser.stderr.on('data', (chunk) => { seen += chunk; const m = seen.match(/DevTools listening on (ws:\/\/\S+)/); if (m) resolve(m[1]); });
  browser.on('exit', () => reject(new Error(`chrome exited: ${seen.slice(-300)}`)));
});

const socket = new WebSocket(endpoint);
await new Promise((resolve) => socket.addEventListener('open', resolve));
let nextId = 0, session;
const waiting = new Map(), problems = [], requests = new Map();
socket.addEventListener('message', ({ data }) => {
  const m = JSON.parse(data);
  if (m.id !== undefined) { waiting.get(m.id)?.(m); return; }
  if (m.method === 'Runtime.exceptionThrown') problems.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) problems.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Network.requestWillBeSent') requests.set(m.params.requestId, { url: m.params.request.url, range: m.params.request.headers.Range ?? m.params.request.headers.range ?? null });
  if (m.method === 'Network.loadingFailed') Object.assign(requests.get(m.params.requestId) ?? {}, { status: 'failed' });
  if (m.method === 'Network.responseReceived') Object.assign(requests.get(m.params.requestId) ?? {}, { url: m.params.response.url, status: m.params.response.status });
});
const send = (method, params = {}, sessionId = session) => new Promise((resolve) => {
  const id = ++nextId; waiting.set(id, resolve);
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const evaluate = async (expression) => { const r = (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result; if (r?.exceptionDetails) console.error("evaluate failed:", r.exceptionDetails.exception?.description ?? r.exceptionDetails.text); return r?.result?.value; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const { result: { targetId } } = await send('Target.createTarget', { url: 'about:blank' });
session = (await send('Target.attachToTarget', { targetId, flatten: true })).result.sessionId;
await Promise.all(['Runtime', 'Log', 'Page', 'Network'].map((d) => send(`${d}.enable`)));
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });

const state = `JSON.stringify(typeof _multires === 'undefined' || !_multires ? { records: 0 } : {
  records: _multires.records.length,
  refined: _multires.records.filter((r) => r.shown.size).length,
  finest: _multires.records.filter((r) => [...r.shown.keys()].some((id) => String(id).startsWith('0:'))).length,
  failed: _multires.failed.size })`;
const deadline = Date.now() + Number(timeoutSeconds) * 1000;
let now = { records: 0 };
while (Date.now() < deadline && !now.records) { await sleep(1000); const raw = await evaluate(state); if (raw) now = JSON.parse(raw); }
const first = now;
// Zoom in at the canvas until an instance is drawn at its finest level, or the time is up.
for (let step = 0; step < 60 && Date.now() < deadline && !(now.finest > 0); step++) {
  await evaluate(`document.querySelector('#scene-holder canvas').dispatchEvent(new WheelEvent('wheel', { deltaY: -150, bubbles: true, cancelable: true }))`);
  await sleep(700);
  const raw = await evaluate(state); if (raw) now = JSON.parse(raw);
}
// The full-size look at one instance reads its finest level whole: its vertices must be the finest level's.
const whole = await evaluate(`(async () => {
  const record = _multires && _multires.records[0]; if (!record) return null;
  const levels = record.item.levels, geometry = await multiresFinest(GEOMETRY[0], levels);
  return JSON.stringify({ read: geometry ? geometry.positions.length / 3 : 0, wanted: levels.levels[0].fragments.reduce((n, f) => n + f.nv, 0) });
})()`);
// Pinning a chart repaints the scene in its explaining form: the big meshes must still be refined there.
const pinned = await evaluate(`(async () => {
  const record = _multires.records.find((r) => !r.key.endsWith('|mask')); if (!record) return null;
  const entity = record.key.split('|')[0], col = Object.keys(INSTANCES[0]).find((k) => /volume/.test(k) && typeof INSTANCES[0][k] === 'number');
  pinToViewer(metricSpec(entity, col));
  await new Promise((r) => setTimeout(r, 6000));
  return JSON.stringify({ explain: !!sceneState.explain, records: _multires ? _multires.records.length : 0,
    refined: _multires ? _multires.records.filter((r) => r.shown.size).length : 0 });
})()`);
const meshBin = [...requests.values()].filter((r) => /meshes\.bin/.test(r.url));
console.log(JSON.stringify({ first, ...now, whole: whole ? JSON.parse(whole) : null, pinned: pinned ? JSON.parse(pinned) : null, statuses: [...new Set(meshBin.map((r) => r.status).filter((v) => v !== undefined))],
  rangeRequests: meshBin.length,
  geometry: [...requests.values()].filter((r) => /geometry\.parquet/.test(r.url)).map((r) => [r.status, r.range]), problems }));
await closeBrowser();
process.exit(0);
