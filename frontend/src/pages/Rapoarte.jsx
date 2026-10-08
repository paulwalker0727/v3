import React, { useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead, lei } from '../components/ui-kit';
import { STATUS_KEYS, STATUS_META, parseRo } from '../mock';

const PAGE = 8;
export default function Rapoarte() {
  const { db, stats, toast } = useApp();
  const [pdf, setPdf] = useState({ start: '', end: '' });
  const [draft, setDraft] = useState({ sector: '', start: '', end: '' });
  const [flt, setFlt] = useState({ sector: '', start: '', end: '' });
  const [page, setPage] = useState(1);
  const sectorsList = [...new Set(db.plots.map((p) => p.sector))].sort();
  const sectors = sectorsList.map((s) => { const o = { sector: `Sector ${s}` }; STATUS_KEYS.forEach((k) => { o[k] = db.plots.filter((p) => p.sector === s && p.status === k).length; }); return o; });
  const tot = (s) => STATUS_KEYS.reduce((n, k) => n + s[k], 0);
  const maxtot = Math.max(1, ...sectors.map(tot));
  const plotOf = (cod) => db.plots.find((p) => p.cod_loc === cod);
  const useRange = flt.start || flt.end;
  const all = (useRange ? db.contracte.map((c) => ({ ...c, _d: parseRo(c.data_sfarsit), _expira: c.data_sfarsit })).filter((c) => c._d) : stats.expiring)
    .map((c) => ({ ...c, _sector: plotOf(c.cod_loc)?.sector }))
    .filter((c) => !flt.sector || c._sector === flt.sector)
    .filter((c) => !flt.start || c._d >= new Date(flt.start)).filter((c) => !flt.end || c._d <= new Date(flt.end))
    .sort((a, b) => a._d - b._d);
  const pages = Math.max(1, Math.ceil(all.length / PAGE)); const cur = Math.min(page, pages);
  const list = all.slice((cur - 1) * PAGE, cur * PAGE);
  const sim = (n) => toast.info(`${n} — generare PDF simulată (date demo)`);

  return (
    <div data-testid="rapoarte-page">
      <PageHead title="Rapoarte" sub="Analize și exporturi oficiale.">
        <button className="btn-outline" data-testid="report-export-excel" onClick={() => sim('Export Excel')}><Icon name="file-spreadsheet" /> Export Excel complet</button>
      </PageHead>

      <div className="card" data-testid="pdf-reports-card">
        <h3 className="card-title">Documente PDF</h3>
        <form className="report-filters" style={{ margin: 0 }} data-testid="pdf-fin-form" onSubmit={(e) => { e.preventDefault(); sim('Raport financiar'); }}>
          <div className="report-filter-field"><label htmlFor="pdf-start">De la</label><input id="pdf-start" type="date" value={pdf.start} onChange={(e) => setPdf({ ...pdf, start: e.target.value })} data-testid="pdf-start" /></div>
          <div className="report-filter-field"><label htmlFor="pdf-end">Până la</label><input id="pdf-end" type="date" value={pdf.end} onChange={(e) => setPdf({ ...pdf, end: e.target.value })} data-testid="pdf-end" /></div>
          <div className="report-filter-actions" style={{ flexWrap: 'wrap' }}>
            <button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="pdf-financiar"><Icon name="file-down" /> Raport financiar</button>
            <button type="button" className="btn-ghost" onClick={() => sim('Registru înhumări')} data-testid="pdf-registru"><Icon name="file-down" /> Registru înhumări</button>
            <button type="button" className="btn-ghost" onClick={() => sim('Expirări 30 zile')} data-testid="pdf-expirari-30"><Icon name="file-down" /> Contracte care expiră (30 zile)</button>
            <button type="button" className="btn-ghost" onClick={() => sim('Expirări 90 zile')} data-testid="pdf-expirari-90"><Icon name="file-down" /> (90 zile)</button>
          </div>
        </form>
      </div>

      <div className="summary-grid">
        <div className="card summary"><div className="kpi-top"><span className="kpi-label">Grad de ocupare</span><span className="kpi-ic"><Icon name="building-2" /></span></div><div className="kpi-val">{stats.grad_ocupare}%</div><div className="kpi-sub">{stats.total_locuri} locuri totale</div></div>
        <div className="card summary"><div className="kpi-top"><span className="kpi-label">Total încasări</span><span className="kpi-ic gold"><Icon name="trending-up" /></span></div><div className="kpi-val mono" style={{ fontSize: 26 }}>{lei(stats.total_incasari)}</div><div className="kpi-sub">{stats.nr_plati} plăți</div></div>
        <div className="card summary"><div className="kpi-top"><span className="kpi-label">Contracte expiră</span><span className={`kpi-ic ${stats.contracte_expira > 0 ? 'danger' : ''}`}><Icon name="alert-triangle" /></span></div><div className={`kpi-val ${stats.contracte_expira > 0 ? 'text-danger' : ''}`}>{stats.contracte_expira}</div><div className="kpi-sub">în următoarele 30 zile</div></div>
      </div>

      <form className="report-filters" data-testid="report-filter-form" onSubmit={(e) => { e.preventDefault(); setFlt(draft); setPage(1); }}>
        <div className="report-filter-field"><label htmlFor="report-sector">Sector</label>
          <select id="report-sector" value={draft.sector} onChange={(e) => setDraft({ ...draft, sector: e.target.value })} data-testid="report-sector-filter"><option value="">Toate sectoarele</option>{sectorsList.map((s) => <option key={s} value={s}>Sector {s}</option>)}</select></div>
        <div className="report-filter-field"><label htmlFor="report-start">Expiră de la</label><input id="report-start" type="date" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} data-testid="report-start-date" /></div>
        <div className="report-filter-field"><label htmlFor="report-end">Expiră până la</label><input id="report-end" type="date" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} data-testid="report-end-date" /></div>
        <div className="report-filter-actions">
          <button type="submit" className="btn-primary" style={{ marginTop: 0 }} data-testid="report-apply-filters"><Icon name="filter" /> Aplică</button>
          <button type="button" className="btn-ghost" onClick={() => { const z = { sector: '', start: '', end: '' }; setDraft(z); setFlt(z); setPage(1); }} data-testid="report-reset-filters">Resetează</button>
        </div>
      </form>

      <div className="card">
        <h3 className="card-title">Raport ocupare pe sectoare</h3>
        <div className="bars">
          {sectors.map((s) => (
            <div className="bar-row" key={s.sector}>
              <div className="bar-label">{s.sector}</div>
              <div className="bar-track">{STATUS_KEYS.map((k) => s[k] > 0 && <div key={k} className="bar-seg" style={{ width: `${(s[k] / maxtot) * 100}%`, background: STATUS_META[k].fill }} title={`${STATUS_META[k].label}: ${s[k]}`} />)}</div>
              <div className="bar-total mono">{tot(s)}</div>
            </div>
          ))}
        </div>
        <div className="legend inline">{STATUS_KEYS.map((k) => <div className="legend-item" key={k}><span className="dot" style={{ background: STATUS_META[k].fill }} />{STATUS_META[k].label}</div>)}</div>
      </div>

      <div className="card">
        <div className="card-head"><h3 className="card-title" style={{ marginBottom: 0 }}>Contracte în expirare (pentru notificări)</h3><span className="count" data-testid="report-expiring-count">{all.length} rezultate</span></div>
        {all.length ? (
          <>
            <div className="table-scroll"><table className="tbl" data-testid="report-expiring-table">
              <thead><tr><th>Nr. contract</th><th>Loc</th><th>Sector</th><th>Expiră</th></tr></thead>
              <tbody>{list.map((c) => <tr key={c.id}><td className="mono" data-label="Nr. contract">{c.numar_contract || '—'}</td><td className="mono" data-label="Loc">{c.cod_loc}</td><td data-label="Sector">{c._sector ? `Sector ${c._sector}` : '—'}</td><td className="mono expira" data-label="Expiră">{c._expira}</td></tr>)}</tbody>
            </table></div>
            {pages > 1 && (
              <nav className="pagination" data-testid="report-pagination">
                {cur > 1 && <button className="page-nav" onClick={() => setPage(cur - 1)} data-testid="report-page-previous"><Icon name="chevron-left" /> Înapoi</button>}
                <span className="page-current" data-testid="report-current-page">Pagina {cur} din {pages}</span>
                {cur < pages && <button className="page-nav" onClick={() => setPage(cur + 1)} data-testid="report-page-next">Înainte <Icon name="chevron-right" /></button>}
              </nav>
            )}
          </>
        ) : <p className="empty">Niciun contract în expirare.</p>}
      </div>
    </div>
  );
}
