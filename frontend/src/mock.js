// Mock data for "Administrare Cimitire" (frontend-only). Will be replaced by backend later.
export const STATUS_KEYS = ['liber', 'ocupat', 'rezervat', 'expirat'];
export const STATUS_META = {
  liber: { label: 'Liber', fill: '#10b981', stroke: '#059669' },
  ocupat: { label: 'Ocupat', fill: '#ef4444', stroke: '#dc2626' },
  rezervat: { label: 'Rezervat', fill: '#f59e0b', stroke: '#d97706' },
  expirat: { label: 'Expirat', fill: '#64748b', stroke: '#475569' },
};

const pad = (n) => String(n).padStart(2, '0');
export const fmtRo = (d) => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
export const parseRo = (s) => {
  if (!s) return null;
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(String(s).trim());
  return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null;
};
export const addDays = (n) => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + n); return d; };
export const todayRo = () => fmtRo(new Date());
export const nowStamp = () => { const d = new Date(); return `${fmtRo(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function rng(seed) { let s = seed; return () => { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; }; }
const NUME = ['Popescu', 'Ionescu', 'Dumitrescu', 'Stoica', 'Gheorghe', 'Constantin', 'Marin', 'Radu', 'Tudor', 'Munteanu', 'Barbu', 'Dinu', 'Lungu', 'Neagu', 'Preda', 'Ilie', 'Matei', 'Cristea'];
const PM = ['Ion', 'Gheorghe', 'Vasile', 'Constantin', 'Nicolae', 'Mihai', 'Alexandru', 'Marin', 'Dumitru', 'Florin'];
const PF = ['Maria', 'Elena', 'Ana', 'Ioana', 'Florica', 'Georgeta', 'Viorica', 'Aurelia', 'Stela', 'Cornelia'];
const STRAZI = ['Str. Libertății', 'Str. Mihai Eminescu', 'Bd. Unirii', 'Str. Florilor', 'Str. Republicii', 'Str. Școlii', 'Str. Victoriei'];
const RELIGII = ['Ortodoxă', 'Ortodoxă', 'Ortodoxă', 'Romano-catolică', 'Greco-catolică'];
const TIPURI = ['Simplu', 'Dublu', 'Familial', 'Cavou'];

export function buildMock() {
  const r = rng(20261005);
  const pick = (a) => a[Math.floor(r() * a.length)];
  const digits = (n) => Array.from({ length: n }, () => Math.floor(r() * 10)).join('');
  let seq = 1000;
  const nid = () => ++seq;

  // concesionari
  const concesionari = Array.from({ length: 14 }, (_, i) => {
    const f = i % 2 === 1; const nume = NUME[i % NUME.length]; const prenume = f ? pick(PF) : pick(PM);
    return { id: i + 1, nume, prenume, cnp: (f ? '2' : '1') + digits(12), bi_ci: pick(['AB', 'CJ', 'MS', 'B']) + ' ' + digits(6),
      adresa: `${pick(STRAZI)} nr. ${1 + Math.floor(r() * 80)}`, telefon: '07' + digits(8), email: `${prenume}.${nume}@exemplu.ro`.toLowerCase() };
  });

  // locuri (harta)
  const sectors = [];
  const plots = [];
  ['A', 'B', 'C', 'D'].forEach((S, si) => {
    const ox = (si % 2) * 130; const oy = Math.floor(si / 2) * 100;
    sectors.push({ sector: S, points: [[ox - 6, oy - 8], [ox + 108, oy - 8], [ox + 108, oy + 62], [ox - 6, oy + 62]] });
    for (let row = 0; row < 3; row++) for (let col = 0; col < 6; col++) {
      const parcela = col < 3 ? 1 : 2; const numar = (col % 3) + 1; const rand = row + 1;
      const x = ox + col * 16 + (col >= 3 ? 6 : 0); const y = oy + row * 20;
      const p = r(); const status = p < 0.33 ? 'liber' : p < 0.74 ? 'ocupat' : p < 0.86 ? 'rezervat' : 'expirat';
      plots.push({ cod_loc: `${S}-${parcela}-${rand}-${numar}`, sector: S, parcela, rand, numar, tip: pick(TIPURI), categorie: pick(['I', 'II', 'III']),
        capacitate: pick([1, 2, 2, 4]), suprafata: pick([2.5, 5, 5, 8]), nr_vechi: '', numar_cf: status === 'liber' ? '' : String(10000 + Math.floor(r() * 80000)),
        stare: pick(['Bună', 'Bună', 'Satisfăcătoare']), disponibilitate: status === 'liber' ? 'Disponibil' : 'Indisponibil', observatii: '', status,
        localizare: `Sector ${S} · Parcela ${parcela} · Rând ${rand}`, points: [[x, y], [x + 13, y], [x + 13, y + 17], [x, y + 17]] });
    }
  });

  // contracte / inhumati / plati
  const contracte = []; const inhumati = []; const plati = []; let ctr = 100; let chit = 2000; let soonLeft = 4;
  plots.forEach((p) => {
    if (p.status === 'ocupat' || p.status === 'expirat') {
      const c = concesionari[Math.floor(r() * concesionari.length)];
      const start = new Date(2005 + Math.floor(r() * 18), Math.floor(r() * 12), 1 + Math.floor(r() * 27));
      let end;
      if (p.status === 'expirat') end = addDays(-(30 + Math.floor(r() * 800)));
      else if (soonLeft > 0 && r() < 0.35) { end = addDays(3 + Math.floor(r() * 25)); soonLeft--; }
      else end = new Date(2028 + Math.floor(r() * 18), Math.floor(r() * 12), 1 + Math.floor(r() * 27));
      ctr++;
      contracte.push({ id: nid(), numar_contract: `${ctr}/${start.getFullYear()}`, cod_loc: p.cod_loc, concesionar_id: c.id,
        concesionar_nume: `${c.nume} ${c.prenume}`, data_contract: fmtRo(start), data_sfarsit: fmtRo(end) });
      plati.push({ id: nid(), cod_loc: p.cod_loc, suma: pick([450, 600, 800, 1200, 1500]), data_plata: fmtRo(start), tip: 'Taxă concesiune', chitanta_nr: `CH-${++chit}` });
      const n = r() < 0.3 ? 2 : 1;
      for (let k = 0; k < n; k++) {
        const f = r() < 0.5; const dec = new Date(1999 + Math.floor(r() * 26), Math.floor(r() * 12), 1 + Math.floor(r() * 27));
        const inh = new Date(dec); inh.setDate(inh.getDate() + 3);
        const nas = new Date(dec.getFullYear() - (55 + Math.floor(r() * 40)), Math.floor(r() * 12), 1 + Math.floor(r() * 27));
        inhumati.push({ id: nid(), cod_loc: p.cod_loc, nume: c.nume, prenume: f ? pick(PF) : pick(PM), cnp: (f ? '2' : '1') + digits(12),
          data_nasterii: fmtRo(nas), data_deces: fmtRo(dec), data_inhumare: fmtRo(inh), religie: pick(RELIGII), nr_certificat_deces: `${digits(5)}/${dec.getFullYear()}` });
        if (k === 0) plati.push({ id: nid(), cod_loc: p.cod_loc, suma: pick([150, 250, 300]), data_plata: fmtRo(inh), tip: 'Taxă înhumare', chitanta_nr: `CH-${++chit}` });
      }
    }
  });

  const liberi = plots.filter((p) => p.status === 'liber' || p.status === 'rezervat').map((p) => p.cod_loc);
  const cereri = [
    ['Vasilescu', 'Andreea', 'nou', 'fiică'], ['Pârvu', 'Mihaela', 'nou', 'soție'], ['Ionescu', 'Adrian', 'nou', 'fiu'],
    ['Dobre', 'Elena', 'validat', 'soră'], ['Zamfir', 'Costin', 'respins', 'nepot'], ['Anghel', 'Lucia', 'validat', 'soție'],
  ].map(([nume, prenume, status, grad_rudenie], i) => {
    const d = addDays(-(i * 2 + 1));
    return { id: i + 1, nume, prenume, status, grad_rudenie, cod_loc: liberi[i % liberi.length], created_at: `${fmtRo(d)} 1${i}:2${i}`,
      cnp: '2' + digits(12), bi_ci: 'CJ ' + digits(6), adresa: `${pick(STRAZI)} nr. ${1 + i * 7}`, telefon: '07' + digits(8),
      email: `${prenume}.${nume}@exemplu.ro`.toLowerCase(), observatii: i % 2 ? 'Solicit rezervarea locului lângă familie.' : '',
      documents: [{ id: 5000 + i * 2, caption: 'Carte de identitate' }, { id: 5001 + i * 2, caption: 'Certificat deces' }].slice(0, i % 3 === 0 ? 2 : 1),
      review_note: status === 'respins' ? 'Documente incomplete.' : '' };
  });

  const users = [
    { id: 1, username: 'admin', password: 'admin123', role: 'admin', rights: ['edit', 'approve'], active: true, lease_until: '', last_login: nowStamp() },
    { id: 2, username: 'operator', password: 'operator123', role: 'operator', rights: ['edit', 'approve'], active: true, lease_until: fmtRo(addDays(20)), last_login: fmtRo(addDays(-2)) + ' 09:14' },
    { id: 3, username: 'maria.pop', password: 'maria12345', role: 'operator', rights: ['edit'], active: true, lease_until: '', last_login: fmtRo(addDays(-6)) + ' 15:40' },
    { id: 4, username: 'user', password: 'user123', role: 'user', rights: [], active: true, lease_until: '', last_login: '' },
  ];

  return {
    seq, plots, sectors, concesionari, contracte, inhumati, plati, cereri, users,
    avize: [{ id: nid(), cod_loc: plots.find((p) => p.status === 'ocupat').cod_loc, nr: 'AV-12', data: fmtRo(addDays(-40)), lucrare: 'Montare monument' }],
    attachments: [{ id: nid(), etype: 'loc', eid: plots.find((p) => p.status === 'ocupat').cod_loc, name: 'plan_amplasament.pdf', category: 'Plan', size: '412 KB' }],
    settings: { cimitir_nume: 'Cimitirul Central', portal_requests_enabled: '1', offline_lease_hours: '72', portal_require_account: '1', notify_email_enabled: '1',
      retention_days_closed: '365', retention_days_rejected: '90', privacy_text: 'Datele cu caracter personal sunt prelucrate de Primărie exclusiv pentru administrarea cimitirului. (Șablon — de aprobat de operator/juriști.)',
      public_base_url: '', alert_emails: '', email_provider: 'mock', backup_enabled: '1', backup_dir: '', backup_keep_days: '30', sync_map_version: 3, sync_last_push_at: fmtRo(addDays(-1)) + ' 02:00' },
  };
}
