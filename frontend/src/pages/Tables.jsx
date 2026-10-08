import React, { useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead, StatusBadge, AttBtn, Modal, lei, formObj } from '../components/ui-kit';
import { STATUS_KEYS, STATUS_META } from '../mock';

function TableCard({ name, testid, headers, rows, render, filter }) {
  const [q, setQ] = useState('');
  const list = rows.filter((r) => !q.trim() || r._s.toLowerCase().includes(q.trim().toLowerCase())).filter(filter || (() => true));
  return (
    <div className="card table-card" data-testid={testid}>
      <div className="table-head-bar">
        <div className="search"><Icon name="search" /><input placeholder="Caută..." value={q} onChange={(e) => setQ(e.target.value)} data-testid={`${name}-table-search`} /></div>
        <span className="count">{list.length} înregistrări</span>
      </div>
      <div className="table-scroll">
        <table className="tbl">
          <thead><tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>{list.map(render)}</tbody>
        </table>
      </div>
    </div>
  );
}
const lab = (headers) => headers.map((h) => h);

export function Locuri() {
  const { db, openDrawer } = useApp();
  const [f, setF] = useState('toate');
  const counts = Object.fromEntries(STATUS_KEYS.map((k) => [k, db.plots.filter((p) => p.status === k).length]));
  const rows = db.plots.map((p) => ({ ...p, _s: `${p.cod_loc} ${p.localizare} ${p.tip}` }));
  const H = ['Cod loc', 'Localizare', 'Tip', 'Status'];
  return (
    <div data-testid="locuri-page">
      <PageHead title="Locuri de veci" sub="Registrul complet al locurilor de veci din cimitir.">
        <button className="btn-outline" data-testid="export-excel" onClick={() => exportCsv(db.plots)}><Icon name="file-spreadsheet" /> Export Excel</button>
      </PageHead>
      <div className="filter-bar">
        <button className={`chip ${f === 'toate' ? 'active' : ''}`} onClick={() => setF('toate')} data-testid="filter-toate">Toate ({db.plots.length})</button>
        {STATUS_KEYS.map((k) => <button key={k} className={`chip ${f === k ? 'active' : ''}`} onClick={() => setF(k)} data-testid={`filter-${k}`}>{STATUS_META[k].label} ({counts[k]})</button>)}
      </div>
      <TableCard name="locuri" testid="locuri-table" headers={lab(H)} rows={rows} filter={(r) => f === 'toate' || r.status === f}
        render={(p) => (
          <tr key={p.cod_loc} className="clickable" onClick={() => openDrawer(p.cod_loc)} data-testid={`locuri-row-${p.cod_loc}`}>
            <td className="mono strong" data-label={H[0]}>{p.cod_loc}</td><td className="muted" data-label={H[1]}>{p.localizare || '—'}</td>
            <td data-label={H[2]}>{p.tip || '—'}</td><td data-label={H[3]}><StatusBadge status={p.status} /></td>
          </tr>
        )} />
    </div>
  );
}

function exportCsv(plots) {
  const head = ['cod_loc', 'sector', 'parcela', 'rand', 'numar', 'tip', 'status'];
  const csv = [head.join(';'), ...plots.map((p) => head.map((h) => p[h]).join(';'))].join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'locuri.csv'; a.click();
}

export function Inhumati() {
  const { db, openDrawer } = useApp();
  const H = ['Nume', 'Loc de veci', 'Data deces', 'Data înhumare', 'Religie', ''];
  const rows = db.inhumati.map((r) => ({ ...r, nume_complet: `${r.nume} ${r.prenume}`.trim(), _s: `${r.nume} ${r.prenume} ${r.cod_loc} ${r.cnp}` }));
  return (
    <div data-testid="inhumati-page">
      <PageHead title="Înhumați" sub="Registrul persoanelor înhumate. Click pentru fișa locului." />
      <TableCard name="inhumati" testid="inhumati-table" headers={H} rows={rows} render={(r) => (
        <tr key={r.id} className={r.cod_loc ? 'clickable' : ''} onClick={() => r.cod_loc && openDrawer(r.cod_loc)}>
          <td className="strong" data-label={H[0]}>{r.nume_complet || '—'}</td><td className="mono" data-label={H[1]}>{r.cod_loc || '—'}</td>
          <td className="mono" data-label={H[2]}>{r.data_deces || '—'}</td><td className="mono" data-label={H[3]}>{r.data_inhumare || '—'}</td>
          <td data-label={H[4]}>{r.religie || '—'}</td><td className="att-cell"><AttBtn etype="inhumat" eid={r.id} /></td>
        </tr>
      )} />
    </div>
  );
}

export function Contracte() {
  const { db, openDrawer } = useApp();
  const H = ['Nr. contract', 'Loc', 'Concesionar', 'Data', 'Expiră', ''];
  const rows = db.contracte.map((c) => ({ ...c, _s: `${c.numar_contract} ${c.cod_loc} ${c.concesionar_nume}` }));
  return (
    <div data-testid="contracte-page">
      <PageHead title="Contracte de concesiune" sub="Toate contractele. Click pentru fișa locului." />
      <TableCard name="contracte" testid="contracte-table" headers={H} rows={rows} render={(c) => (
        <tr key={c.id} className={c.cod_loc ? 'clickable' : ''} onClick={() => c.cod_loc && openDrawer(c.cod_loc)}>
          <td className="mono strong" data-label={H[0]}>{c.numar_contract || '—'}</td><td className="mono" data-label={H[1]}>{c.cod_loc || '—'}</td>
          <td data-label={H[2]}>{c.concesionar_nume || '—'}</td><td className="mono" data-label={H[3]}>{c.data_contract || '—'}</td>
          <td className="mono" data-label={H[4]}>{c.data_sfarsit ? <span className="expira">{c.data_sfarsit}</span> : '—'}</td>
          <td className="att-cell"><AttBtn etype="contract" eid={c.id} /></td>
        </tr>
      )} />
    </div>
  );
}

export function Plati() {
  const { db, openDrawer, stats } = useApp();
  const H = ['Chitanță', 'Loc', 'Sumă', 'Data', 'Tip', ''];
  const rows = db.plati.map((r) => ({ ...r, _s: `${r.chitanta_nr} ${r.cod_loc} ${r.tip}` }));
  return (
    <div data-testid="plati-page">
      <PageHead title="Plăți & chitanțe" sub="Evidența încasărilor." />
      <div className="total-card"><div className="tc-left"><Icon name="wallet" /><span>Total încasat</span></div><span className="tc-val mono" data-testid="plati-total">{lei(stats.total_incasari)}</span></div>
      <TableCard name="plati" testid="plati-table" headers={H} rows={rows} render={(r) => (
        <tr key={r.id} className={r.cod_loc ? 'clickable' : ''} onClick={() => r.cod_loc && openDrawer(r.cod_loc)}>
          <td className="mono" data-label={H[0]}>{r.chitanta_nr || '—'}</td><td className="mono" data-label={H[1]}>{r.cod_loc || '—'}</td>
          <td className="mono strong" data-label={H[2]}>{lei(r.suma)}</td><td className="mono" data-label={H[3]}>{r.data_plata}</td>
          <td data-label={H[4]}>{r.tip || '—'}</td><td className="att-cell"><AttBtn etype="plata" eid={r.id} /></td>
        </tr>
      )} />
    </div>
  );
}

export function Concesionari() {
  const { db, a, toast } = useApp();
  const [modal, setModal] = useState(null);
  const H = ['Nume', 'CNP', 'Telefon', 'Email', ''];
  const rows = db.concesionari.map((r) => ({ ...r, _s: `${r.nume} ${r.prenume} ${r.cnp} ${r.telefon} ${r.email}` }));
  const fields = [['nume', 'Nume'], ['prenume', 'Prenume'], ['cnp', 'CNP'], ['bi_ci', 'Serie/nr. CI']];
  const save = (e) => { const f = formObj(e); a.saveConcesionar(f); setModal(null); toast.success(f.id ? 'Concesionar actualizat' : 'Concesionar adăugat'); };
  React_useCloseModals(() => setModal(null));
  return (
    <div data-testid="concesionari-page">
      <PageHead title="Concesionari" sub="Titularii contractelor de concesiune.">
        <button className="btn-primary" onClick={() => setModal({})} data-testid="add-conces-btn"><Icon name="plus" /> Concesionar nou</button>
      </PageHead>
      <TableCard name="concesionari" testid="concesionari-table" headers={H} rows={rows} render={(r) => (
        <tr key={r.id}>
          <td className="strong" data-label={H[0]}>{`${r.nume} ${r.prenume}`.trim() || '—'}</td><td className="mono" data-label={H[1]}>{r.cnp || '—'}</td>
          <td className="mono" data-label={H[2]}>{r.telefon || '—'}</td><td data-label={H[3]}>{r.email || '—'}</td>
          <td className="row-actions">
            <AttBtn etype="concesionar" eid={r.id} />
            <button title="Editează" onClick={() => setModal(r)} data-testid={`edit-conces-${r.id}`}><Icon name="pencil" /></button>
            <button className="del" onClick={() => { if (window.confirm('Ștergi concesionarul?')) { a.delConcesionar(r.id); toast.success('Concesionar șters'); } }} data-testid={`del-conces-${r.id}`}><Icon name="trash-2" /></button>
          </td>
        </tr>
      )} />
      <Modal open={!!modal} title={modal?.id ? 'Editează concesionar' : 'Concesionar nou'} onClose={() => setModal(null)} testid="conces-dialog">
        <form className="form-grid" onSubmit={save}>
          <input type="hidden" name="id" defaultValue={modal?.id || ''} />
          {fields.map(([k, l]) => <div key={k}><label>{l}</label><input name={k} defaultValue={modal?.[k] || ''} data-testid={`conces-${k}`} /></div>)}
          <div className="col2"><label>Adresă</label><input name="adresa" defaultValue={modal?.adresa || ''} data-testid="conces-adresa" /></div>
          <div><label>Telefon</label><input name="telefon" defaultValue={modal?.telefon || ''} data-testid="conces-telefon" /></div>
          <div><label>Email</label><input name="email" defaultValue={modal?.email || ''} data-testid="conces-email" /></div>
          <div className="col2 modal-foot"><button type="button" className="btn-ghost" onClick={() => setModal(null)}>Anulează</button><button type="submit" className="btn-primary" data-testid="conces-save" style={{ marginTop: 0 }}>Salvează</button></div>
        </form>
      </Modal>
    </div>
  );
}

function React_useCloseModals(fn) {
  React.useEffect(() => { document.addEventListener('close-modals', fn); return () => document.removeEventListener('close-modals', fn); });
}
