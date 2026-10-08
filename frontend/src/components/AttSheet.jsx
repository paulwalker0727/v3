import React, { useRef, useState } from 'react';
import { useApp } from '../store';
import { Icon } from './ui-kit';

const LABELS = { loc: 'Loc de veci', concesionar: 'Concesionar', contract: 'Contract', inhumat: 'Înhumat', plata: 'Plată', aviz: 'Aviz', cerere: 'Cerere' };

export function AttPanel({ etype, eid }) {
  const { db, a, toast } = useApp();
  const [cat, setCat] = useState('General');
  const [err, setErr] = useState('');
  const fileRef = useRef(null); const camRef = useRef(null);
  const items = db.attachments.filter((x) => x.etype === etype && String(x.eid) === String(eid));
  const onFile = (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    if (f.size > 10 * 1024 * 1024) { setErr('Fișierul depășește 10 MB.'); return; }
    setErr(''); a.addAtt(etype, eid, { name: f.name, category: cat, size: f.size > 1048576 ? `${(f.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(f.size / 1024))} KB` });
    toast.success('Fișier atașat'); e.target.value = '';
  };
  const groups = items.reduce((m, x) => { (m[x.category] = m[x.category] || []).push(x); return m; }, {});
  return (
    <div className="att-panel" data-testid="att-panel">
      <div className="att-head"><span className="att-ent">{LABELS[etype] || etype} · {eid}</span><span className="att-count">{items.length}</span></div>
      {err && <div className="att-error" role="alert"><Icon name="alert-triangle" /> {err}</div>}
      <div className="att-form">
        <div className="att-fields">
          <label>Categorie
            <select value={cat} onChange={(e) => setCat(e.target.value)} data-testid="att-category">
              {['General', 'Plan', 'Act identitate', 'Certificat deces', 'Contract', 'Chitanță', 'Fotografie'].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        </div>
        <div className="att-actions">
          <button type="button" className="att-pick" onClick={() => fileRef.current.click()} data-testid="att-pick"><Icon name="upload" /> Alege fișier</button>
          <button type="button" className="att-pick att-camera" onClick={() => camRef.current.click()} data-testid="att-camera"><Icon name="camera" /> Fotografiază</button>
        </div>
        <input ref={fileRef} type="file" hidden onChange={onFile} data-testid="att-file-input" />
        <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={onFile} />
      </div>
      {items.length === 0 && <p className="empty">Niciun fișier atașat.</p>}
      {Object.entries(groups).map(([g, list]) => (
        <div key={g}>
          <div className="att-group-title">{g}</div>
          {list.map((x) => (
            <div className="att-item" key={x.id}>
              <div className="att-thumb"><Icon name="file-text" /></div>
              <div className="att-meta"><span className="att-name">{x.name}</span><span className="muted-xs">{x.size}</span></div>
              <button type="button" className="att-dl" title="Descarcă" onClick={() => toast.info('Descărcare simulată (date demo)')}><Icon name="download" /></button>
              <button type="button" className="att-del" title="Șterge" onClick={() => { a.delAtt(x.id); toast.success('Fișier șters'); }} data-testid={`att-del-${x.id}`}><Icon name="trash-2" /></button>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function AttSheet() {
  const { att, closeAtt } = useApp();
  return (
    <>
      <div className={`sheet-overlay ${att ? 'show' : ''}`} onClick={closeAtt} />
      <div className={`sheet ${att ? 'show' : ''}`} data-testid="att-sheet" role="dialog" aria-modal="true" aria-label="Atașamente">
        <div className="sheet-bar"><strong>Atașamente{att ? ` · ${LABELS[att.etype] || att.etype}` : ''}</strong><button type="button" onClick={closeAtt} aria-label="Închide" data-testid="att-close"><Icon name="x" /></button></div>
        <div className="sheet-body">{att && <AttPanel etype={att.etype} eid={att.eid} />}</div>
      </div>
    </>
  );
}
