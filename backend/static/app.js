/* ---------- Drawer (plot inspector) ---------- */
function openDrawer(cod) {
  document.getElementById('drawer-overlay').classList.add('show');
  document.getElementById('drawer').classList.add('show');
  document.getElementById('drawer-body').innerHTML = '<div class="drawer-loading">Se încarcă…</div>';
  htmx.ajax('GET', '/admin/ui/loc/' + encodeURIComponent(cod), { target: '#drawer-body', swap: 'innerHTML' });
}
function closeDrawer() {
  document.getElementById('drawer-overlay').classList.remove('show');
  document.getElementById('drawer').classList.remove('show');
}
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeDrawer(); document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show')); } });

/* inspector tabs */
function inspTab(btn, id) {
  const root = btn.closest('.insp');
  root.querySelectorAll('.itab').forEach(b => b.classList.remove('active'));
  root.querySelectorAll('.itab-pane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  root.querySelector('#' + id).classList.add('active');
}
function bindInspTabs() { /* icons re-init handled inline */ }

/* re-run icons after htmx swaps */
document.body.addEventListener('htmx:afterSwap', function () { if (window.lucide) lucide.createIcons(); });

/* ---------- Modals ---------- */
function openModal(id) { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }
function openConces(data) {
  const d = data || {};
  document.getElementById('conces-title').textContent = d.id ? 'Editează concesionar' : 'Concesionar nou';
  ['id', 'nume', 'prenume', 'cnp', 'bi_ci', 'adresa', 'telefon', 'email'].forEach(k => {
    const el = document.getElementById('c-' + k); if (el) el.value = d[k] || '';
  });
  openModal('conces-modal');
}

/* ---------- Cereri accordion ---------- */
function toggleCerere(id) {
  const el = document.getElementById('cerere-' + id);
  if (el) el.classList.toggle('open');
}

/* ---------- Table filters (client-side) ---------- */
function initTableFilters(name) {
  const bar = document.querySelector('[data-filterbar="' + name + '"]');
  const rowsWrap = document.querySelector('[data-rows="' + name + '"]');
  const searchInput = document.querySelector('[data-search-input="' + name + '"]');
  const counter = document.querySelector('[data-count="' + name + '"]');
  let activeStatus = 'toate';
  function apply() {
    if (!rowsWrap) return;
    const q = (searchInput ? searchInput.value : '').trim().toLowerCase();
    let visible = 0;
    rowsWrap.querySelectorAll('tr').forEach(tr => {
      const st = tr.getAttribute('data-status');
      const hay = (tr.getAttribute('data-search') || '').toLowerCase();
      const okStatus = (activeStatus === 'toate' || !st) ? (activeStatus === 'toate' || st === activeStatus) : st === activeStatus;
      const okStatus2 = activeStatus === 'toate' || st === activeStatus;
      const okSearch = !q || hay.includes(q);
      const show = okStatus2 && okSearch;
      tr.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    if (counter) counter.textContent = visible + ' înregistrări';
  }
  if (bar) bar.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
    bar.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
    c.classList.add('active'); activeStatus = c.getAttribute('data-f'); apply();
  }));
  if (searchInput) searchInput.addEventListener('input', apply);
  apply();
}

/* ---------- Map (SVG pan / zoom) ---------- */
let MAP = { data: null, vb: { x: 0, y: 0, w: 100, h: 100 }, home: null, drag: null, filter: 'toate', q: '' };

async function initMap() {
  const svg = document.getElementById('map-svg');
  try {
    const res = await fetch('/api/admin/map.json');
    MAP.data = await res.json();
  } catch (e) { svg.innerHTML = '<text x="10" y="20">Eroare la încărcarea hărții</text>'; return; }
  const all = [].concat(MAP.data.plots || [], MAP.data.sectors || []);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  all.forEach(o => (o.points || []).forEach(p => { minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); }));
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 100; maxY = 100; }
  const pad = (maxX - minX + maxY - minY) * 0.04 + 12;
  const box = { x: minX - pad, y: minY - pad, w: (maxX - minX) + 2 * pad, h: (maxY - minY) + 2 * pad };
  MAP.vb = Object.assign({}, box); MAP.home = Object.assign({}, box);
  renderMap();
  const counts = {};
  (MAP.data.plots || []).forEach(p => counts[p.status] = (counts[p.status] || 0) + 1);
  ['liber', 'ocupat', 'rezervat', 'expirat'].forEach(k => { const e = document.getElementById('cnt-' + k); if (e) e.textContent = counts[k] || 0; });
  const legend = document.getElementById('map-legend');
  const META = { liber: ['#10b981', 'Liber'], ocupat: ['#ef4444', 'Ocupat'], rezervat: ['#f59e0b', 'Rezervat'], expirat: ['#64748b', 'Expirat'] };
  legend.innerHTML = Object.keys(META).map(k => `<span><i style="background:${META[k][0]}"></i>${META[k][1]} · ${counts[k] || 0}</span>`).join('');
  svg.addEventListener('wheel', onWheel, { passive: false });
  svg.addEventListener('pointerdown', e => { MAP.drag = { x: e.clientX, y: e.clientY }; });
  svg.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', () => { MAP.drag = null; });
}

function metaColor(status, key) {
  const M = { liber: { fill: '#10b981', stroke: '#059669' }, ocupat: { fill: '#ef4444', stroke: '#dc2626' }, rezervat: { fill: '#f59e0b', stroke: '#d97706' }, expirat: { fill: '#64748b', stroke: '#475569' } };
  return (M[status] || M.liber)[key];
}
function match(p) {
  return (MAP.filter === 'toate' || p.status === MAP.filter) && (!MAP.q || p.cod_loc.toLowerCase().includes(MAP.q));
}
function renderMap() {
  const svg = document.getElementById('map-svg');
  setVB();
  let html = '';
  (MAP.data.sectors || []).forEach(s => {
    const pts = (s.points || []).map(p => p.join(',')).join(' ');
    html += `<polygon points="${pts}" fill="rgba(27,56,43,.04)" stroke="rgba(27,56,43,.4)" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"></polygon>`;
  });
  (MAP.data.plots || []).forEach(p => {
    const pts = (p.points || []).map(x => x.join(',')).join(' ');
    const dim = match(p) ? 1 : 0.15;
    html += `<polygon points="${pts}" data-testid="map-plot-${p.cod_loc}" fill="${metaColor(p.status, 'fill')}" stroke="${metaColor(p.status, 'stroke')}" stroke-width="1" vector-effect="non-scaling-stroke" opacity="${dim}" class="map-plot" onclick="openDrawer('${p.cod_loc}')"><title>${p.cod_loc}</title></polygon>`;
  });
  (MAP.data.plots || []).filter(match).forEach(p => {
    let x = 0, y = 0; p.points.forEach(pt => { x += pt[0]; y += pt[1]; });
    x /= p.points.length; y /= p.points.length;
    html += `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="central" font-size="7" font-weight="600" fill="${metaColor(p.status, 'stroke')}" pointer-events="none">${p.cod_loc}</text>`;
  });
  svg.innerHTML = html;
}
function setVB() { const b = MAP.vb; document.getElementById('map-svg').setAttribute('viewBox', `${b.x} ${b.y} ${b.w} ${b.h}`); }
function mapZoom(f, cx, cy) { const b = MAP.vb; const mx = cx == null ? b.x + b.w / 2 : cx; const my = cy == null ? b.y + b.h / 2 : cy; b.x = mx - (mx - b.x) * f; b.y = my - (my - b.y) * f; b.w *= f; b.h *= f; setVB(); }
function onWheel(e) { e.preventDefault(); const svg = document.getElementById('map-svg'); const r = svg.getBoundingClientRect(); const mx = MAP.vb.x + ((e.clientX - r.left) / r.width) * MAP.vb.w; const my = MAP.vb.y + ((e.clientY - r.top) / r.height) * MAP.vb.h; mapZoom(e.deltaY < 0 ? 0.85 : 1.17, mx, my); }
function onMove(e) { if (!MAP.drag) return; const svg = document.getElementById('map-svg'); const r = svg.getBoundingClientRect(); MAP.vb.x -= ((e.clientX - MAP.drag.x) / r.width) * MAP.vb.w; MAP.vb.y -= ((e.clientY - MAP.drag.y) / r.height) * MAP.vb.h; MAP.drag = { x: e.clientX, y: e.clientY }; setVB(); }
function mapReset() { MAP.vb = Object.assign({}, MAP.home); setVB(); }
function mapFilter(f, btn) { MAP.filter = f; document.querySelectorAll('#map-filters .chip').forEach(c => c.classList.remove('active')); btn.classList.add('active'); renderMap(); }
function mapSearch(v) { MAP.q = (v || '').trim().toLowerCase(); renderMap(); }

/* ---------- Atașamente (sheet generic) ---------- */
const ATT_LABELS = { loc: 'Loc de veci', concesionar: 'Concesionar', contract: 'Contract', inhumat: 'Înhumat', plata: 'Plată', aviz: 'Aviz', cerere: 'Cerere' };
function openAtt(etype, eid) {
  document.getElementById('att-sheet-title').textContent = 'Atașamente · ' + (ATT_LABELS[etype] || etype);
  document.getElementById('att-body').innerHTML = '<div class="drawer-loading">Se încarcă…</div>';
  document.getElementById('att-overlay').classList.add('show');
  document.getElementById('att-sheet').classList.add('show');
  htmx.ajax('GET', '/admin/att/' + encodeURIComponent(etype) + '/' + encodeURIComponent(eid), { target: '#att-body', swap: 'innerHTML' });
}
function closeAtt() {
  document.getElementById('att-overlay').classList.remove('show');
  document.getElementById('att-sheet').classList.remove('show');
}
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAtt(); });
/* erorile de validare (422) trebuie afișate în panou, nu ignorate de htmx */
document.body.addEventListener('htmx:beforeSwap', function (e) {
  if (e.detail.xhr && e.detail.xhr.status === 422) { e.detail.shouldSwap = true; e.detail.isError = false; }
});

/* ---------- Tabele -> carduri pe telefon: etichetele coloanelor din antet ---------- */
function labelTables() {
  document.querySelectorAll('table.tbl').forEach(function (t) {
    const heads = Array.from(t.querySelectorAll('thead th')).map(function (h) { return h.textContent.trim(); });
    t.querySelectorAll('tbody tr').forEach(function (tr) {
      Array.from(tr.children).forEach(function (td, i) { if (!td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i] || ''); });
    });
  });
}
document.addEventListener('DOMContentLoaded', labelTables);
document.body.addEventListener('htmx:afterSwap', labelTables);

/* ---------- Hartă: pinch-zoom cu 2 degete + tap (pe lângă drag) ---------- */
(function () {
  const pts = new Map(); let lastDist = 0;
  function dist() { const a = Array.from(pts.values()); return Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y); }
  document.addEventListener('pointerdown', function (e) {
    const svg = document.getElementById('map-svg'); if (!svg || !svg.contains(e.target)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { lastDist = dist(); MAP.drag = null; e.stopPropagation(); }
  }, true);
  document.addEventListener('pointermove', function (e) {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2 && lastDist) {
      const d = dist(), f = lastDist / d; lastDist = d;
      const a = Array.from(pts.values()), svg = document.getElementById('map-svg'), r = svg.getBoundingClientRect();
      const cx = MAP.vb.x + (((a[0].x + a[1].x) / 2 - r.left) / r.width) * MAP.vb.w;
      const cy = MAP.vb.y + (((a[0].y + a[1].y) / 2 - r.top) / r.height) * MAP.vb.h;
      mapZoom(f, cx, cy);
    }
  }, true);
  ['pointerup', 'pointercancel'].forEach(function (n) {
    document.addEventListener(n, function (e) { pts.delete(e.pointerId); if (pts.size < 2) lastDist = 0; }, true);
  });
})();
