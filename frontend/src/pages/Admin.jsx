import React, { useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead } from '../components/ui-kit';
import { Denied } from './Utilizatori';

export function Setari() {
  const { db, session, a, can, toast } = useApp();
  const [newKey, setNewKey] = useState('');
  const cfg = db.settings; const dis = !can('manage_users');
  const save = (e) => { e.preventDefault(); a.saveSettings(Object.fromEntries(new FormData(e.target).entries())); toast.success('Setări salvate'); };
  const L = ({ t }) => <label className="lbl">{t}</label>;
  const Sel = ({ name, opts, testid }) => <select name={name} defaultValue={cfg[name]} disabled={dis} data-testid={testid}>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>;
  const Inp = ({ name, testid, ...r }) => <input name={name} defaultValue={cfg[name]} disabled={dis} data-testid={testid} {...r} />;
  return (
    <div data-testid="setari-page" className="narrow">
      <PageHead title="Setări" sub="Configurarea cimitirului activ." />
      <div className="card">
        <h3 className="card-title"><Icon name="database" /> Baza de date activă</h3>
        <div className="db-active"><div className="db-ic sqlite"><Icon name="hard-drive" /></div><div><div className="strong">{session.source.name}</div><div className="mono muted-xs">SQLite · {session.source.id}</div></div></div>
      </div>
      <div className="card">
        <h3 className="card-title">Configurare cimitir și portal</h3>
        <form onSubmit={save} data-testid="setari-form">
          <L t="Nume afișat" /><Inp name="cimitir_nume" testid="setari-nume" />
          <L t="Depunere cereri online" /><Sel name="portal_requests_enabled" opts={[['1', 'Activă'], ['0', 'Oprită']]} testid="setari-requests" />
          <L t="Acces MAIN fără internet (ore, reînnoit la fiecare sincronizare)" /><Inp name="offline_lease_hours" type="number" min="1" max="720" testid="setari-lease" />
          <L t="Cetățenii trebuie să aibă cont pentru a depune cereri" /><Sel name="portal_require_account" opts={[['1', 'Da'], ['0', 'Nu']]} testid="setari-require-account" />
          <L t="Notificări e-mail către cetățeni (aprobare / respingere)" /><Sel name="notify_email_enabled" opts={[['1', 'Activate'], ['0', 'Oprite']]} testid="setari-notify-email" />
          <L t="Păstrare cereri aprobate (zile după închidere)" /><Inp name="retention_days_closed" type="number" min="1" max="3650" testid="setari-ret-closed" />
          <L t="Păstrare cereri respinse (zile)" /><Inp name="retention_days_rejected" type="number" min="1" max="3650" testid="setari-ret-rejected" />
          <L t="Informare privind prelucrarea datelor (șablon — de aprobat de operator/juriști)" />
          <textarea name="privacy_text" rows={6} defaultValue={cfg.privacy_text} disabled={dis} data-testid="setari-privacy" />
          <p className="muted-xs" style={{ marginTop: 6 }}>Monitorizare și jurnal operațiuni · Aceste setări nu reprezintă o declarație de conformitate GDPR.</p>
          <L t="Adresa publică a portalului (folosită în linkurile din e-mailuri), ex: https://portal.exemplu.ro" /><Inp name="public_base_url" testid="setari-public-url" />
          <L t={'E-mailuri pentru alerte „MAIN offline” (separate prin virgulă, maxim 5)'} /><Inp name="alert_emails" testid="setari-alert-emails" />
          <L t="Furnizor e-mail (mock nu trimite nimic; smtp folosește SMTP_* din mediul serverului)" /><Sel name="email_provider" opts={[['mock', 'mock (nu trimite)'], ['smtp', 'smtp']]} testid="setari-email-provider" />
          <L t="Copii de rezervă automate" /><Sel name="backup_enabled" opts={[['1', 'Activate (zilnic)'], ['0', 'Oprite']]} testid="setari-backup-enabled" />
          <L t="Folder copii de rezervă (gol = implicit)" /><Inp name="backup_dir" testid="setari-backup-dir" />
          <L t="Păstrează copiile (zile)" /><Inp name="backup_keep_days" type="number" min="1" max="365" testid="setari-backup-keep" />
          {!dis ? <button type="submit" className="btn-primary" data-testid="setari-save"><Icon name="save" /> Salvează</button> : <p className="muted-xs" style={{ marginTop: 12 }}>Doar administratorii pot modifica setările.</p>}
        </form>
      </div>
      <div className="card" data-testid="sync-card">
        <h3 className="card-title">Sincronizare MAIN ↔ server</h3>
        <p className="muted-xs">Mod: <b>online</b> · versiune hartă publicată: <b data-testid="sync-map-version">{cfg.sync_map_version}</b> · ultima primire: {cfg.sync_last_push_at || '—'}</p>
        {!dis && <button className="btn-primary" type="button" onClick={() => { setNewKey('sk_' + Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')); toast.success('Cheie nouă generată'); }} data-testid="sync-key-btn">Generează cheie nouă de sincronizare</button>}
        {newKey && <><p data-testid="sync-key-value" className="mono" style={{ marginTop: 10, wordBreak: 'break-all' }}>{newKey}</p><p className="muted-xs">Se afișează o singură dată. Se introduce în MAIN → Sincronizare cloud. Cheia veche nu mai funcționează.</p></>}
      </div>
    </div>
  );
}

const Pill = ({ tone, children, testid }) => {
  const c = { ok: ['#dcfce7', '#166534'], err: ['#fee2e2', '#991b1b'], warn: ['#fef3c7', '#92400e'] }[tone];
  return <span data-testid={testid} style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 999, fontSize: 13, fontWeight: 600, background: c[0], color: c[1] }}>{children}</span>;
};

export function System() {
  const { can, stats, db, toast } = useApp();
  const [res, setRes] = useState(null);
  if (!can('manage_users')) return <Denied />;
  const run = (label, msg) => { setRes({ label, busy: true }); setTimeout(() => setRes({ label, msg }), 600); };
  const tiles = [
    ['Server', <Pill tone="ok" testid="sys-server">ONLINE</Pill>, 'mod online'],
    ['Motor bază de date', <span data-testid="sys-db-engine">SQLite</span>, 'schemă: 5 · local (demo)'],
    ['PostgreSQL', <Pill tone="warn" testid="sys-postgres">NEUTILIZAT (SQLite)</Pill>, ''],
    ['Ultima sincronizare', <span className="mono" style={{ fontSize: 15 }} data-testid="sys-last-sync">{db.settings.sync_last_push_at}</span>, '1 client(i) MAIN · 0 offline'],
    ['Operații pending în outbox', <span data-testid="sys-outbox">0</span>, 'raportate de MAIN: 0'],
    ['Ultimul backup', <span className="mono" style={{ fontSize: 15 }} data-testid="sys-last-backup">ieri 02:00</span>, 'cimitir-backup.zip · 184 KB'],
    ['Dimensiune bază de date', <span data-testid="sys-db-size">{(168 + stats.total_locuri / 4).toFixed(0)} KB</span>, 'citită prin metode fixe, în siguranță'],
    ['Versiune aplicație', <span data-testid="sys-version">1.0.0</span>, ''],
    ['Storage', <Pill tone="ok" testid="sys-storage">ONLINE</Pill>, 'driver: local'],
    ['Email SMTP', <Pill tone="err" testid="sys-smtp">NECONFIGURAT</Pill>, 'furnizor activ: mock'],
    ['Erori (ultimele 24 h)', <span data-testid="sys-errors">0</span>, '7 zile: 0'],
  ];
  return (
    <div>
      <PageHead title="Sistem" sub="Pagină tehnică, vizibilă doar administratorilor. Nu afișează parole sau chei și nu permite SQL din browser." />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(250px,1fr))', gap: 14, margin: '14px 0' }} data-testid="sys-status">
        {tiles.map(([k, v, s]) => (
          <div key={k} style={{ border: '1px solid #e2e8f0', borderRadius: 10, padding: 14, background: '#fff' }}>
            <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase', letterSpacing: '.04em' }}>{k}</div>
            <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>{v}</div><div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{s}</div>
          </div>
        ))}
      </div>
      <div className="card">
        <h3 className="card-title">Verificări producție</h3>
        <p style={{ margin: '6px 0' }}><Pill tone="warn" testid="sys-prod-level">SQLite local</Pill> <Pill tone="warn" testid="sys-prod-ready">NU ESTE GATA PENTRU PRODUCȚIE</Pill></p>
        <p className="muted-xs">Doar „PG cloud + persistență confirmată” este considerat pregătit pentru producție.</p>
      </div>
      <div className="card">
        <h3 className="card-title">Acțiuni sigure</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }} data-testid="sys-actions">
          <button className="btn-ghost" onClick={() => run('Test DB', 'Conexiune DB: OK (2 ms)')} data-testid="btn-test-db">Test conexiune DB</button>
          <button className="btn-ghost" onClick={() => run('Test storage', 'Storage local: scriere/citire OK')} data-testid="btn-test-storage">Test storage</button>
          <button className="btn-primary" style={{ marginTop: 0 }} onClick={() => { if (window.confirm('Creezi acum o copie de rezervă?')) { run('Backup', 'Backup creat: cimitir-backup.zip'); toast.success('Backup creat (simulat)'); } }} data-testid="btn-backup-now">Backup acum</button>
          <button className="btn-ghost" onClick={() => run('Loguri', '[INFO] server pornit\n[INFO] sincronizare MAIN ok\n[INFO] backup zilnic finalizat')} data-testid="btn-view-logs">Vezi loguri</button>
          <button className="btn-ghost" onClick={() => run('Sincronizare', 'Sincronizare verificată: 0 conflicte, 0 operații pending')} data-testid="btn-check-sync">Verifică sincronizarea</button>
        </div>
      </div>
      {res && <div className="card" data-testid="sys-result-area"><h3 className="card-title">{res.label}</h3>{res.busy ? <p className="muted">Se execută…</p> : <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{res.msg}</pre>}</div>}
    </div>
  );
}

export function Cloud() {
  const { can, toast } = useApp();
  const [res, setRes] = useState('');
  if (!can('manage_users')) return <Denied />;
  return (
    <div className="narrow" data-testid="cloud-page">
      <PageHead title="Configurare Cloud" sub="Conexiune PostgreSQL și storage Supabase. Secretele nu sunt afișate niciodată înapoi." />
      <div className="info-banner"><Icon name="shield-alert" /> Pagină demo: valorile nu sunt trimise nicăieri și nu se scrie niciun fișier .env.</div>
      <div className="card">
        <h3 className="card-title"><Icon name="cloud" /> Supabase / PostgreSQL</h3>
        <form onSubmit={(e) => { e.preventDefault(); toast.success('Configurație salvată (simulat)'); }}>
          <label className="lbl">DATABASE_URL</label><input type="password" placeholder="postgresql://… (necompletat)" data-testid="cloud-db-url" />
          <label className="lbl" style={{ marginTop: 10 }}>SUPABASE_URL</label><input placeholder="https://proiect.supabase.co" data-testid="cloud-url" />
          <label className="lbl" style={{ marginTop: 10 }}>SUPABASE_SECRET_KEY</label><input type="password" placeholder="sb_secret_…" data-testid="cloud-key" />
          <label className="lbl" style={{ marginTop: 10 }}>Bucket</label><input defaultValue="cimitir-documente" data-testid="cloud-bucket" />
          <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
            <button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="cloud-save"><Icon name="save" /> Salvează</button>
            <button type="button" className="btn-ghost" onClick={() => setRes('PostgreSQL: DATABASE_URL gol — neconfigurat')} data-testid="cloud-test-pg">Testează PostgreSQL</button>
            <button type="button" className="btn-ghost" onClick={() => setRes('Storage: cheie lipsă — neconfigurat')} data-testid="cloud-test-storage">Testează storage</button>
          </div>
        </form>
        {res && <p className="muted-xs" style={{ marginTop: 12 }} data-testid="cloud-result">{res}</p>}
      </div>
    </div>
  );
}
