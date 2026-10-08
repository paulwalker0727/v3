import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead } from '../components/ui-kit';
import { STATUS_KEYS, STATUS_META } from '../mock';

export default function Harta() {
  const { db, openDrawer } = useApp();
  const [filter, setFilter] = useState('toate');
  const [q, setQ] = useState('');
  const svgRef = useRef(null);
  const home = useMemo(() => {
    const pts = [...db.plots, ...db.sectors].flatMap((o) => o.points);
    const xs = pts.map((p) => p[0]); const ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = (maxX - minX + maxY - minY) * 0.04 + 12;
    return { x: minX - pad, y: minY - pad, w: maxX - minX + 2 * pad, h: maxY - minY + 2 * pad };
  }, [db.plots, db.sectors]);
  const [vb, setVb] = useState(home);
  const drag = useRef(null); const moved = useRef(false); const pts = useRef(new Map()); const lastDist = useRef(0);
  const vbRef = useRef(vb); vbRef.current = vb;

  const zoom = (f, cx, cy) => setVb((b) => { const mx = cx ?? b.x + b.w / 2; const my = cy ?? b.y + b.h / 2; return { x: mx - (mx - b.x) * f, y: my - (my - b.y) * f, w: b.w * f, h: b.h * f }; });
  useEffect(() => {
    const svg = svgRef.current;
    const onWheel = (e) => {
      e.preventDefault(); const r = svg.getBoundingClientRect(); const b = vbRef.current;
      zoom(e.deltaY < 0 ? 0.85 : 1.17, b.x + ((e.clientX - r.left) / r.width) * b.w, b.y + ((e.clientY - r.top) / r.height) * b.h);
    };
    svg.addEventListener('wheel', onWheel, { passive: false }); return () => svg.removeEventListener('wheel', onWheel);
  }, []);

  const onDown = (e) => { pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY }); drag.current = { x: e.clientX, y: e.clientY }; moved.current = false; if (pts.current.size === 2) { const [p1, p2] = [...pts.current.values()]; lastDist.current = Math.hypot(p1.x - p2.x, p1.y - p2.y); drag.current = null; } };
  const onMove = (e) => {
    if (pts.current.has(e.pointerId)) pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const r = svgRef.current.getBoundingClientRect();
    if (pts.current.size === 2 && lastDist.current) {
      const [p1, p2] = [...pts.current.values()]; const d = Math.hypot(p1.x - p2.x, p1.y - p2.y); const f = lastDist.current / d; lastDist.current = d; const b = vbRef.current;
      zoom(f, b.x + (((p1.x + p2.x) / 2 - r.left) / r.width) * b.w, b.y + (((p1.y + p2.y) / 2 - r.top) / r.height) * b.h); return;
    }
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x; const dy = e.clientY - drag.current.y; if (Math.abs(dx) + Math.abs(dy) > 3) moved.current = true;
    setVb((b) => ({ ...b, x: b.x - (dx / r.width) * b.w, y: b.y - (dy / r.height) * b.h })); drag.current = { x: e.clientX, y: e.clientY };
  };
  const onUp = (e) => { pts.current.delete(e.pointerId); if (pts.current.size < 2) lastDist.current = 0; drag.current = null; };

  const ql = q.trim().toLowerCase();
  const match = (p) => (filter === 'toate' || p.status === filter) && (!ql || p.cod_loc.toLowerCase().includes(ql));
  const counts = Object.fromEntries(STATUS_KEYS.map((k) => [k, db.plots.filter((p) => p.status === k).length]));
  const poly = (a) => a.map((x) => x.join(',')).join(' ');

  return (
    <div data-testid="map-page">
      <PageHead title="Harta cimitirului" sub="Click pe un loc pentru detalii. Scroll = zoom, drag = mutare.">
        <div className="map-search"><Icon name="search" /><input placeholder="Cod loc..." value={q} onChange={(e) => setQ(e.target.value)} data-testid="map-search" /></div>
      </PageHead>
      <div className="filter-bar" id="map-filters">
        <button className={`chip ${filter === 'toate' ? 'active' : ''}`} onClick={() => setFilter('toate')} data-testid="status-filter-toate">Toate</button>
        {STATUS_KEYS.map((k) => <button key={k} className={`chip ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)} data-testid={`status-filter-${k}`}>{STATUS_META[k].label} <span>{counts[k]}</span></button>)}
      </div>
      <div className="map-box">
        <div className="map-tools">
          <button onClick={() => zoom(0.8)} data-testid="zoom-in"><Icon name="zoom-in" /></button>
          <button onClick={() => zoom(1.25)} data-testid="zoom-out"><Icon name="zoom-out" /></button>
          <button onClick={() => setVb(home)} data-testid="zoom-reset"><Icon name="maximize" /></button>
        </div>
        <svg ref={svgRef} className="map-svg" data-testid="map-svg" preserveAspectRatio="xMidYMid meet" viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
          {db.sectors.map((s) => <polygon key={s.sector} points={poly(s.points)} fill="rgba(27,56,43,.04)" stroke="rgba(27,56,43,.4)" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />)}
          {db.sectors.map((s) => <text key={`t${s.sector}`} x={s.points[0][0] + 2} y={s.points[0][1] + 5.5} fontSize="5" fontWeight="700" fill="rgba(27,56,43,.5)" pointerEvents="none">SECTOR {s.sector}</text>)}
          {db.plots.map((p) => (
            <polygon key={p.cod_loc} points={poly(p.points)} data-testid={`map-plot-${p.cod_loc}`} fill={STATUS_META[p.status].fill} stroke={STATUS_META[p.status].stroke}
              strokeWidth="1" vectorEffect="non-scaling-stroke" opacity={match(p) ? 1 : 0.15} className="map-plot" onClick={() => { if (!moved.current) openDrawer(p.cod_loc); }}><title>{p.cod_loc}</title></polygon>
          ))}
          {db.plots.filter(match).map((p) => {
            const x = p.points.reduce((s, t) => s + t[0], 0) / p.points.length; const y = p.points.reduce((s, t) => s + t[1], 0) / p.points.length;
            return <text key={`l${p.cod_loc}`} x={x} y={y} textAnchor="middle" dominantBaseline="central" fontSize="2.8" fontWeight="600" fill="#fff" pointerEvents="none">{p.cod_loc}</text>;
          })}
        </svg>
        <div className="map-legend">{STATUS_KEYS.map((k) => <span key={k}><i style={{ background: STATUS_META[k].fill }} />{STATUS_META[k].label} · {counts[k]}</span>)}</div>
      </div>
    </div>
  );
}
