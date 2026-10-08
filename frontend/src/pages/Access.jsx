import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp, SOURCES } from '../store';
import { Icon } from '../components/ui-kit';

export default function Access() {
  const { db, session, a, toast } = useApp();
  const nav = useNavigate();
  const [error, setError] = useState('');
  const [newName, setNewName] = useState('');
  const isAdmin = session?.role === 'admin';
  const stat = { locuri: db.plots.length, ocupate: db.plots.filter((p) => p.status === 'ocupat').length, contracte: db.contracte.length };

  const login = (e, id) => {
    e.preventDefault(); const f = new FormData(e.target);
    const err = a.login(id, f.get('username'), f.get('password'));
    if (err) setError(err); else { setError(''); nav('/admin'); }
  };

  return (
    <div className="access-body" data-testid="access-screen">
      <div className="access-bg" />
      <header className="access-head">
        <div className="crest big">✦</div>
        <div><div className="access-title">Administrare Cimitire</div><div className="access-sub">Sistem integrat · Primărie</div></div>
      </header>
      <main className="access-main">
        <div className="access-inner">
          <div className="access-badge"><Icon name="database" /> Pasul 1 · Selectează baza de date</div>
          <h1 className="access-h1">Ce bază de date deschidem?</h1>
          <p className="access-lead">Alege cimitirul (fișier SQLite, partajat cu aplicația desktop) și autentifică-te.</p>
          {error && <div className="access-error" data-testid="login-error">{error}</div>}

          <div className="db-grid">
            {SOURCES.map((s) => (
              <div key={s.id} className={`db-card ${s.available ? '' : 'disabled'}`} data-testid={`database-card-${s.id}`}>
                <div className="db-card-top">
                  <div className="db-ic sqlite"><Icon name="hard-drive" /></div>
                  <span className="db-tag">SQLite</span>
                </div>
                <div className="db-card-name">{s.name}</div>
                <div className="db-card-id">{s.id}</div>
                {s.available ? (
                  <>
                    <div className="db-stats">
                      <div><span>{stat.locuri}</span><small>Locuri</small></div>
                      <div><span>{stat.ocupate}</span><small>Ocupate</small></div>
                      <div><span>{stat.contracte}</span><small>Contracte</small></div>
                    </div>
                    <form className="db-login" onSubmit={(e) => login(e, s.id)}>
                      <input name="username" placeholder="Utilizator" autoComplete="username" data-testid={`login-username-${s.id}`} required />
                      <input name="password" type="password" placeholder="Parolă" autoComplete="current-password" data-testid={`login-password-${s.id}`} required />
                      <button type="submit" className="btn-gold" data-testid={`login-submit-${s.id}`}><Icon name="shield-check" /> Autentificare</button>
                    </form>
                  </>
                ) : <div className="db-unavail">{s.error || 'Indisponibil'}</div>}
              </div>
            ))}

            {isAdmin && (
              <>
                <form className="db-card ghost" onSubmit={(e) => { e.preventDefault(); toast.success(`Cimitir „${newName}” creat (simulat)`); setNewName(''); }}>
                  <div className="ghost-ic"><Icon name="plus" /></div>
                  <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Nume cimitir nou" data-testid="new-db-name" required />
                  <button type="submit" className="btn-outline-light" data-testid="new-db-create">Adaugă cimitir</button>
                </form>
                <form className="db-card ghost" onSubmit={(e) => { e.preventDefault(); toast.success('Import simulat'); }}>
                  <div className="ghost-ic"><Icon name="upload" /></div>
                  <div className="ghost-label">Importă fișier .db</div>
                  <input type="file" accept=".db" data-testid="import-db-input" required />
                  <button type="submit" className="btn-outline-light" data-testid="import-db-btn">Importă</button>
                </form>
              </>
            )}
          </div>
          <p className="access-hint">Demo: <b>admin / admin123</b> · operator / operator123 · user / user123 (date simulate, salvate în browser)</p>
        </div>
      </main>
    </div>
  );
}
