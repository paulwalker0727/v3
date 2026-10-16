const API = '/api/v1';
const app = document.getElementById('mobile-app');
let state = { key: null, bootstrap: null, summary: null, dataStatus: null, view: 'dashboard', map: null, selected: null };

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtName = (o) => o?.display_name || o?.official_name || o?.name || 'Cimitir';

function dbOpen(){
  return new Promise((resolve,reject)=>{const r=indexedDB.open('cimitir-mobile-v2',1);r.onupgradeneeded=()=>r.result.createObjectStore('kv');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
}
async function kvGet(k){const db=await dbOpen();return new Promise((resolve,reject)=>{const tx=db.transaction('kv','readonly');const r=tx.objectStore('kv').get(k);r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error);});}
async function kvSet(k,v){const db=await dbOpen();return new Promise((resolve,reject)=>{const tx=db.transaction('kv','readwrite');tx.objectStore('kv').put(v,k);tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);});}
async function kvDel(k){const db=await dbOpen();return new Promise((resolve,reject)=>{const tx=db.transaction('kv','readwrite');tx.objectStore('kv').delete(k);tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);});}

async function api(path, options={}){
  const headers = {...(options.headers||{})};
  if(state.key) headers.Authorization = `Binding ${state.key}`;
  if(options.body && !headers['Content-Type']) headers['Content-Type']='application/json';
  const r=await fetch(path,{...options,headers,cache:'no-store'});
  let body={}; try{body=await r.json();}catch(_e){}
  if(!r.ok||body.ok===false){const e=new Error(body.message||body.code||`HTTP ${r.status}`);e.status=r.status;e.code=body.code;throw e;}
  return body.result;
}
function toast(msg){document.querySelector('.toast')?.remove();const n=document.createElement('div');n.className='toast';n.textContent=msg;document.body.appendChild(n);setTimeout(()=>n.remove(),3200);}

function pairingView(message=''){
  app.innerHTML=`<div class="auth-wrap"><section class="auth-card"><div class="brand-mark">✦</div><h1>Cimitir Mobile</h1><p>Asociază acest dispozitiv o singură dată. Codul se generează din MAIN → <b>Cloud / Telefoane</b>.</p><form id="pair-form"><div class="field"><label>Cod pairing</label><input id="pair-code" class="pair-code" maxlength="28" autocomplete="one-time-code" inputmode="text" placeholder="XXXXX-XXXXX-XXXXX-XXXXX" required></div><div class="field"><label>Numele dispozitivului</label><input id="device-name" maxlength="64" value="${/Android/i.test(navigator.userAgent)?'Telefon / tabletă Android':'Dispozitiv mobil'}"></div><button class="primary" id="pair-submit">Asociază dispozitivul</button><div class="error" id="pair-error">${esc(message)}</div></form><p class="micro">Cheia tehnică este păstrată local și nu este afișată în interfața normală.</p></section></div>`;
  const form=document.getElementById('pair-form');
  const inp=document.getElementById('pair-code');
  inp.addEventListener('input',()=>{let s=inp.value.toUpperCase().replace(/[^A-Z2-9]/g,'').slice(0,20);inp.value=s.match(/.{1,5}/g)?.join('-')||s;});
  form.addEventListener('submit',async(e)=>{e.preventDefault();const b=document.getElementById('pair-submit');const er=document.getElementById('pair-error');b.disabled=true;er.textContent='';try{const r=await api(`${API}/phone-pairing/redeem`,{method:'POST',body:JSON.stringify({code:inp.value,device_name:document.getElementById('device-name').value,client_id:getClientId()})});state.key=r.binding_key;await kvSet('binding_key',state.key);await loadApp();}catch(x){er.textContent=x.status===429?'Prea multe încercări. Așteaptă puțin și încearcă din nou.':'Cod invalid, expirat sau deja folosit.';}finally{b.disabled=false;}});
}
function getClientId(){let id=localStorage.getItem('cimitir_mobile_client');if(!id){id=(crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9._:-]/g,'');localStorage.setItem('cimitir_mobile_client',id);}return id;}

function shell(){
  const cem=fmtName(state.bootstrap?.cemetery), dev=state.bootstrap?.device?.device_name||'Dispozitiv mobil';
  app.innerHTML=`<div class="shell"><aside class="sidebar"><div class="side-brand"><div class="brand-mark">✦</div><div><div class="side-title">Cimitir Mobile</div><div class="side-sub">${esc(cem)}</div></div></div><nav class="nav">${navBtn('dashboard','⌂','Acasă')}${navBtn('search','⌕','Căutare')}${navBtn('map','⌖','Hartă')}${navBtn('modules','▦','Registre')}</nav><div class="side-foot"><div><span class="online-dot"></span>Cloud conectat</div><button class="logout-link" id="unpair">Dezasociază acest dispozitiv</button></div></aside><div class="main"><header class="topbar"><div><div class="top-title">${esc(cem)}</div></div><div class="top-meta"><button class="sync-btn" id="sync-btn" data-testid="sync-button"></button><span class="device-chip">${esc(dev)}</span></div></header><main class="content" id="view"></main></div></div><nav class="bottom-nav">${bottomBtn('dashboard','⌂','Acasă')}${bottomBtn('search','⌕','Caută')}${bottomBtn('map','⌖','Hartă')}${bottomBtn('modules','▦','Meniu')}</nav><div class="sheet-backdrop" id="sheet"><div class="sheet" id="sheet-body"></div></div>`;
  document.querySelectorAll('[data-view]').forEach((b)=>b.addEventListener('click',()=>navigate(b.dataset.view)));
  document.getElementById('sync-btn')?.addEventListener('click',openSyncPanel);renderSyncBtn();
  document.getElementById('unpair')?.addEventListener('click',async()=>{if(confirm('Dezasociezi acest dispozitiv? Va fi necesar un cod nou pentru reconectare.')){await kvDel('binding_key');state.key=null;pairingView();}});
}
function navBtn(v,i,l){return `<button data-view="${v}" class="${state.view===v?'active':''}"><span class="ico">${i}</span>${l}</button>`}
function bottomBtn(v,i,l){return `<button data-view="${v}" class="${state.view===v?'active':''}"><span class="ico">${i}</span><span>${l}</span></button>`}
function refreshNav(){document.querySelectorAll('[data-view]').forEach((b)=>b.classList.toggle('active',b.dataset.view===state.view));}
function navigate(v){state.view=v;refreshNav();renderView();window.scrollTo({top:0,behavior:'smooth'});}

async function loadApp(){
  try{
    state.bootstrap=await api(`${API}/mobile/bootstrap`);
    state.summary=await api(`${API}/mobile/summary`);
    state.dataStatus=await api(`${API}/mobile/data-status`).catch(()=>null);
    await kvSet('bootstrap_cache',state.bootstrap); await kvSet('summary_cache',state.summary);
    await loadSyncInfo();
    shell();renderView();
    if((await outbox()).some(o=>o.state==='pending'))syncNow();
  }catch(e){
    if(e.status===401){await kvDel('binding_key');await kvDel('bootstrap_cache');await kvDel('summary_cache');state.key=null;pairingView('Asocierea nu mai este validă. Generează un cod nou din MAIN.');return;}
    const cachedBootstrap=await kvGet('bootstrap_cache').catch(()=>null), cachedSummary=await kvGet('summary_cache').catch(()=>null);
    if(cachedBootstrap){state.bootstrap=cachedBootstrap;state.summary=cachedSummary||{};shell();renderView();toast('Cloud indisponibil: afișez ultima stare memorată.');}
    else pairingView('Cloud indisponibil momentan. Reîncearcă atunci când revine conexiunea.');
  }
}
function renderView(){const v=document.getElementById('view');if(!v)return;if(state.view==='dashboard')renderDashboard(v);else if(state.view==='search')renderSearch(v);else if(state.view==='map')renderMap(v);else renderModules(v);}
function dataNotice(){
  const d=state.dataStatus;
  if(!d)return '';
  const live=d.live||{};
  if(d.state==='imported_not_materialized')return `Cloud-ul are ${Number(d.bootstrap_rows)||0} înregistrări în registrul de import, dar încă nu le-a materializat în registrul citit de Mobile v2. Nu reface asocierea telefonului; verifică etapa de publicare/import MAIN → cloud TEST.`;
  if(d.state==='empty_live_registry')return 'În cimitirul asociat telefonului nu sunt încă locuri în registrul live din cloud. Verifică sincronizarea MAIN și faptul că este selectat același cimitir.';
  if(d.state==='no_drawn_geometry')return `În cloud există ${Number(live.graves)||0} locuri, dar niciun poligon desenat. Verifică trimiterea câmpului points din MAIN.`;
  return '';
}
async function refreshCloudData(){
  try{
    state.summary=await api(`${API}/mobile/summary`);
    state.dataStatus=await api(`${API}/mobile/data-status`).catch(()=>null);
    state.map=null;
    await kvSet('summary_cache',state.summary);
    renderView();toast('Datele au fost recitite din cloud TEST.');
  }catch(_e){toast('Cloud indisponibil. Datele existente au fost păstrate.');}
}

function renderDashboard(v){const s=state.summary||{},notice=dataNotice();v.innerHTML=`<div class="page-head"><div><h1>Bun venit</h1><p>Acces rapid la datele cimitirului, fără coduri tehnice.</p></div><button class="refresh-button" id="refresh-data">Reverifică datele</button></div>${notice?`<div class="sync-notice" role="status">${esc(notice)}</div>`:''}<section class="hero-search"><h2>Ce cauți?</h2><div class="searchbox"><input id="home-q" placeholder="Concesionar, înhumat, loc, contract…"><button id="home-search">Caută</button></div></section><div class="quick-grid"><div class="quick-card"><div class="qico">▦</div><div><b>${s.graves??'—'}</b><span>locuri de veci</span></div></div><div class="quick-card"><div class="qico">♟</div><div><b>${s.concessionaires??'—'}</b><span>concesionari</span></div></div><div class="quick-card"><div class="qico">✦</div><div><b>${s.burials??'—'}</b><span>înhumați</span></div></div><div class="quick-card"><div class="qico">▤</div><div><b>${s.contracts??'—'}</b><span>contracte</span></div></div></div><div class="module-grid"><button class="module-card" data-go="map"><span class="ico">⌖</span><strong>Hartă cimitir</strong><small>Alege direct locul de pe hartă și deschide fișa.</small></button><button class="module-card" data-go="search"><span class="ico">⌕</span><strong>Căutare universală</strong><small>Concesionari, înhumați, locuri și contracte.</small></button><button class="module-card" data-soon="Contract nou"><span class="ico">▤</span><strong>Contract nou</strong><small>Fluxul ghidat de emitere va fi activat în etapa de scriere.</small></button><button class="module-card" data-soon="Documente"><span class="ico">▧</span><strong>Documente</strong><small>Arhivă și emitere documente, în etapa următoare.</small></button></div>`;
  document.getElementById('refresh-data').onclick=refreshCloudData;const go=()=>{const q=document.getElementById('home-q').value.trim();state.searchSeed=q;navigate('search');};document.getElementById('home-search').onclick=go;document.getElementById('home-q').addEventListener('keydown',(e)=>{if(e.key==='Enter')go();});v.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.go));v.querySelectorAll('[data-soon]').forEach(b=>b.onclick=()=>toast(`${b.dataset.soon}: pregătit pentru etapa următoare.`));
}

function renderSearch(v){v.innerHTML=`<div class="page-head"><div><h1>Căutare</h1><p>Nu trebuie să cunoști UID-uri. Caută exact cum ai căuta în MAIN.</p></div></div><div class="search-panel"><div class="big-search"><input id="search-q" type="search" autocomplete="off" placeholder="Nume, telefon, cod loc, nr. contract…" value="${esc(state.searchSeed||'')}"></div><div id="search-results" class="empty">Introdu cel puțin 2 caractere.</div></div>`;const input=document.getElementById('search-q');state.searchSeed='';let timer;input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>doSearch(input.value),220);});if(input.value.trim().length>=2)doSearch(input.value);else input.focus();}
async function doSearch(q){const box=document.getElementById('search-results');if(!box)return;if(q.trim().length<2){box.className='empty';box.textContent='Introdu cel puțin 2 caractere.';return;}box.className='empty';box.textContent='Se caută…';try{const r=await api(`${API}/mobile/search?q=${encodeURIComponent(q.trim())}`);const groups=[['Înhumați','burials'],['Concesionari','concessionaires'],['Locuri de veci','graves'],['Contracte','contracts']];let html='';let total=0;for(const [title,key] of groups){const a=r[key]||[];if(!a.length)continue;total+=a.length;html+=`<div class="section-title">${title} · ${a.length}</div><div class="result-list">${a.map(x=>searchRow(key,x)).join('')}</div>`;}box.className='';box.innerHTML=total?html:'<div class="empty">Niciun rezultat.</div>';box.querySelectorAll('[data-grave]').forEach(b=>b.onclick=()=>openGrave(b.dataset.grave));}catch(e){box.className='empty';box.textContent='Căutarea nu este disponibilă momentan.';}}
function searchRow(type,x){let title='',sub='',uid=type==='graves'?x.uid:(x.grave_uid||'');if(type==='burials'){title=x.name;sub=`Înhumat · ${x.grave_code||'loc neasociat'}${x.death_date?' · deces '+x.death_date:''}`;}else if(type==='concessionaires'){title=x.name;sub=`Concesionar${x.grave_code?' · loc '+x.grave_code:''}${x.phone?' · '+x.phone:''}`;}else if(type==='graves'){title=`Loc ${x.code}`;sub=[x.sector&&`sector ${x.sector}`,x.parcel&&`parcela ${x.parcel}`,x.row&&`rând ${x.row}`].filter(Boolean).join(' · ');uid=x.uid;}else{title=`Contract ${x.number||'—'}`;sub=`${x.concessionaire||'—'}${x.grave_code?' · loc '+x.grave_code:''}`;}return `<button class="result" ${uid?`data-grave="${esc(uid)}"`:'disabled aria-disabled="true"'}><div><b>${esc(title)}</b><small>${esc(sub||'')}</small></div><span class="pill">${type==='graves'?(x.occupied?'Ocupat':'Disponibil'):(uid?'Deschide':'Fără loc asociat')}</span></button>`;}

async function renderMap(v){v.innerHTML=`<div class="page-head"><div><h1>Harta cimitirului</h1><p>Atinge un loc pentru fișa completă. Pan și pinch/zoom funcționează pe telefon.</p></div></div><div class="map-card"><div class="map-toolbar"><input id="map-q" placeholder="Filtrează după codul locului…"><button id="map-home">Încadrează</button><button id="map-reload">Actualizează</button></div><div id="map-stage" class="empty">Se încarcă harta…</div><div class="map-legend">■ Verde = disponibil &nbsp; ■ Roșu = ocupat</div></div>`;document.getElementById('map-reload').onclick=()=>renderMap(v);try{state.map=await api(`${API}/mobile/map`);drawMap(state.map);}catch(e){document.getElementById('map-stage').textContent='Harta nu este disponibilă momentan.';}}
function drawMap(data){const stage=document.getElementById('map-stage');if(!stage)return;const plots=(data.plots||[]).filter(p=>Array.isArray(p.points)&&p.points.length>=3);const sectors=(data.sectors||[]).filter(s=>Array.isArray(s.points)&&s.points.length>=3);if(!plots.length){stage.className='empty';stage.textContent=dataNotice()||'Nu există încă geometrii sincronizate pentru hartă. Folosește Actualizează după sincronizarea MAIN.';return;}const all=[...plots,...sectors].flatMap(x=>x.points);const xs=all.map(p=>Number(p[0])),ys=all.map(p=>Number(p[1]));const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);const pad=Math.max(10,(maxX-minX+maxY-minY)*.04);const home={x:minX-pad,y:minY-pad,w:maxX-minX+2*pad,h:maxY-minY+2*pad};let vb={...home};stage.className='';stage.innerHTML=`<svg id="cem-map" class="map-svg" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}" preserveAspectRatio="xMidYMid meet">${sectors.map(s=>`<polygon points="${pts(s.points)}" fill="rgba(24,59,45,.035)" stroke="rgba(24,59,45,.38)" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"></polygon>`).join('')}${plots.map(p=>`<polygon class="plot" data-uid="${esc(p.uid)}" data-code="${esc(p.code)}" points="${pts(p.points)}" fill="${p.occupied?'#9f5a52':'#4f8a68'}" stroke="#fff" stroke-width=".7" vector-effect="non-scaling-stroke"><title>${esc(p.code)}</title></polygon>`).join('')}</svg>`;const svg=document.getElementById('cem-map');const set=()=>svg.setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`);let drag=null,moved=false;svg.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};moved=false;svg.setPointerCapture?.(e.pointerId);});svg.addEventListener('pointermove',e=>{if(!drag)return;const r=svg.getBoundingClientRect(),dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)moved=true;vb.x-=dx/r.width*vb.w;vb.y-=dy/r.height*vb.h;drag={x:e.clientX,y:e.clientY};set();});svg.addEventListener('pointerup',()=>drag=null);svg.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?.84:1.19;const r=svg.getBoundingClientRect(),mx=vb.x+(e.clientX-r.left)/r.width*vb.w,my=vb.y+(e.clientY-r.top)/r.height*vb.h;vb={x:mx-(mx-vb.x)*f,y:my-(my-vb.y)*f,w:vb.w*f,h:vb.h*f};set();},{passive:false});svg.querySelectorAll('.plot').forEach(p=>p.addEventListener('click',()=>{if(!moved)openGrave(p.dataset.uid);}));document.getElementById('map-home').onclick=()=>{vb={...home};set();};document.getElementById('map-q').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();svg.querySelectorAll('.plot').forEach(p=>p.style.opacity=!q||p.dataset.code.toLowerCase().includes(q)?'1':'.12');});}
function pts(a){return a.map(p=>`${Number(p[0])},${Number(p[1])}`).join(' ')}

function renderModules(v){v.innerHTML=`<div class="page-head"><div><h1>Registre</h1><p>Interfața este pregătită pentru modulele complete. Prima etapă este intenționat read-only.</p></div></div><div class="module-grid"><button class="module-card" data-go="search"><span class="ico">♟</span><strong>Concesionari</strong><small>Caută și deschide locul asociat.</small></button><button class="module-card" data-go="search"><span class="ico">✦</span><strong>Înhumați</strong><small>Caută după nume, CNP sau loc.</small></button><button class="module-card" data-go="search"><span class="ico">▤</span><strong>Contracte</strong><small>Caută după număr, concesionar sau loc.</small></button><button class="module-card" data-soon="Plăți"><span class="ico">▣</span><strong>Plăți</strong><small>${state.summary?.payments??'—'} plăți în cloud; registrul complet urmează.</small></button></div>`;v.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.go));v.querySelectorAll('[data-soon]').forEach(b=>b.onclick=()=>toast('Registrul Plăți va fi activat în etapa următoare.'));}

async function openGrave(uid){if(!uid)return;const back=document.getElementById('sheet'),body=document.getElementById('sheet-body');back.classList.add('show');body.innerHTML='<div class="sheet-body">Se încarcă fișa…</div>';back.onclick=(e)=>{if(e.target===back)back.classList.remove('show');};try{const d=await api(`${API}/mobile/graves/${encodeURIComponent(uid)}`);state.currentGrave=d;const g=d.grave||{};const pend=(await outbox()).filter(o=>o.entity==='inhumati'&&o.data?.loc_uid===g.uid&&!(d.burials||[]).some(b=>b.uid===o.uid));body.innerHTML=`<div class="sheet-head"><button id="sheet-x" data-testid="sheet-close">×</button><div class="micro">FIȘĂ LOC DE VECI</div><h2>${esc(g.cod_loc||'Loc')}</h2><div>${esc([g.sector&&'Sector '+g.sector,g.parcela&&'Parcela '+g.parcela,g.rand&&'Rând '+g.rand].filter(Boolean).join(' · '))}</div></div><div class="sheet-body">${detailBlock('Date loc',[["Stare",g.stare||g.disponibilitate||'—'],['Suprafață',g.suprafata?g.suprafata+' m²':'—'],['Capacitate',g.capacitate||'—'],['Nr. vechi',g.nr_vechi||'—'],['Observații',g.observatii||'—']])}${can('locuri_de_veci')?'<button class="edit-btn" data-edit="locuri_de_veci" data-testid="edit-grave">Editează locul</button>':''}${listBlock('Concesionari / contracte',d.contracts,(x)=>`${x.nume||''} ${x.prenume||''}`.trim()||'Concesionar',x=>`Contract ${x.numar_contract||'—'} · ${x.data_contract||'fără dată'}`,'concesionari','concessionaire_uid')}${listBlock('Înhumați',(d.burials||[]).concat(pend.map(o=>({uid:o.uid,nume:o.data.nume,prenume:o.data.prenume,data_deces:o.data.data_deces,data_inhumare:o.data.data_inhumare,_pending:true}))),(x)=>`${x.nume||''} ${x.prenume||''}`.trim(),x=>`Deces ${x.data_deces||'—'} · Înhumare ${x.data_inhumare||'—'}${x._pending?' · nesincronizat':''}`,'inhumati','uid')}${can('inhumati')?'<button class="edit-btn" data-add="inhumati" data-testid="add-burial">Adaugă înhumat</button>':''}${listBlock('Plăți',d.payments,(x)=>`${x.suma??0} lei`,x=>`${x.chitanta_nr||'Fără nr.'} · ${x.data_plata||'—'}`)}${listBlock('Avize de construcție',d.permits,(x)=>`Aviz ${x.nr_aviz||'—'}`,x=>`${x.lucrare||'Lucrare'} · ${x.data_aviz||'fără dată'}`)}</div>`;document.getElementById('sheet-x').onclick=()=>back.classList.remove('show');body.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>openRecordForm(b.dataset.edit,b.dataset.uid||g.uid));body.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>openRecordForm(b.dataset.add,null));}catch(e){body.innerHTML='<div class="sheet-body">Fișa nu a putut fi încărcată.</div>';}}
function detailBlock(title,rows){return `<section class="detail-card"><h3>${esc(title)}</h3>${rows.map(([k,v])=>`<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</section>`}
function listBlock(title,arr,titleFn,subFn,entity,uidKey){arr=arr||[];const ed=entity&&can(entity);return `<section class="detail-card"><h3>${esc(title)} · ${arr.length}</h3>${arr.length?arr.map(x=>`<div class="kv"><div><b>${esc(titleFn(x))}</b><div class="micro">${esc(subFn(x))}</div></div>${ed&&x[uidKey]&&!x._pending?`<button class="edit-btn small" data-edit="${entity}" data-uid="${esc(x[uidKey])}" data-testid="edit-${entity}">Editează</button>`:''}</div>`).join(''):'<div class="micro">Nicio înregistrare.</div>'}</section>`}

/* ---------------- Sync v2: stare, outbox, reluare ---------------- */
const ENTITY_ORDER=['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize'];
const FORMS={
  inhumati:{title:'Înhumat',fields:[['nume','Nume'],['prenume','Prenume'],['data_nasterii','Data nașterii'],['data_deces','Data decesului'],['data_inhumare','Data înhumării'],['observatii','Observații']],label:d=>`Înhumat: ${d.nume||''} ${d.prenume||''}`.trim()},
  locuri_de_veci:{title:'Loc de veci',fields:[['stare','Stare'],['disponibilitate','Disponibilitate'],['observatii','Observații']],label:d=>`Loc ${d.cod_loc||''}`.trim()},
  concesionari:{title:'Concesionar',fields:[['nume','Nume'],['prenume','Prenume'],['telefon','Telefon'],['email','E-mail'],['adresa','Adresă'],['observatii','Observații']],label:d=>`Concesionar: ${d.nume||''} ${d.prenume||''}`.trim()}
};
const REASONS={not_authorized:'Nu ai dreptul de editare pentru acest registru (se acordă din MAIN).',delete_not_allowed:'Ștergerea nu este permisă de pe telefon.',version_mismatch:'Înregistrarea a fost modificată între timp. Se păstrează varianta din MAIN.',natural_key:'Există deja un loc cu acest cod.',deleted_on_server:'Înregistrarea a fost ștearsă între timp.',date_invalide:'Date invalide.'};
const can=(e)=>!!state.syncInfo?.editable?.includes(e);
const newId=()=>((crypto.randomUUID?.()||`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}0000000000000000`)).replace(/-/g,'').slice(0,32);
async function outbox(){return (await kvGet('outbox').catch(()=>null))||[];}
async function saveOutbox(o){await kvSet('outbox',o);renderSyncBtn();}
async function loadSyncInfo(){try{state.syncInfo=await api(`${API}/sync-v2/status`);}catch(e){if(e.status===401)throw e;}state.lastSync=state.lastSync||await kvGet('last_sync').catch(()=>null);}
async function renderSyncBtn(){const b=document.getElementById('sync-btn');if(!b)return;const ob=await outbox();const pending=ob.filter(o=>o.state==='pending').length,bad=ob.filter(o=>o.state==='conflict'||o.state==='error').length;const ls=state.lastSync||{};b.dataset.state=state.syncing?'busy':bad?'bad':ls.offline?'offline':pending?'pending':'ok';b.textContent=state.syncing?'Se sincronizează…':bad?`Sincronizează · ${bad} de rezolvat`:pending?`Sincronizează · ${pending} în coadă`:ls.offline?'Offline · reîncearcă':'Sincronizează';b.disabled=!!state.syncing;}
async function syncNow(){
  if(state.syncing)return;state.syncing=true;renderSyncBtn();
  const res={at:Date.now(),applied:0,conflicts:0,errors:0,retry:0,offline:false,error:''};
  try{
    let ob=await outbox();
    const todo=ob.filter(o=>o.state==='pending').sort((a,b)=>ENTITY_ORDER.indexOf(a.entity)-ENTITY_ORDER.indexOf(b.entity));
    for(let i=0;i<todo.length;i+=50){
      const chunk=todo.slice(i,i+50);
      const r=await api(`${API}/sync-v2/push`,{method:'POST',body:JSON.stringify({ops:chunk.map(o=>({op_id:o.op_id,entity:o.entity,uid:o.uid,action:o.action,base_version:o.base_version,data:o.data}))})});
      for(const x of r.results){const o=ob.find(z=>z.op_id===x.op_id);if(!o)continue;
        if(x.status==='applied'){ob=ob.filter(z=>z!==o);res.applied++;}
        else if(x.status==='conflict'){o.state='conflict';o.reason=x.reason;o.serverVersion=x.server_version;res.conflicts++;}
        else if(x.status==='retry'){o.state='pending';o.error=x.reason;res.retry++;}
        else{o.state='error';o.reason=x.reason;res.errors++;}}
      await saveOutbox(ob);
    }
    state.summary=await api(`${API}/mobile/summary`);state.dataStatus=await api(`${API}/mobile/data-status`).catch(()=>null);state.map=null;
    await loadSyncInfo();await kvSet('summary_cache',state.summary);
  }catch(e){
    if(e.status===401){await kvDel('binding_key');state.key=null;state.syncing=false;pairingView('Asocierea nu mai este validă. Generează un cod nou din MAIN.');return;}
    res.offline=!e.status;res.error=res.offline?'Fără conexiune la internet. Modificările rămân în coadă.':(e.message||'Eroare');res.errors+=res.offline?0:1;
  }
  state.syncing=false;state.lastSync=res;await kvSet('last_sync',res);renderSyncBtn();renderView();
  toast(res.offline?res.error:res.error?`Sincronizare incompletă: ${res.error}`:res.conflicts||res.errors?`Sincronizat: ${res.applied} trimise, ${res.conflicts+res.errors} de rezolvat`:`Sincronizat: ${res.applied} modificări trimise, date actualizate.`);
  if(document.getElementById('sheet')?.classList.contains('show')&&state.panel)openSyncPanel();
}
async function openSyncPanel(){
  state.panel=true;const back=document.getElementById('sheet'),body=document.getElementById('sheet-body');back.classList.add('show');back.onclick=(e)=>{if(e.target===back){back.classList.remove('show');state.panel=false;}};
  const ob=await outbox();const ls=state.lastSync||{};const when=ls.at?new Date(ls.at).toLocaleString('ro-RO'):'niciodată';
  const rows=ob.map((o,i)=>`<div class="kv"><div><b>${esc(o.label||o.entity)}</b><div class="micro">${esc(o.state==='pending'?'În așteptare'+(o.error?' · se reîncearcă':''):o.state==='conflict'?(REASONS[o.reason]||'Conflict'):(REASONS[o.reason]||'Refuzat de server'))}</div></div>${o.state==='conflict'?`<button class="edit-btn small" data-rebase="${i}">Reia peste varianta nouă</button>`:''}${o.state!=='pending'?`<button class="edit-btn small" data-retry="${i}">Reîncearcă</button>`:''}<button class="edit-btn small" data-drop="${i}">Renunță</button></div>`).join('');
  body.innerHTML=`<div class="sheet-head"><button id="sheet-x" data-testid="sync-close">×</button><div class="micro">SINCRONIZARE</div><h2>Stare sincronizare</h2></div><div class="sheet-body"><section class="detail-card" data-testid="sync-status"><div class="kv"><span>Ultima sincronizare</span><b>${esc(when)}</b></div><div class="kv"><span>În coadă</span><b>${ob.filter(o=>o.state==='pending').length}</b></div><div class="kv"><span>Conflicte / erori</span><b>${ob.filter(o=>o.state!=='pending').length}</b></div>${ls.at?`<div class="kv"><span>Ultimul rezultat</span><b>${ls.offline?'Offline':`${ls.applied||0} trimise`}</b></div>`:''}${ls.error?`<div class="micro">${esc(ls.error)}</div>`:''}</section><section class="detail-card"><h3>Modificări locale · ${ob.length}</h3>${rows||'<div class="micro">Nicio modificare locală.</div>'}</section><button class="primary" id="sync-run" data-testid="sync-run">${ob.some(o=>o.state==='pending')?'Sincronizează acum':'Verifică și actualizează'}</button></div>`;
  document.getElementById('sheet-x').onclick=()=>{back.classList.remove('show');state.panel=false;};
  document.getElementById('sync-run').onclick=()=>syncNow();
  body.querySelectorAll('[data-retry]').forEach(b=>b.onclick=async()=>{const o=ob[+b.dataset.retry];o.state='pending';o.op_id='m-'+newId();await saveOutbox(ob);syncNow();});
  body.querySelectorAll('[data-drop]').forEach(b=>b.onclick=async()=>{ob.splice(+b.dataset.drop,1);await saveOutbox(ob);openSyncPanel();});
  body.querySelectorAll('[data-rebase]').forEach(b=>b.onclick=async()=>{const o=ob[+b.dataset.rebase];o.state='pending';o.op_id='m-'+newId();o.base_version=o.serverVersion||o.base_version;await saveOutbox(ob);syncNow();});
}
async function openRecordForm(entity,uid){
  const f=FORMS[entity];if(!f||!can(entity))return;const body=document.getElementById('sheet-body');state.panel=false;
  let row=null;
  if(uid){try{row=await api(`${API}/sync-v2/row?entity=${entity}&uid=${encodeURIComponent(uid)}`);}catch(_e){toast('Înregistrarea nu poate fi citită acum. Reîncearcă online.');return;}}
  const cur=row?.data||{};
  body.innerHTML=`<div class="sheet-head"><button id="sheet-x">×</button><div class="micro">${row?'EDITARE':'ADĂUGARE'}</div><h2>${esc(f.title)}</h2></div><div class="sheet-body"><form id="rec-form">${f.fields.map(([k,l])=>`<div class="field"><label>${esc(l)}</label><input name="${k}" maxlength="500" value="${esc(cur[k]??'')}" data-testid="field-${k}"></div>`).join('')}<button class="primary" id="rec-save" data-testid="record-save">Salvează și sincronizează</button></form></div>`;
  document.getElementById('sheet-x').onclick=()=>document.getElementById('sheet').classList.remove('show');
  document.getElementById('rec-form').onsubmit=async(e)=>{e.preventDefault();const vals=Object.fromEntries(new FormData(e.target).entries());
    const data=row?{...cur,...vals}:{...vals};
    if(!row&&entity==='inhumati')data.loc_uid=state.currentGrave?.grave?.uid;
    const op={op_id:'m-'+newId(),entity,uid:row?.uid||newId(),action:'upsert',base_version:row?.version||0,data,state:'pending',label:f.label({...cur,...vals,cod_loc:state.currentGrave?.grave?.cod_loc})};
    let ob=(await outbox()).filter(o=>!(o.uid===op.uid&&o.state==='pending'));ob.push(op);await saveOutbox(ob);
    document.getElementById('sheet').classList.remove('show');await syncNow();};
}

(async function boot(){if('serviceWorker' in navigator){navigator.serviceWorker.register('/mobile/sw.js',{scope:'/mobile/'}).catch(()=>{});}try{state.key=await kvGet('binding_key');}catch(_e){}if(state.key)await loadApp();else pairingView();})();
