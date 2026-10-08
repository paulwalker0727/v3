import React, { useState } from 'react';
import { useApp } from '../store';
import { Icon, PageHead, StatusBadge, lei } from '../components/ui-kit';

export default function Cauta() {
  const { db, openDrawer } = useApp();
  const [q, setQ] = useState('');
  const t = q.trim().toLowerCase();
  const has = (...v) => v.some((x) => String(x ?? '').toLowerCase().includes(t));
  const R = t.length < 2 ? null : {
    persoane: db.inhumati.filter((x) => has(x.nume, x.prenume, `${x.nume} ${x.prenume}`, x.cnp, x.cod_loc)).slice(0, 15),
    conces: db.concesionari.filter((x) => has(x.nume, x.prenume, `${x.nume} ${x.prenume}`, x.cnp, x.telefon, x.email)).slice(0, 15),
    locuri: db.plots.filter((x) => has(x.cod_loc, x.localizare)).slice(0, 15),
    contracte: db.contracte.filter((x) => has(x.numar_contract, x.cod_loc, x.concesionar_nume)).slice(0, 15),
    plati: db.plati.filter((x) => has(x.chitanta_nr, x.cod_loc)).slice(0, 15),
  };
  const total = R ? Object.values(R).reduce((s, l) => s + l.length, 0) : 0;
  const locOfConc = (id) => db.contracte.find((c) => c.concesionar_id === id)?.cod_loc;
  const Card = ({ title, sub, cod, right }) => (
    <div className="res-card clickable" style={{ cursor: cod ? 'pointer' : 'default' }} onClick={() => cod && openDrawer(cod)}>
      <div><div className="strong">{title}</div><div className="muted-xs">{sub}</div></div>
      <div className="res-act">{right}</div>
    </div>
  );
  const Sec = ({ title, list, children }) => list.length > 0 && <><div className="res-title">{title} ({list.length})</div>{children}</>;

  return (
    <div data-testid="search-page">
      <PageHead title="Căutare" sub="Persoane, concesionari, cod loc, contracte, chitanțe." />
      <div className="search-big">
        <Icon name="search" />
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nume, CNP, telefon, cod loc, nr. contract…" autoComplete="off" autoFocus data-testid="search-input" />
      </div>
      <div data-testid="search-results">
        {!R && <p className="empty">Introdu cel puțin 2 caractere pentru a căuta.</p>}
        {R && total === 0 && <p className="empty">Niciun rezultat pentru „{q}”.</p>}
        {R && <>
          <Sec title="Persoane înhumate" list={R.persoane}>{R.persoane.map((x) => <Card key={x.id} title={`${x.nume} ${x.prenume}`} sub={`Loc ${x.cod_loc} · deces ${x.data_deces}`} cod={x.cod_loc} />)}</Sec>
          <Sec title="Concesionari" list={R.conces}>{R.conces.map((x) => (
            <Card key={x.id} title={`${x.nume} ${x.prenume}`} sub={`CNP ${x.cnp || '—'}`} cod={locOfConc(x.id)}
              right={x.telefon && <a className="tel" href={`tel:${x.telefon}`} onClick={(e) => e.stopPropagation()}><Icon name="phone" />{x.telefon}</a>} />
          ))}</Sec>
          <Sec title="Locuri de veci" list={R.locuri}>{R.locuri.map((x) => <Card key={x.cod_loc} title={<span className="mono">{x.cod_loc}</span>} sub={x.localizare} cod={x.cod_loc} right={<StatusBadge status={x.status} />} />)}</Sec>
          <Sec title="Contracte" list={R.contracte}>{R.contracte.map((x) => <Card key={x.id} title={<span className="mono">{x.numar_contract}</span>} sub={`${x.concesionar_nume} · Loc ${x.cod_loc}`} cod={x.cod_loc} />)}</Sec>
          <Sec title="Chitanțe" list={R.plati}>{R.plati.map((x) => <Card key={x.id} title={<span className="mono">{x.chitanta_nr}</span>} sub={`Loc ${x.cod_loc} · ${x.data_plata}`} cod={x.cod_loc} right={<b className="mono">{lei(x.suma)}</b>} />)}</Sec>
        </>}
      </div>
    </div>
  );
}
