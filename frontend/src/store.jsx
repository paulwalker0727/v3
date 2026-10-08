import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react';
import { toast } from 'sonner';
import { buildMock, parseRo, todayRo, addDays, STATUS_KEYS } from './mock';

const KEY = 'cimitir_mock_v1';
const SKEY = 'cimitir_session';
const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

export const SOURCES = [
  { id: 'cimitir_demo', name: 'Cimitirul Central', engine: 'sqlite', available: true },
  { id: 'cimitir_nord', name: 'Cimitirul Nord', engine: 'sqlite', available: false, error: 'Fișier indisponibil (cimitir_nord.db)' },
];

export function computeStats(db) {
  const total = db.plots.length;
  const sc = Object.fromEntries(STATUS_KEYS.map((k) => [k, 0]));
  db.plots.forEach((p) => { sc[p.status] = (sc[p.status] || 0) + 1; });
  const now = addDays(0); const lim = addDays(30);
  const plotOf = (cod) => db.plots.find((p) => p.cod_loc === cod);
  const expiring = db.contracte.map((c) => ({ ...c, _d: parseRo(c.data_sfarsit), _expira: c.data_sfarsit, _sector: plotOf(c.cod_loc)?.sector }))
    .filter((c) => c._d && c._d >= now && c._d <= lim).sort((a, b) => a._d - b._d);
  const total_incasari = db.plati.reduce((s, p) => s + (Number(p.suma) || 0), 0);
  return {
    total_locuri: total, status_counts: sc, grad_ocupare: total ? Math.round((sc.ocupat / total) * 100) : 0,
    total_incasari, nr_plati: db.plati.length, contracte_expira: expiring.length, expiring,
    cereri_noi: db.cereri.filter((c) => c.status === 'nou').length, recent_cereri: db.cereri.slice(0, 4),
    recent_inhumati: [...db.inhumati].reverse().slice(0, 6),
  };
}

export function AppProvider({ children }) {
  const [db, setDb] = useState(() => { try { const s = localStorage.getItem(KEY); if (s) return JSON.parse(s); } catch (e) { /* ignore */ } return buildMock(); });
  const [session, setSession] = useState(() => { try { return JSON.parse(localStorage.getItem(SKEY)); } catch (e) { return null; } });
  const [drawer, setDrawer] = useState(null);
  const [att, setAtt] = useState(null);

  useEffect(() => { localStorage.setItem(KEY, JSON.stringify(db)); }, [db]);
  useEffect(() => { if (session) localStorage.setItem(SKEY, JSON.stringify(session)); else localStorage.removeItem(SKEY); }, [session]);

  const stats = useMemo(() => computeStats(db), [db]);
  const upd = useCallback((fn) => setDb((prev) => { const next = JSON.parse(JSON.stringify(prev)); fn(next); return next; }), []);
  const nid = (d) => ++d.seq;
  const rights = session ? (session.role === 'admin' ? ['view', 'edit', 'approve', 'manage_users'] : ['view', ...(db.users.find((u) => u.id === session.uid)?.rights || [])]) : [];

  const a = {
    login(sourceId, username, password) {
      const u = db.users.find((x) => x.username === username.trim());
      if (!u || u.password !== password || !u.active) return 'Utilizator sau parolă incorectă.';
      const source = SOURCES.find((s) => s.id === sourceId);
      setSession({ uid: u.id, username: u.username, role: u.role, source });
      return null;
    },
    logout() { setSession(null); setDrawer(null); setAtt(null); },
    saveLoc(cod, f) { upd((d) => { const p = d.plots.find((x) => x.cod_loc === cod); Object.assign(p, f); }); },
    addInhumat(cod, f) { upd((d) => { d.inhumati.push({ id: nid(d), cod_loc: cod, ...f }); const p = d.plots.find((x) => x.cod_loc === cod); if (p.status === 'liber' || p.status === 'rezervat') p.status = 'ocupat'; }); },
    delInhumat(id) { upd((d) => { d.inhumati = d.inhumati.filter((x) => x.id !== id); }); },
    addContract(cod, f) {
      if (!f.numar_contract?.trim()) return 'Numărul contractului este obligatoriu.';
      if (db.contracte.some((c) => c.numar_contract === f.numar_contract.trim())) return 'Există deja un contract cu acest număr.';
      if (!f.concesionar_id && !f.nume?.trim()) return 'Alege un concesionar existent sau completează numele unuia nou.';
      if (f.data_contract && !parseRo(f.data_contract)) return 'Data contractului trebuie să fie în formatul zz.ll.aaaa.';
      if (f.data_sfarsit && !parseRo(f.data_sfarsit)) return 'Data de expirare trebuie să fie în formatul zz.ll.aaaa.';
      upd((d) => {
        let cid = Number(f.concesionar_id) || null; let c = d.concesionari.find((x) => x.id === cid);
        if (!c) { c = { id: nid(d), nume: f.nume.trim(), prenume: f.prenume || '', cnp: f.cnp || '', bi_ci: '', adresa: f.adresa || '', telefon: f.telefon || '', email: '' }; d.concesionari.push(c); }
        d.contracte.push({ id: nid(d), numar_contract: f.numar_contract.trim(), cod_loc: cod, concesionar_id: c.id, concesionar_nume: `${c.nume} ${c.prenume}`.trim(), data_contract: f.data_contract, data_sfarsit: f.data_sfarsit });
        const p = d.plots.find((x) => x.cod_loc === cod); if (p.status === 'liber') p.status = 'ocupat';
      });
      return null;
    },
    delContract(id) { upd((d) => { d.contracte = d.contracte.filter((x) => x.id !== id); }); },
    addPlata(cod, f) { upd((d) => { d.plati.push({ id: nid(d), cod_loc: cod, ...f, suma: Number(String(f.suma).replace(',', '.')) || 0, data_plata: f.data_plata || todayRo() }); }); },
    delPlata(id) { upd((d) => { d.plati = d.plati.filter((x) => x.id !== id); }); },
    addAviz(cod, f) { upd((d) => { d.avize.push({ id: nid(d), cod_loc: cod, ...f }); }); },
    delAviz(id) { upd((d) => { d.avize = d.avize.filter((x) => x.id !== id); }); },
    saveConcesionar(f) { upd((d) => { if (f.id) Object.assign(d.concesionari.find((x) => x.id === Number(f.id)), f, { id: Number(f.id) }); else d.concesionari.push({ ...f, id: nid(d) }); }); },
    delConcesionar(id) { upd((d) => { d.concesionari = d.concesionari.filter((x) => x.id !== id); }); },
    decideCerere(id, status, note) {
      upd((d) => {
        const c = d.cereri.find((x) => x.id === id); c.status = status; c.review_note = note || c.review_note;
        if (status === 'validat') {
          const conc = { id: nid(d), nume: c.nume, prenume: c.prenume, cnp: c.cnp, bi_ci: c.bi_ci, adresa: c.adresa, telefon: c.telefon, email: c.email };
          d.concesionari.push(conc);
          if (c.cod_loc) {
            d.contracte.push({ id: nid(d), numar_contract: `DRAFT-${c.id}`, cod_loc: c.cod_loc, concesionar_id: conc.id, concesionar_nume: `${c.nume} ${c.prenume}`, data_contract: todayRo(), data_sfarsit: '' });
            const p = d.plots.find((x) => x.cod_loc === c.cod_loc); if (p && p.status === 'liber') p.status = 'rezervat';
          }
        }
      });
    },
    addCerere(f) {
      let id = 0;
      upd((d) => { id = nid(d); d.cereri.unshift({ id, nume: f.nume, prenume: f.prenume, status: 'nou', grad_rudenie: f.grad_rudenie || '', cod_loc: f.cod_loc || '', created_at: `${todayRo()} 12:00`, cnp: f.cnp || '', bi_ci: f.bi_ci || '', adresa: f.adresa || '', telefon: f.telefon || '', email: f.email || '', observatii: f.observatii || '', documents: [], review_note: '' }); });
      return id;
    },
    addAtt(etype, eid, f) { upd((d) => { d.attachments.push({ id: nid(d), etype, eid: String(eid), ...f }); }); },
    delAtt(id) { upd((d) => { d.attachments = d.attachments.filter((x) => x.id !== id); }); },
    addUser(f) { if (db.users.some((u) => u.username === f.username)) return 'Utilizatorul există deja.'; upd((d) => { d.users.push({ id: nid(d), ...f, rights: f.role === 'operator' ? ['edit', 'approve'] : [], active: true, lease_until: '', last_login: '' }); }); return null; },
    updUser(id, f) { upd((d) => { Object.assign(d.users.find((u) => u.id === id), f); }); },
    setPassword(id, password) { upd((d) => { d.users.find((u) => u.id === id).password = password; }); },
    delUser(id) { upd((d) => { d.users = d.users.filter((u) => u.id !== id); }); },
    saveSettings(f) { upd((d) => { Object.assign(d.settings, f); }); },
    resetDemo() { setDb(buildMock()); },
  };

  const val = { db, stats, session, rights, can: (r) => rights.includes(r), a, drawer, openDrawer: setDrawer, closeDrawer: () => setDrawer(null), att, openAtt: (etype, eid) => setAtt({ etype, eid }), closeAtt: () => setAtt(null), toast };
  return <Ctx.Provider value={val}>{children}</Ctx.Provider>;
}
