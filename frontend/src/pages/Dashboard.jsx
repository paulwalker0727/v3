import React from 'react';
import { Link } from 'react-router-dom';
import { useApp } from '../store';
import { Icon, PageHead, lei, CerereStatus } from '../components/ui-kit';
import { STATUS_KEYS, STATUS_META } from '../mock';

export default function Dashboard() {
  const { stats: s } = useApp();
  let off = 0;
  return (
    <div data-testid="dashboard-page">
      <PageHead title="Panou principal" sub="Situația generală a cimitirului și activitatea recentă." />
      <div className="kpi-grid">
        <Link to="/admin/locuri" className="kpi" data-testid="kpi-locuri">
          <div className="kpi-top"><span className="kpi-label">Locuri de veci</span><span className="kpi-ic"><Icon name="grid-3x3" /></span></div>
          <div className="kpi-val">{s.total_locuri}</div>
          <div className="kpi-sub">{s.status_counts.liber} libere · {s.status_counts.ocupat} ocupate</div>
        </Link>
        <div className="kpi" data-testid="kpi-ocupare">
          <div className="kpi-top"><span className="kpi-label">Grad de ocupare</span><span className="kpi-ic"><Icon name="trending-up" /></span></div>
          <div className="kpi-val">{s.grad_ocupare}%</div>
          <div className="kpi-bar"><span style={{ width: `${s.grad_ocupare}%` }} /></div>
        </div>
        <Link to="/admin/plati" className="kpi" data-testid="kpi-incasari">
          <div className="kpi-top"><span className="kpi-label">Total încasări</span><span className="kpi-ic gold"><Icon name="wallet" /></span></div>
          <div className="kpi-val mono" style={{ fontSize: 26 }}>{lei(s.total_incasari)}</div>
          <div className="kpi-sub">{s.nr_plati} plăți înregistrate</div>
        </Link>
        <Link to="/admin/contracte" className="kpi" data-testid="kpi-expira">
          <div className="kpi-top"><span className="kpi-label">Contracte expiră (30 zile)</span><span className={`kpi-ic ${s.contracte_expira > 0 ? 'danger' : ''}`}><Icon name="alert-triangle" /></span></div>
          <div className={`kpi-val ${s.contracte_expira > 0 ? 'text-danger' : ''}`}>{s.contracte_expira}</div>
          <div className="kpi-sub">{s.contracte_expira > 0 ? 'Necesită atenție' : 'Totul în regulă'}</div>
        </Link>
      </div>

      <div className="dash-grid">
        <div className="card">
          <h3 className="card-title">Distribuție status</h3>
          <div className="donut-wrap">
            <svg viewBox="0 0 42 42" className="donut">
              {STATUS_KEYS.map((k) => {
                const pct = s.total_locuri ? ((s.status_counts[k] || 0) / s.total_locuri) * 100 : 0;
                const el = <circle key={k} className="donut-seg" cx="21" cy="21" r="15.915" fill="none" stroke={STATUS_META[k].fill} strokeWidth="6" strokeDasharray={`${pct} ${100 - pct}`} strokeDashoffset={(100 - off) % 100} />;
                off += pct; return el;
              })}
            </svg>
            <div className="donut-center"><div><div className="donut-num">{s.total_locuri}</div><div className="donut-lbl">locuri</div></div></div>
          </div>
          <div className="legend">
            {STATUS_KEYS.map((k) => <div className="legend-item" key={k}><span className="dot" style={{ background: STATUS_META[k].fill }} />{STATUS_META[k].label}<b>{s.status_counts[k] || 0}</b></div>)}
          </div>
        </div>

        <div className="dash-mid">
          <div className="card" data-testid="panel-expiring">
            <div className="card-head"><h3 className="card-title" style={{ marginBottom: 0 }}>Contracte în expirare</h3><Link to="/admin/contracte" className="see-all">Vezi tot <Icon name="arrow-right" /></Link></div>
            {s.expiring.length ? s.expiring.slice(0, 5).map((c) => (
              <div className="rowline" key={c.id}><div><div className="mono strong">{c.numar_contract || c.cod_loc}</div><div className="muted-xs">Loc {c.cod_loc || '—'}</div></div><span className="expira mono">{c._expira}</span></div>
            )) : <p className="empty">Niciun contract în expirare.</p>}
          </div>
          <div className="card" data-testid="panel-cereri">
            <div className="card-head"><h3 className="card-title" style={{ marginBottom: 0 }}>Cereri online recente {s.cereri_noi > 0 && <span className="mini-badge">{s.cereri_noi}</span>}</h3><Link to="/admin/cereri" className="see-all">Vezi tot <Icon name="arrow-right" /></Link></div>
            {s.recent_cereri.length ? s.recent_cereri.map((c) => (
              <div className="rowline" key={c.id}><div><div className="strong">{`${c.nume} ${c.prenume}`.trim()}</div><div className="muted-xs">{c.cod_loc || 'fără loc'} · {c.created_at}</div></div><CerereStatus status={c.status} /></div>
            )) : <p className="empty">Nicio cerere online.</p>}
          </div>
        </div>

        <div className="card" data-testid="panel-inhumari">
          <h3 className="card-title"><Icon name="users" /> Înhumări recente</h3>
          {s.recent_inhumati.length ? s.recent_inhumati.map((x) => (
            <div className="rowline" key={x.id}><div><div className="strong">{`${x.nume} ${x.prenume}`.trim()}</div><div className="muted-xs">{x.data_inhumare || x.data_deces || ''}</div></div></div>
          )) : <p className="empty">—</p>}
        </div>
      </div>
    </div>
  );
}
