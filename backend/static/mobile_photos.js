// Coadă offline pentru fotografii/documente (IndexedDB, doar pe acest dispozitiv): declarare idempotentă -> încărcare -> verificare; reluare automată; fără duplicate.
import { dbOpen } from './mobile_offline.js';

const STORE = 'photos';
const MAX_BYTES = 25 * 1024 * 1024;
const TYPES = { 'image/jpeg': [0xff, 0xd8, 0xff], 'image/png': [0x89, 0x50, 0x4e, 0x47], 'application/pdf': [0x25, 0x50, 0x44, 0x46] };
const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
const done = (tx) => new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = () => rej(tx.error); });
const newId = () => ((crypto.randomUUID?.() || `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}0000000000000000`)).replace(/-/g, '').slice(0, 32);

export async function sha256Hex(buf) {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
}
export async function queueList() {
  const db = await dbOpen();
  return (await req(db.transaction(STORE, 'readonly').objectStore(STORE).getAll())).sort((a, b) => a.created - b.created);
}
async function put(item) { const db = await dbOpen(); const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put(item, item.id); return done(tx); }
async function drop(id) { const db = await dbOpen(); const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).delete(id); return done(tx); }
export const dropPhoto = drop;
export async function queueCounts() {
  const q = await queueList();
  return { pending: q.filter((x) => x.state === 'pending' || x.state === 'uploading').length, error: q.filter((x) => x.state === 'error').length, total: q.length };
}

// Pune fișierul în coadă. Întoarce {ok, duplicate?, error?}. Același conținut pentru aceeași entitate nu se pune de două ori.
export async function enqueue(file, { entity = 'locuri_de_veci', entity_uid, caption = '', doc_type = 'fotografie_loc' } = {}) {
  if (!file || !entity_uid) return { ok: false, error: 'Fișier sau loc lipsă.' };
  if (!TYPES[file.type]) return { ok: false, error: 'Sunt acceptate doar JPEG, PNG sau PDF.' };
  if (!(file.size > 0 && file.size <= MAX_BYTES)) return { ok: false, error: 'Fișier gol sau prea mare (maxim 25 MB).' };
  const buf = await file.arrayBuffer();
  const head = new Uint8Array(buf, 0, 4);
  if (!TYPES[file.type].every((b, i) => head[i] === b)) return { ok: false, error: 'Conținutul nu corespunde tipului fișierului.' };
  const sha = await sha256Hex(buf);
  if ((await queueList()).some((x) => x.sha256 === sha && x.entity_uid === entity_uid && x.entity === entity)) return { ok: true, duplicate: true };
  const ext = file.type === 'image/png' ? 'png' : file.type === 'application/pdf' ? 'pdf' : 'jpg';
  const name = (file.name || `foto.${ext}`).replace(/[^\w.\- ]/g, '_').slice(0, 100);
  await put({ id: newId(), entity, entity_uid, doc_type, kind: file.type === 'application/pdf' ? 'document' : 'photo', filename: name, mime: file.type, size: file.size, sha256: sha, caption: String(caption).slice(0, 500), buf, state: 'pending', attempts: 0, error: '', created: Date.now() });
  return { ok: true };
}

const PERMANENT = new Set([400, 403, 415]);
// Trimite coada. `api(path, options)` aruncă Error cu .status (fără .status = rețea). Oprește la prima eroare de rețea; elementele rămân în coadă.
export async function flush(api, base) {
  const out = { sent: 0, duplicate: 0, errors: 0, offline: false, waiting: 0 };
  for (const it of await queueList()) {
    if (it.state === 'error' && it.attempts >= 5) continue;
    try {
      it.state = 'uploading'; it.attempts += 1; await put(it);
      const meta = { uid: it.id, entity: it.entity, entity_uid: it.entity_uid, doc_type: it.doc_type, kind: it.kind, filename: it.filename, mime: it.mime, size: it.size, sha256: it.sha256, caption: it.caption };
      const d = await api(`${base}/sync-v2/documents/declare`, { method: 'POST', body: JSON.stringify(meta) });
      if (d.status === 'retry') { it.state = 'pending'; it.error = 'Locul nu este încă în cloud; se reîncearcă după sincronizare.'; await put(it); out.waiting++; continue; }
      if (d.status === 'duplicate') { await drop(it.id); out.duplicate++; continue; }
      if (d.status === 'upload') await api(`${base}/sync-v2/documents/${it.id}/content`, { method: 'PUT', body: it.buf, headers: { 'Content-Type': 'application/octet-stream' } });
      await drop(it.id); out.sent++;
    } catch (e) {
      if (!e.status) { it.state = 'pending'; it.attempts = Math.max(0, it.attempts - 1); await put(it); out.offline = true; break; }
      if (e.status === 401) { it.state = 'pending'; await put(it); throw e; }
      it.state = PERMANENT.has(e.status) || e.status === 409 ? 'error' : 'pending'; it.error = e.message || 'Eroare'; await put(it); out.errors++;
    }
  }
  return out;
}
export const retryErrors = async () => { for (const it of await queueList()) if (it.state === 'error') { it.state = 'pending'; it.attempts = 0; it.error = ''; await put(it); } };
