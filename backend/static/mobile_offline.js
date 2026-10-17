// Copie locală (offline) a datelor AUTORIZATE ale telefonului + căutare/fișă/hartă offline. Doar registrele pe care serverul le permite (pull 403 => se șterg local).
const DB = 'cimitir-mobile-v2';
const ENTITIES = ['locuri_de_veci', 'concesionari', 'contracte', 'inhumati', 'plati', 'amenajari_avize'];

export function dbOpen() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 3);
    r.onupgradeneeded = () => { for (const s of ['kv', 'rows', 'photos']) if (!r.result.objectStoreNames.contains(s)) r.result.createObjectStore(s); };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
const req = (r) => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
const done = (tx) => new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
export async function kvGet(k) { const db = await dbOpen(); return (await req(db.transaction('kv', 'readonly').objectStore('kv').get(k))) || null; }
export async function kvSet(k, v) { const db = await dbOpen(); const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').put(v, k); return done(tx); }
export async function kvDel(k) { const db = await dbOpen(); const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').delete(k); return done(tx); }

async function putRows(entity, changes) {
  const db = await dbOpen(); const tx = db.transaction('rows', 'readwrite'); const st = tx.objectStore('rows');
  for (const c of changes) { const key = `${entity}|${c.uid}`; if (c.deleted) st.delete(key); else st.put({ uid: c.uid, version: c.version, data: c.data }, key); }
  return done(tx);
}
async function dropEntity(entity) {
  const db = await dbOpen(); const tx = db.transaction('rows', 'readwrite'); tx.objectStore('rows').delete(IDBKeyRange.bound(`${entity}|`, `${entity}|\uffff`));
  return done(tx);
}
export async function rowsOf(entity) {
  const db = await dbOpen();
  return req(db.transaction('rows', 'readonly').objectStore('rows').getAll(IDBKeyRange.bound(`${entity}|`, `${entity}|\uffff`)));
}
export async function mirrorClear() {
  const db = await dbOpen(); const tx = db.transaction(['rows', 'kv'], 'readwrite'); tx.objectStore('rows').clear();
  const kv = tx.objectStore('kv'); for (const e of ENTITIES) kv.delete(`cursor:${e}`); kv.delete('mirror_at');
  return done(tx);
}
export const mirrorAt = () => kvGet('mirror_at');

// Descărcare incrementală (cursor change_seq). `api(path)` aruncă Error cu .status. Întoarce {rows, denied:[...]}.
export async function mirrorSync(api, base) {
  let rows = 0; const denied = [];
  for (const e of ENTITIES) {
    let since = (await kvGet(`cursor:${e}`)) || 0;
    try {
      for (;;) {
        const page = await api(`${base}/sync-v2/pull?entity=${e}&since=${since}&limit=500`);
        if (page.changes.length) await putRows(e, page.changes);
        rows += page.changes.length; since = page.cursor; await kvSet(`cursor:${e}`, since);
        if (!page.more) break;
      }
    } catch (err) {
      if (err.status === 403) { denied.push(e); await dropEntity(e); await kvDel(`cursor:${e}`); } else throw err;
    }
  }
  await kvSet('mirror_at', Date.now());
  return { rows, denied };
}

// Copie locală + modificările din coadă (nesincronizate), marcate _pending.
async function merged(entity, outbox) {
  const map = new Map((await rowsOf(entity)).map((r) => [r.uid, { ...r, data: { ...r.data } }]));
  for (const o of outbox || []) {
    if (o.entity !== entity || (o.state !== 'pending' && o.state !== 'conflict')) continue;
    const cur = map.get(o.uid);
    map.set(o.uid, { uid: o.uid, version: cur?.version || 0, data: { ...(cur?.data || {}), ...o.data }, _pending: true });
  }
  return [...map.values()];
}
const nm = (d) => `${d.nume || ''} ${d.prenume || ''}`.trim();
const has = (q, ...vals) => vals.some((v) => String(v ?? '').toLowerCase().includes(q));

export async function offlineData(outbox) {
  const [L, C, K, I, P, A] = await Promise.all(ENTITIES.map((e) => merged(e, outbox)));
  return { L, C, K, I, P, A };
}
function shape(D, loc) {
  const g = { uid: loc.uid, version: loc.version, ...loc.data };
  g.occupied = !!(g.ocupat || g.are_inhumari || D.I.some((b) => b.data.loc_uid === loc.uid));
  const contracts = D.K.filter((k) => k.data.loc_uid === loc.uid).map((k) => {
    const c = D.C.find((x) => x.uid === k.data.concesionar_uid);
    return { uid: k.uid, version: k.version, ...k.data, concessionaire_uid: c?.uid, concessionaire_version: c?.version, nume: c?.data.nume, prenume: c?.data.prenume, telefon: c?.data.telefon, email: c?.data.email, adresa: c?.data.adresa, _pending: !!k._pending };
  });
  const kUids = new Set(contracts.map((k) => k.uid));
  return {
    grave: g, contracts,
    burials: D.I.filter((b) => b.data.loc_uid === loc.uid).map((b) => ({ uid: b.uid, version: b.version, ...b.data, _pending: !!b._pending })),
    payments: D.P.filter((p) => p.data.cod_loc === g.cod_loc || kUids.has(p.data.contract_uid)).map((p) => ({ uid: p.uid, version: p.version, ...p.data, _pending: !!p._pending })),
    permits: D.A.filter((a) => a.data.cod_loc === g.cod_loc).map((a) => ({ uid: a.uid, version: a.version, ...a.data, _pending: !!a._pending })),
  };
}
export async function offlineGrave(uid, outbox) {
  const D = await offlineData(outbox); const loc = D.L.find((l) => l.uid === uid);
  return loc ? shape(D, loc) : null;
}
export async function offlineMap(outbox) {
  const D = await offlineData(outbox);
  return { plots: D.L.map((l) => ({ uid: l.uid, code: l.data.cod_loc || '', points: l.data.points || [], sector: l.data.sector || '', parcel: l.data.parcela || '', row: l.data.rand || '', number: l.data.numar || '', state: l.data.stare || '', occupied: !!(l.data.ocupat || l.data.are_inhumari || D.I.some((b) => b.data.loc_uid === l.uid)) })), sectors: [] };
}
export async function offlineSearch(query, outbox) {
  const q = String(query || '').trim().toLowerCase(); const D = await offlineData(outbox);
  const code = (uid) => D.L.find((l) => l.uid === uid)?.data.cod_loc || '';
  const conc = (k) => nm(D.C.find((x) => x.uid === k.data.concesionar_uid)?.data || {});
  return {
    graves: D.L.filter((l) => has(q, l.data.cod_loc, l.data.nr_vechi, l.data.sector, l.data.parcela)).slice(0, 30).map((l) => ({ uid: l.uid, code: l.data.cod_loc, sector: l.data.sector, parcel: l.data.parcela, row: l.data.rand, occupied: !!(l.data.ocupat || l.data.are_inhumari) })),
    burials: D.I.filter((b) => has(q, nm(b.data), b.data.cnp, code(b.data.loc_uid))).slice(0, 30).map((b) => ({ uid: b.uid, name: nm(b.data), death_date: b.data.data_deces, grave_uid: b.data.loc_uid || '', grave_code: code(b.data.loc_uid) })),
    concessionaires: D.C.filter((c) => has(q, nm(c.data), c.data.telefon, c.data.cnp)).slice(0, 30).map((c) => { const k = D.K.find((x) => x.data.concesionar_uid === c.uid); return { uid: c.uid, name: nm(c.data), phone: c.data.telefon, grave_uid: k?.data.loc_uid || '', grave_code: k ? code(k.data.loc_uid) : '' }; }),
    contracts: D.K.filter((k) => has(q, k.data.numar_contract, k.data.cod_loc, conc(k))).slice(0, 30).map((k) => ({ uid: k.uid, number: k.data.numar_contract, grave_uid: k.data.loc_uid || '', grave_code: code(k.data.loc_uid), concessionaire: conc(k) })),
  };
}
export async function offlineList(kind, query, outbox) {
  const q = String(query || '').trim().toLowerCase(); const D = await offlineData(outbox);
  const code = (uid) => D.L.find((l) => l.uid === uid)?.data.cod_loc || '';
  let items = [];
  if (kind === 'locuri') items = D.L.map((l) => ({ t: `Loc ${l.data.cod_loc || ''}`, s: [l.data.sector && `sector ${l.data.sector}`, l.data.stare].filter(Boolean).join(' · '), grave: l.uid, p: l._pending }));
  if (kind === 'inhumati') items = D.I.map((b) => ({ t: nm(b.data), s: `${code(b.data.loc_uid) ? 'loc ' + code(b.data.loc_uid) : 'loc neasociat'}${b.data.data_deces ? ' · deces ' + b.data.data_deces : ''}`, grave: b.data.loc_uid, p: b._pending }));
  if (kind === 'contracte') items = D.K.map((k) => { const c = D.C.find((x) => x.uid === k.data.concesionar_uid); return { t: `Contract ${k.data.numar_contract || '—'}`, s: `${nm(c?.data || {}) || '—'}${code(k.data.loc_uid) ? ' · loc ' + code(k.data.loc_uid) : ''}`, grave: k.data.loc_uid, p: k._pending }; });
  if (kind === 'plati') items = D.P.map((p) => { const k = D.K.find((x) => x.uid === p.data.contract_uid); return { t: `${p.data.suma ?? 0} lei · ${p.data.tip || 'plată'}`, s: `${p.data.chitanta_nr || 'fără chitanță'} · ${p.data.data_plata || '—'}${p.data.cod_loc ? ' · loc ' + p.data.cod_loc : ''}`, grave: k?.data.loc_uid || D.L.find((l) => l.data.cod_loc === p.data.cod_loc)?.uid, p: p._pending }; });
  return items.filter((x) => !q || has(q, x.t, x.s)).slice(0, 300);
}
