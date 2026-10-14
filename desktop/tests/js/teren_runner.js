// Rulează codul REAL al telefonului (backend/static/teren.js) în Node, față de un server HTTP local. Fault injection: pierdere cerere / pierdere răspuns.
// Utilizare: node teren_runner.js '<json cfg>'  -> tipărește JSON cu rezultatele scenariului.
const TC = require('../../../backend/static/teren.js');
const { webcrypto } = require('crypto');

const cfg = JSON.parse(process.argv[2]);
const store = {};
const storage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
if (cfg.key) { storage.setItem(TC.STORE_KEY, JSON.stringify({ binding_key: cfg.key, cemetery_uid: cfg.bound_cemetery || cfg.cemetery })); }

const calls = [];
let faults = (cfg.faults || []).map((f) => Object.assign({ times: 1 }, f));
function wrappedFetch(url, init) {
  const rec = { method: init.method, url, headers: Object.assign({}, init.headers), body: typeof init.body === 'string' ? init.body : null };
  calls.push(rec);
  const f = faults.find((x) => x.times > 0 && new RegExp(x.match).test(url) && (!x.method || x.method === init.method));
  if (f && f.mode === 'before') { f.times--; return Promise.reject(new TypeError('network down')); }
  const p = fetch(url, init);
  if (f && f.mode === 'after') { f.times--; return p.then(() => { throw new TypeError('response lost'); }); }
  return p;
}

const client = new TC.Client(cfg.base, storage, wrappedFetch, { cemetery: cfg.cemetery, crypto: webcrypto });
const F = (bytes, name, type) => new File([Buffer.from(bytes)], name, { type });
const JPG = [0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5, 6, 7, 8];
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4];
const PDF = Array.from(Buffer.from('%PDF-1.4\n1 0 obj\n'));

function build(c) { return TC.buildItem(c.type, c.form || {}, c.ctx || {}); }
function fileOf(c) {
  if (!c.file) { return null; }
  const bytes = { jpg: JPG, png: PNG, pdf: PDF, bad: [1, 2, 3, 4, 5, 6] }[c.file.kind];
  return F(bytes, c.file.name, c.file.type);
}

(async () => {
  const out = { calls };
  try {
    switch (cfg.scenario) {
      case 'session': out.session = await client.session(); break;
      case 'pair': out.pair = await client.pair(cfg.code, 'Telefon test'); out.session = await client.session(); out.stored = store[TC.STORE_KEY] || null; break;
      case 'send': {
        out.session = await client.session();
        const f = fileOf(cfg.req);
        TC.checkFile(cfg.req.type, f);
        out.result = await client.send(build(cfg.req), f); break;
      }
      case 'double_tap': {
        const item = build(cfg.req), file = fileOf(cfg.req);
        const [a, b] = await Promise.all([client.send(item, file), client.send(item, file)]);
        out.results = [a, b]; break;
      }
      case 'retry': {
        const item = build(cfg.req), f = fileOf(cfg.req);
        out.results = [];
        for (let i = 0; i < (cfg.attempts || 2); i++) { out.results.push(await client.send(item, f)); }
        break;
      }
      case 'edit_after_fail': {
        const r1 = build(cfg.req), r2 = build(cfg.req2);
        out.results = [await client.send(r1, fileOf(cfg.req)), await client.send(r2, fileOf(cfg.req2))]; break;
      }
      default: throw new Error('scenariu necunoscut');
    }
  } catch (e) { out.error = { name: e.name, message: e.message }; }
  out.storage = Object.keys(store).map((k) => [k, store[k]]);
  process.stdout.write(JSON.stringify(out));
})();
