import React, { useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead, AttBtn, CerereStatus } from '../components/ui-kit';

export default function Cereri() {
  const { db, a, can, toast } = useApp();
  const [tab, setTab] = useState('');
  const [openId, setOpenId] = useState(null);
  const [notes, setNotes] = useState({});
  const rows = db.cereri.filter((c) => !tab || c.status === tab);
  const tabs = [['', 'Toate'], ['nou', 'Noi'], ['validat', 'Validate'], ['respins', 'Respinse']];
  const decide = (c, status) => {
    const msg = status === 'validat' ? 'Aprobi cererea? Se creează concesionarul și un contract draft, iar locul devine rezervat.' : 'Respingi cererea?';
    if (!window.confirm(msg)) return;
    if (status === 'respins' && !(notes[c.id] || '').trim()) { toast.error('Motivul respingerii este obligatoriu.'); return; }
    a.decideCerere(c.id, status, notes[c.id]); toast.success(status === 'validat' ? 'Cerere aprobată' : 'Cerere respinsă');
  };
  return (
    <div data-testid="cereri-page">
      <PageHead title="Cereri online" sub="Cererile de concesionare primite din portalul public." />
      <div className="filter-bar">
        {tabs.map(([k, l]) => <button key={k || 'toate'} className={`chip ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)} data-testid={`cereri-tab-${k || 'toate'}`}>{l}</button>)}
      </div>
      {rows.length === 0 ? <div className="empty-box"><Icon name="inbox" /> Nicio cerere.</div> : (
        <div className="cerere-list">
          {rows.map((c) => (
            <React.Fragment key={c.id}>
              <div className="cerere-card" onClick={() => setOpenId(openId === c.id ? null : c.id)} data-testid={`cerere-card-${c.id}`}>
                <div className="cerere-main">
                  <div className="cerere-name">{`${c.nume} ${c.prenume}`.trim()} <CerereStatus status={c.status} /></div>
                  <div className="muted-xs">#{c.id} · {c.created_at}{c.cod_loc ? ` · Loc ${c.cod_loc}` : ''}{c.grad_rudenie ? ` · ${c.grad_rudenie}` : ''}</div>
                </div>
                {c.documents?.length > 0 && <span className="doc-count"><Icon name="file-text" />{c.documents.length}</span>}
              </div>
              <div className={`cerere-detail ${openId === c.id ? 'open' : ''}`} id={`cerere-${c.id}`}>
                {[['Loc solicitat', 'cod_loc'], ['CNP', 'cnp'], ['Serie/nr. CI', 'bi_ci'], ['Adresă', 'adresa'], ['Telefon', 'telefon'], ['Email', 'email'], ['Observații', 'observatii']].map(([l, k]) => <div className="kv" key={k}><span>{l}</span><b>{c[k] || '—'}</b></div>)}
                {c.documents?.length > 0 && <div className="doc-strip">{c.documents.map((d) => <button key={d.id} className="doc-pill" onClick={() => toast.info('Document demo — previzualizare indisponibilă')}><Icon name="file-text" />{d.caption || 'Doc'}</button>)}</div>}
                <AttBtn etype="cerere" eid={c.id} label="Documente cerere" />
                {(c.status === 'nou' || c.status === 'completari') && (can('approve') ? (
                  <div className="cerere-decide" data-testid={`cerere-decide-${c.id}`}>
                    <input maxLength={500} placeholder="Notă / motiv (opțional la aprobare)" value={notes[c.id] || ''} onChange={(e) => setNotes({ ...notes, [c.id]: e.target.value })} data-testid={`cerere-note-${c.id}`} />
                    <button className="btn-ok" onClick={() => decide(c, 'validat')} data-testid={`cerere-approve-${c.id}`}><Icon name="check" /> Aprobă</button>
                    <button className="btn-no" onClick={() => decide(c, 'respins')} data-testid={`cerere-reject-${c.id}`}><Icon name="x" /> Respinge</button>
                  </div>
                ) : <div className="info-banner" style={{ marginTop: 10 }} data-testid={`cerere-no-right-${c.id}`}><Icon name="lock" /> Aprobarea cererilor necesită dreptul „approve”.</div>)}
                {c.review_note && <div className="kv"><span>Notă evaluare</span><b>{c.review_note}</b></div>}
              </div>
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
