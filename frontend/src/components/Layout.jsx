import React, { useEffect, useState } from 'react';
import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom';
import { useApp } from '../store';
import { Icon } from './ui-kit';
import Drawer from './Drawer';
import AttSheet from './AttSheet';

export default function Layout() {
  const { session, can, stats, a, closeDrawer, closeAtt } = useApp();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);

  useEffect(() => { setOpen(false); window.scrollTo(0, 0); }, [pathname]);
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') { closeDrawer(); closeAtt(); document.dispatchEvent(new CustomEvent('close-modals')); } };
    document.addEventListener('keydown', h); return () => document.removeEventListener('keydown', h);
  }, [closeDrawer, closeAtt]);

  if (!session) return <Navigate to="/admin/access" replace />;
  const cereriNoi = stats.cereri_noi;
  const item = (href, label, icon, active, badge = 0) => {
    const slug = href.replace('/admin', '').replace(/^\/|\/$/g, '') || 'dashboard';
    return (
      <NavLink end key={href} to={href} className={`nav-item ${active ? 'active' : ''}`} data-testid={`nav-${slug}`}>
        <Icon name={icon} /><span>{label}</span>{badge > 0 && <span className="nav-badge">{badge}</span>}
      </NavLink>
    );
  };
  const is = (p) => pathname === p || pathname === p + '/';
  const adm = can('manage_users');

  return (
    <>
      <div className="app-shell">
        <aside className={`sidebar ${open ? 'open' : ''}`} id="sidebar">
          <div className="sidebar-brand">
            <div className="crest">✦</div>
            <div className="brand-txt"><div className="brand-title">Admin Cimitire</div><div className="brand-sub">Primărie</div></div>
            <button className="sidebar-close" onClick={() => setOpen(false)}><Icon name="x" /></button>
          </div>
          <nav className="sidebar-nav">
            {item('/admin', 'Panou principal', 'layout-dashboard', is('/admin'))}
            {item('/admin/cauta', 'Căutare', 'search', is('/admin/cauta'))}
            {item('/admin/harta', 'Hartă cimitir', 'map', is('/admin/harta'))}
            {item('/admin/locuri', 'Locuri de veci', 'grid-3x3', is('/admin/locuri'))}
            {item('/admin/inhumati', 'Înhumați', 'users', is('/admin/inhumati'))}
            {item('/admin/contracte', 'Contracte', 'file-signature', is('/admin/contracte'))}
            {item('/admin/concesionari', 'Concesionari', 'user-circle', is('/admin/concesionari'))}
            {item('/admin/plati', 'Plăți & chitanțe', 'wallet', is('/admin/plati'))}
            {item('/admin/cereri', 'Cereri online', 'inbox', is('/admin/cereri'), cereriNoi)}
            {item('/admin/rapoarte', 'Rapoarte', 'bar-chart-3', is('/admin/rapoarte'))}
            {adm && item('/admin/utilizatori', 'Utilizatori', 'users', is('/admin/utilizatori'))}
            {adm && item('/admin/system', 'Sistem', 'server', pathname.startsWith('/admin/system'))}
            {adm && item('/admin/cloud', 'Configurare Cloud', 'cloud', pathname.startsWith('/admin/cloud'))}
            {item('/admin/setari', 'Setări', 'settings', is('/admin/setari'))}
          </nav>
          <div className="sidebar-foot">
            <div className="db-chip">
              <Icon name="hard-drive" />
              <div><div className="db-name">{session.source.name}</div><div className="db-eng">SQLite</div></div>
            </div>
            <NavLink to="/admin/access" className="switch-db" data-testid="switch-db"><Icon name="database" /> Schimbă baza de date</NavLink>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <button className="menu-btn" onClick={() => setOpen((o) => !o)}><Icon name="menu" /></button>
            <span className="db-pill"><Icon name="hard-drive" /> {session.source.name}</span>
            {stats.contracte_expira > 0 && <span className="alert-pill" data-testid="expiring-alert"><Icon name="bell" /> {stats.contracte_expira} contracte expiră</span>}
            <div className="topbar-right">
              <NavLink to="/portal" target="_blank" className="portal-link" data-testid="portal-link"><Icon name="external-link" /> Portal public</NavLink>
              <div className="user-chip">
                <div className="avatar">{session.username.slice(0, 2).toUpperCase()}</div>
                <div className="user-meta"><div className="uname">{session.username}</div><div className="urole">{session.role}</div></div>
                <button type="button" className="logout" onClick={() => a.logout()} data-testid="logout-button" title="Ieșire"><Icon name="log-out" /></button>
              </div>
            </div>
          </header>
          <main className="content"><Outlet /></main>
        </div>
      </div>

      <AttSheet />
      <nav className="bottom-nav" data-testid="bottom-nav" aria-label="Navigare principală">
        <NavLink to="/admin/cauta" className={is('/admin/cauta') ? 'active' : ''} data-testid="bn-cauta"><Icon name="search" /><span>Caută</span></NavLink>
        <NavLink to="/admin/harta" className={is('/admin/harta') ? 'active' : ''} data-testid="bn-harta"><Icon name="map" /><span>Hartă</span></NavLink>
        <NavLink to="/admin/locuri" className={is('/admin/locuri') ? 'active' : ''} data-testid="bn-locuri"><Icon name="grid-3x3" /><span>Locuri</span></NavLink>
        <NavLink to="/admin/cereri" className={is('/admin/cereri') ? 'active' : ''} data-testid="bn-cereri"><Icon name="inbox" /><span>Cereri</span>{cereriNoi > 0 && <b className="bn-badge">{cereriNoi}</b>}</NavLink>
        <button type="button" onClick={() => setOpen(true)} data-testid="bn-meniu"><Icon name="menu" /><span>Meniu</span></button>
      </nav>
      <Drawer />
    </>
  );
}
