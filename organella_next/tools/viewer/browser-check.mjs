// Open a served report in headless Chrome and print what a reader would see, as JSON.
//
//   node browser-check.mjs http://127.0.0.1:8052/?data=/report.parquet [timeoutSeconds]
//
// No dependencies: Chrome is driven over the DevTools protocol with node's own WebSocket. Exit 77 when there is
// no Chrome (set CHROME to point at one) or the page's CDN scripts cannot be reached.
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
const waiting = new Map(), problems = [];
socket.addEventListener('message', ({ data }) => {
  const m = JSON.parse(data);
  if (m.id !== undefined) { waiting.get(m.id)?.(m); return; }
  if (m.method === 'Runtime.exceptionThrown') problems.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') problems.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error' && !/favicon/.test(m.params.entry.url ?? '')) problems.push(`${m.params.entry.text} ${m.params.entry.url ?? ''}`);
});
const send = (method, params = {}, sessionId = session) => new Promise((resolve) => {
  const id = ++nextId; waiting.set(id, resolve);
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true })).result?.result?.value;

const { result: { targetId } } = await send('Target.createTarget', { url: 'about:blank' });
session = (await send('Target.attachToTarget', { targetId, flatten: true })).result.sessionId;
await Promise.all(['Runtime', 'Log', 'Page'].map((d) => send(`${d}.enable`)));
await send('Page.navigate', { url });

const snapshot = `JSON.stringify({
  charts: document.querySelectorAll('.js-plotly-plot').length,
  loadError: document.getElementById('landing-error') && !document.getElementById('landing-error').classList.contains('d-none')
    ? document.getElementById('landing-error-msg').textContent : null,
  sections: Object.fromEntries([...document.querySelectorAll('section[id^="s-"], [id^="s-"]')].filter((e) => /^s-[a-z0-9]+$/.test(e.id))
    .map((e) => [e.id, { text: e.innerText.trim().length, charts: e.querySelectorAll('.js-plotly-plot').length }]))
})`;
const deadline = Date.now() + Number(timeoutSeconds) * 1000;
let last = null, steady = 0, now;
while (Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, 1000));
  const raw = await evaluate(snapshot);
  if (!raw) continue;
  now = JSON.parse(raw);
  if (now.loadError) break;
  steady = last && now.charts > 0 && now.charts === last.charts ? steady + 1 : 0;   // drawn, and no longer growing
  last = now;
  if (steady >= 3) break;
}
console.log(JSON.stringify({ ...now, problems, drawn: steady >= 3 }));
await closeBrowser();
process.exit(0);
