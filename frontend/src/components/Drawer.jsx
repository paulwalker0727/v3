import React, { useEffect, useState } from 'react';
import { useApp } from '../store';
import { Icon, StatusBadge, AttBtn, lei, formObj } from './ui-kit';
import { todayRo } from '../mock';

const TABS = [['general', 'General'], ['inhumati', 'Înhumați'], ['contracte', 'Contracte'], ['plati', 'Plăți'], ['avize', 'Avize'], ['doc', 'Docs']];
const Empty = () => <p className="empty">Nicio înregistrare.</p>;
const F = ({ label, name, def = '', ...r }) => <div><label>{label}</label><input name={name} defaultValue={def} {...r} /></div>;

function General({ loc, conc }) {
  const { a, can, toast } = useApp();
  const fields = ['sector', 'parcela', 'rand', 'numar', 'tip', 'categorie', 'capacitate', 'suprafata', 'nr_vechi', 'numar_cf'];
  const save = (e) => { const f = formObj(e); if (!can('edit')) { toast.error('Nu aveți dreptul de editare.'); return; } a.saveLoc(loc.cod_loc, f); toast.success('Loc salvat'); };
  return (
    <>
      <form className="mini-card" onSubmit={save}>
        <div className="mini-head"><h4>Identificare & poziție</h4><button type="submit" className="btn-sm" data-testid="save-loc-btn"><Icon name="save" /> Salvează</button></div>
        <div className="grid2">
          {fields.map((f) => <F key={f} label={f.replace('_', ' ').replace(/^./, (c) => c.toUpperCase())} name={f} def={loc[f] ?? ''} data-testid={`field-${f}`} />)}
          <F label="Stare" name="stare" def={loc.stare} />
          <F label="Disponibilitate" name="disponibilitate" def={loc.disponibilitate} />
          <div className="col2"><label>Observații</label><input name="observatii" defaultValue={loc.observatii} /></div>
        </div>
      </form>
      <div className="mini-card">
        <h4>Concesionar</h4>
        {conc ? (
          <>
            <div className="kv"><span>Nume</span><b>{`${conc.nume} ${conc.prenume}`.trim()}</b></div>
            <div className="kv"><span>Telefon</span><b className="mono">{conc.telefon || '—'}</b></div>
            <div className="kv"><span>Email</span><b>{conc.email || '—'}</b></div>
            <div className="kv"><span>Adresă</span><b>{conc.adresa || '—'}</b></div>
          </>
        ) : <p className="empty">Niciun concesionar asociat.</p>}
      </div>
    </>
  );
}

function Inhumati({ cod, rows }) {
  const { a, toast } = useApp();
  const fields = [['nume', 'Nume'], ['prenume', 'Prenume'], ['cnp', 'CNP'], ['data_nasterii', 'Data nașterii'], ['data_deces', 'Data deces'], ['data_inhumare', 'Data înhumare'], ['religie', 'Religie'], ['nr_certificat_deces', 'Nr. certificat deces']];
  return (
    <>
      <details className="add-block"><summary data-testid="add-inhumati-btn"><Icon name="user-plus" /> Adaugă înhumat</summary>
        <form className="grid2 add-form" onSubmit={(e) => { const f = formObj(e); if (!f.nume.trim()) { toast.error('Numele este obligatoriu'); return; } a.addInhumat(cod, f); e.target.reset(); toast.success('Înhumat adăugat'); }}>
          {fields.map(([k, l]) => <div key={k}><label>{l}</label><input name={k} data-testid={`add-inhumati-${k}`} /></div>)}
          <div className="col2"><button type="submit" className="btn-sm" data-testid="submit-inhumati">Salvează</button></div>
        </form>
      </details>
      {rows.length === 0 && <Empty />}
      {rows.map((x) => (
        <div className="list-item" key={x.id}>
          <div><div className="strong">{`${x.nume} ${x.prenume}`.trim() || '—'}</div><div className="muted-xs">Deces: {x.data_deces || '—'} · Înhumare: {x.data_inhumare || '—'}</div></div>
          <AttBtn etype="inhumat" eid={x.id} />
          <button type="button" className="att-btn" onClick={() => toast.info('PDF simulat (date demo)')} data-testid={`pdf-adeverinta-${x.id}`}><Icon name="file-down" /><span>Adeverință</span></button>
          <button type="button" className="del" onClick={() => { a.delInhumat(x.id); toast.success('Înregistrare ștearsă'); }} data-testid={`del-inhumati-${x.id}`}><Icon name="trash-2" /></button>
        </div>
      ))}
    </>
  );
}

function Contracte({ cod, rows }) {
  const { a, can, db, toast } = useApp();
  const [error, setError] = useState('');
  const submit = (e) => { const f = formObj(e); const err = a.addContract(cod, f); setError(err || ''); if (!err) { toast.success('Contract salvat'); e.target.reset(); } };
  return (
    <>
      {error && <div className="att-error" data-testid="drawer-error" role="alert"><Icon name="alert-triangle" /> {error}</div>}
      {can('approve') ? (
        <details className="add-box" data-testid="contract-add-box" open={!!error}>
          <summary className="btn-sm" data-testid="contract-add-toggle"><Icon name="file-plus" /> Adaugă contract</summary>
          <form className="stack-form" data-testid="contract-form" onSubmit={submit}>
            <label>Nr. contract * <input name="numar_contract" required maxLength={60} data-testid="contract-numar" /></label>
            <label>Data contract <input name="data_contract" defaultValue={todayRo()} placeholder="zz.ll.aaaa" inputMode="numeric" data-testid="contract-data" /></label>
            <label>Valabil până la <input name="data_sfarsit" placeholder="zz.ll.aaaa (opțional)" inputMode="numeric" data-testid="contract-sfarsit" /></label>
            <label>Concesionar existent
              <select name="concesionar_id" data-testid="contract-concesionar"><option value="">— alege sau completează unul nou —</option>
                {db.concesionari.map((o) => <option key={o.id} value={o.id}>{`${o.nume} ${o.prenume}`}</option>)}</select></label>
            <div className="muted-xs">sau concesionar nou:</div>
            <label>Nume <input name="nume" data-testid="contract-nume" /></label>
            <label>Prenume <input name="prenume" /></label>
            <label>CNP <input name="cnp" inputMode="numeric" /></label>
            <label>Telefon <input name="telefon" inputMode="tel" /></label>
            <label>Adresă <input name="adresa" /></label>
            <button type="submit" className="btn-sm" data-testid="contract-save"><Icon name="save" /> Salvează contractul</button>
          </form>
        </details>
      ) : <div className="muted-xs" data-testid="contract-no-right"><Icon name="lock" /> Crearea contractelor necesită dreptul „approve”; aici pot fi consultate.</div>}
      {rows.length === 0 && <Empty />}
      {rows.map((x) => (
        <div className="list-item" key={x.id}>
          <div><div className="strong mono">{x.numar_contract || 'Contract'}</div><div className="muted-xs">{x.data_contract} {x.data_sfarsit ? `→ ${x.data_sfarsit}` : ''}</div></div>
          <AttBtn etype="contract" eid={x.id} />
          <button type="button" className="att-btn" onClick={() => toast.info('PDF simulat (date demo)')} data-testid={`pdf-contract-${x.id}`}><Icon name="file-down" /><span>PDF</span></button>
          <button type="button" className="del" onClick={() => { a.delContract(x.id); toast.success('Contract șters'); }}><Icon name="trash-2" /></button>
        </div>
      ))}
    </>
  );
}

function Plati({ cod, rows }) {
  const { a, toast } = useApp();
  return (
    <>
      <div className="total-line"><span>Total încasat</span><b className="mono">{lei(rows.reduce((s, x) => s + x.suma, 0))}</b></div>
      <details className="add-block"><summary><Icon name="wallet" /> Adaugă plată</summary>
        <form className="grid2 add-form" onSubmit={(e) => { const f = formObj(e); if (!f.suma) { toast.error('Suma este obligatorie'); return; } a.addPlata(cod, f); e.target.reset(); toast.success('Plată adăugată'); }}>
          {[['suma', 'Sumă (lei)'], ['data_plata', 'Data plată'], ['tip', 'Tip'], ['chitanta_nr', 'Nr. chitanță']].map(([k, l]) => <div key={k}><label>{l}</label><input name={k} /></div>)}
          <div className="col2"><button type="submit" className="btn-sm">Salvează</button></div>
        </form>
      </details>
      {rows.length === 0 && <Empty />}
      {rows.map((x) => (
        <div className="list-item" key={x.id}>
          <div><div className="strong mono">{lei(x.suma)}</div><div className="muted-xs">{x.data_plata} · {x.tip || 'plată'} {x.chitanta_nr ? `· chit. ${x.chitanta_nr}` : ''}</div></div>
          <AttBtn etype="plata" eid={x.id} />
          <button type="button" className="att-btn" onClick={() => toast.info('PDF simulat (date demo)')} data-testid={`pdf-chitanta-${x.id}`}><Icon name="file-down" /><span>Chitanță</span></button>
          <button type="button" className="del" onClick={() => { a.delPlata(x.id); toast.success('Plată ștearsă'); }}><Icon name="trash-2" /></button>
        </div>
      ))}
    </>
  );
}

function Avize({ cod, rows }) {
  const { a, toast } = useApp();
  return (
    <>
      <details className="add-block"><summary><Icon name="hammer" /> Aviz lucrare</summary>
        <form className="grid2 add-form" onSubmit={(e) => { a.addAviz(cod, formObj(e)); e.target.reset(); toast.success('Aviz adăugat'); }}>
          {[['nr', 'Nr. aviz'], ['data', 'Data'], ['lucrare', 'Lucrare']].map(([k, l]) => <div key={k}><label>{l}</label><input name={k} /></div>)}
          <div className="col2"><button type="submit" className="btn-sm">Salvează</button></div>
        </form>
      </details>
      {rows.length === 0 && <Empty />}
      {rows.map((x) => (
        <div className="list-item" key={x.id}>
          <div><div className="strong">{x.lucrare || 'Lucrare'}</div><div className="muted-xs">Aviz {x.nr || '—'} · {x.data || ''}</div></div>
          <AttBtn etype="aviz" eid={x.id} />
          <button type="button" className="del" onClick={() => { a.delAviz(x.id); toast.success('Aviz șters'); }}><Icon name="trash-2" /></button>
        </div>
      ))}
    </>
  );
}

export default function Drawer() {
  const { db, drawer, closeDrawer, toast } = useApp();
  const [tab, setTab] = useState('general');
  useEffect(() => { if (drawer) setTab('general'); }, [drawer]);
  const loc = drawer ? db.plots.find((p) => p.cod_loc === drawer) : null;
  const by = (k) => db[k].filter((x) => x.cod_loc === drawer);
  const contracte = loc ? by('contracte') : [];
  const conc = loc ? db.concesionari.find((c) => c.id === contracte[0]?.concesionar_id) : null;
  return (
    <>
      <div className={`drawer-overlay ${drawer ? 'show' : ''}`} onClick={closeDrawer} id="drawer-overlay" />
      <div className={`drawer ${drawer ? 'show' : ''}`} id="drawer" data-testid="drawer">
        <button className="drawer-x" onClick={closeDrawer} data-testid="drawer-close"><Icon name="x" /></button>
        {loc && (
          <div className="insp">
            <div className="insp-head">
              <div>
                <div className="insp-eyebrow">Loc de veci</div>
                <div className="insp-cod mono">{loc.cod_loc}</div>
                <div className="insp-status"><StatusBadge status={loc.status} /></div>
              </div>
              <button type="button" className="btn-light" onClick={() => toast.info('PDF simulat (date demo)')} data-testid="fisa-pdf-btn"><Icon name="file-down" /> Fișă PDF</button>
            </div>
            <div className="insp-tabs">
              {TABS.map(([k, l]) => <button key={k} className={`itab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)} data-testid={`tab-${k}`}>{l}</button>)}
            </div>
            <div className="insp-body">
              {tab === 'general' && <General key={loc.cod_loc} loc={loc} conc={conc} />}
              {tab === 'inhumati' && <Inhumati cod={loc.cod_loc} rows={by('inhumati')} />}
              {tab === 'contracte' && <Contracte cod={loc.cod_loc} rows={contracte} />}
              {tab === 'plati' && <Plati cod={loc.cod_loc} rows={by('plati')} />}
              {tab === 'avize' && <Avize cod={loc.cod_loc} rows={by('avize')} />}
              {tab === 'doc' && <AttInline cod={loc.cod_loc} />}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

import { AttPanel } from './AttSheet';
const AttInline = ({ cod }) => <div data-testid="loc-attachments"><AttPanel etype="loc" eid={cod} /></div>;
