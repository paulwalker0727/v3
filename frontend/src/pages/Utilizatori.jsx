import React, { useEffect, useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead, Modal, formObj } from '../components/ui-kit';

export const Denied = () => <div className="info-banner warn"><Icon name="lock" /> Acces permis doar administratorilor.</div>;

export default function Utilizatori() {
  const { db, a, can, toast } = useApp();
  const [newOpen, setNewOpen] = useState(false);
  const [pw, setPw] = useState(null);
  useEffect(() => { const h = () => { setNewOpen(false); setPw(null); }; document.addEventListener('close-modals', h); return () => document.removeEventListener('close-modals', h); }, []);
  if (!can('manage_users')) return <Denied />;
  const upd = (e, u) => {
    e.preventDefault(); const fd = new FormData(e.target);
    a.updUser(u.id, { role: fd.get('role'), rights: fd.getAll('rights'), active: fd.get('active') === '1' }); toast.success(`Utilizator ${u.username} actualizat`);
  };
  return (
    <div data-testid="utilizatori-page">
      <PageHead title="Utilizatori" sub="Administrarea conturilor și rolurilor.">
        <button className="btn-primary" onClick={() => setNewOpen(true)} data-testid="add-user-btn"><Icon name="plus" /> Utilizator nou</button>
      </PageHead>
      <div className="info-banner"><Icon name="shield-alert" /> <span>Roluri: <b>admin</b> (toate drepturile), <b>operator</b> (consultare + editare + aprobare, ajustabile), <b>user</b> (doar consultare). Conturile dezactivate sau revocate pierd accesul online imediat.</span></div>
      <div className="card table-card" data-testid="users-table">
        <div className="table-scroll">
          <table className="tbl">
            <thead><tr><th>Utilizator</th><th>Rol · drepturi · stare</th><th>Acces MAIN offline</th><th>Ultima autentificare</th><th></th></tr></thead>
            <tbody>
              {db.users.map((r) => (
                <tr key={r.id + JSON.stringify([r.role, r.rights, r.active])}>
                  <td className="strong" data-label="Utilizator">{r.username}</td>
                  <td data-label="Rol">
                    <form onSubmit={(e) => upd(e, r)} style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }} data-testid={`update-user-${r.id}`}>
                      <select name="role" defaultValue={r.role} style={{ width: 'auto', minWidth: 110 }} data-testid={`role-user-${r.id}`}>{['admin', 'operator', 'user'].map((ro) => <option key={ro}>{ro}</option>)}</select>
                      {['edit', 'approve'].map((x) => <label className="muted-xs" key={x} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><input type="checkbox" name="rights" value={x} defaultChecked={r.rights.includes(x)} style={{ width: 'auto', minHeight: 0 }} data-testid={`right-${x}-${r.id}`} /> {x}</label>)}
                      <label className="muted-xs" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><input type="checkbox" name="active" value="1" defaultChecked={r.active} style={{ width: 'auto', minHeight: 0 }} data-testid={`active-user-${r.id}`} /> activ</label>
                      <button type="submit" className="btn-ghost" data-testid={`save-user-${r.id}`}>Salvează</button>
                    </form>
                  </td>
                  <td className="mono" data-label="Acces MAIN offline">{r.lease_until ? `valabil până ${r.lease_until}` : 'fără limită (cont local)'}</td>
                  <td className="mono" data-label="Ultima autentificare">{r.last_login || '—'}</td>
                  <td className="row-actions">
                    <button title="Schimbă parola" onClick={() => setPw(r)} data-testid={`pw-user-${r.id}`}><Icon name="key-round" /></button>
                    <button className="del" onClick={() => { if (r.username === 'admin') { toast.error('Contul admin nu poate fi șters.'); return; } if (window.confirm(`Ștergi utilizatorul ${r.username}?`)) { a.delUser(r.id); toast.success('Utilizator șters'); } }} data-testid={`del-user-${r.id}`}><Icon name="trash-2" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={newOpen} title="Utilizator nou" onClose={() => setNewOpen(false)} testid="user-dialog">
        <form className="form-col" onSubmit={(e) => { const f = formObj(e); const err = a.addUser(f); if (err) toast.error(err); else { toast.success('Utilizator creat'); setNewOpen(false); } }}>
          <div><label>Utilizator</label><input name="username" data-testid="user-username" required /></div>
          <div><label>Parolă</label><input name="password" type="password" minLength={6} data-testid="user-password" required /></div>
          <div><label>Rol</label><select name="role" data-testid="user-role"><option value="operator">Operator</option><option value="admin">Administrator</option><option value="user">Consultare</option></select></div>
          <div className="modal-foot"><button type="button" className="btn-ghost" onClick={() => setNewOpen(false)}>Anulează</button><button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="user-save">Creează</button></div>
        </form>
      </Modal>
      <Modal open={!!pw} title={`Schimbă parola · ${pw?.username || ''}`} onClose={() => setPw(null)} testid="pw-dialog">
        <form className="form-col" onSubmit={(e) => { const f = formObj(e); a.setPassword(pw.id, f.password); toast.success('Parolă schimbată'); setPw(null); }}>
          <input type="password" name="password" placeholder="Parolă nouă" minLength={6} data-testid="new-password" required />
          <div className="modal-foot"><button type="button" className="btn-ghost" onClick={() => setPw(null)}>Anulează</button><button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="pw-save">Salvează</button></div>
        </form>
      </Modal>
    </div>
  );
}
