import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useApp } from '../store';
import { Icon, StatusBadge, formObj } from '../components/ui-kit';

export default function Portal() {
  const { db, a, toast } = useApp();
  const [q, setQ] = useState('');
  const [sent, setSent] = useState(null);
  const t = q.trim().toLowerCase();
  const res = t.length < 2 ? [] : db.inhumati.filter((x) => `${x.nume} ${x.prenume} ${x.cod_loc}`.toLowerCase().includes(t)).slice(0, 12);
  const locuri = t.length < 2 ? [] : db.plots.filter((p) => p.cod_loc.toLowerCase().includes(t)).slice(0, 6);
  const libere = db.plots.filter((p) => p.status === 'liber');
  const enabled = db.settings.portal_requests_enabled === '1';

  const submit = (e) => {
    const f = formObj(e);
    if (!f.gdpr) { toast.error('Trebuie să accepți prelucrarea datelor.'); return; }
    const id = a.addCerere(f); setSent(id); e.target.reset(); toast.success('Cererea a fost trimisă');
  };

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)' }} data-testid="portal-page">
      <header style={{ background: 'var(--sidebar)', color: '#fff', padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div className="crest big">✦</div>
        <div><div className="access-title">{db.settings.cimitir_nume}</div><div className="access-sub">Portal public · Primărie</div></div>
        <Link to="/admin/access" className="portal-link" style={{ marginLeft: 'auto', color: 'rgba(255,255,255,.7)' }}><Icon name="shield-check" /> Acces personal</Link>
      </header>
      <main style={{ maxWidth: 900, margin: '0 auto', padding: '28px 16px 60px' }}>
        <h1 className="page-title">Căutare loc de veci</h1>
        <p className="page-sub" style={{ marginBottom: 16 }}>Aflați locația unei persoane înhumate sau starea unui loc de veci.</p>
        <div className="search-big"><Icon name="search" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nume sau cod loc (ex: A-1-2-3)…" data-testid="portal-search" /></div>
        {t.length >= 2 && res.length === 0 && locuri.length === 0 && <p className="empty">Niciun rezultat.</p>}
        {res.map((x) => <div className="res-card" key={x.id}><div><div className="strong">{x.nume} {x.prenume}</div><div className="muted-xs">Loc {x.cod_loc} · înhumat {x.data_inhumare}</div></div></div>)}
        {locuri.map((p) => <div className="res-card" key={p.cod_loc}><div><div className="strong mono">{p.cod_loc}</div><div className="muted-xs">{p.localizare}</div></div><StatusBadge status={p.status} /></div>)}

        <div className="card" style={{ marginTop: 28 }}>
          <h3 className="card-title"><Icon name="file-plus" /> Cerere online de concesionare</h3>
          {!enabled ? <div className="info-banner warn"><Icon name="lock" /> Depunerea cererilor online este momentan oprită.</div> : sent ? (
            <div className="att-notice" data-testid="portal-sent"><Icon name="check" /> Cererea #{sent} a fost înregistrată. Veți fi contactat după evaluare.</div>
          ) : (
            <form className="form-grid" onSubmit={submit} data-testid="portal-form">
              {[['nume', 'Nume *'], ['prenume', 'Prenume *'], ['cnp', 'CNP'], ['bi_ci', 'Serie/nr. CI'], ['telefon', 'Telefon *'], ['email', 'Email']].map(([k, l]) => <div key={k}><label>{l}</label><input name={k} required={l.includes('*')} data-testid={`portal-${k}`} /></div>)}
              <div className="col2"><label>Adresă</label><input name="adresa" /></div>
              <div><label>Loc dorit</label><select name="cod_loc" data-testid="portal-loc"><option value="">— fără preferință —</option>{libere.map((p) => <option key={p.cod_loc}>{p.cod_loc}</option>)}</select></div>
              <div><label>Grad de rudenie</label><input name="grad_rudenie" /></div>
              <div className="col2"><label>Observații</label><textarea name="observatii" rows={3} /></div>
              <label className="chk col2"><input type="checkbox" name="gdpr" value="1" data-testid="portal-gdpr" /> <span className="muted-xs">{db.settings.privacy_text}</span></label>
              <div className="col2 modal-foot"><button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="portal-submit"><Icon name="check" /> Trimite cererea</button></div>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
