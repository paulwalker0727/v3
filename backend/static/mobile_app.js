import {dbOpen,kvGet,kvSet,kvDel,mirrorSync,mirrorClear,mirrorAt,offlineGrave,offlineMap,offlineSearch,offlineList} from './mobile_offline.js';
import {enqueue,flush as flushPhotos,queueList,queueCounts,retryErrors,dropPhoto} from './mobile_photos.js';
const API = '/api/v1';
const app = document.getElementById('mobile-app');
let state = { key: null, bootstrap: null, summary: null, dataStatus: null, view: 'dashboard', map: null, selected: null };

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtName = (o) => o?.display_name || o?.official_name || o?.name || 'Cimitir';


async function api(path, options={}){
  const headers = {...(options.headers||{})};
  if(state.key) headers.Authorization = `Binding ${state.key}`;
  if(options.body && !headers['Content-Type']) headers['Content-Type']='application/json';
  const r=await fetch(path,{...options,headers,cache:'no-store'});
  let body={}; try{body=await r.json();}catch(_e){}
  if(!r.ok||body.ok===false){const e=new Error(friendly(r.status,body));e.status=r.status;e.code=body.code;throw e;}
  return body.result;
}
function toast(msg){document.querySelector('.toast')?.remove();const n=document.createElement('div');n.className='toast';n.textContent=msg;document.body.appendChild(n);setTimeout(()=>n.remove(),3200);}

function pairingView(message='',autoCode=''){
  app.innerHTML=`<div class="auth-wrap"><section class="auth-card"><div class="brand-mark">✦</div><h1>Cimitir Mobile</h1><p>Asociază acest dispozitiv o singură dată. Codul se generează din MAIN → <b>Cloud / Telefoane</b>.</p><form id="pair-form"><div class="field"><label>Cod pairing</label><input id="pair-code" class="pair-code" maxlength="28" autocomplete="one-time-code" inputmode="text" placeholder="XXXXX-XXXXX-XXXXX-XXXXX" required></div><div class="field"><label>Numele dispozitivului</label><input id="device-name" maxlength="64" value="${/Android/i.test(navigator.userAgent)?'Telefon / tabletă Android':'Dispozitiv mobil'}"></div><button class="primary" id="pair-submit">Asociază dispozitivul</button><button type="button" class="secondary" id="pair-scan" data-testid="pair-scan">Scanează codul QR</button><video id="pair-video" class="pair-video" playsinline muted hidden></video><div class="error" id="pair-error">${esc(message)}</div></form><p class="micro">Cheia tehnică este păstrată local și nu este afișată în interfața normală.</p></section></div>`;
  const form=document.getElementById('pair-form');
  const inp=document.getElementById('pair-code');
  inp.addEventListener('input',()=>{let s=inp.value.toUpperCase().replace(/[^A-Z2-9]/g,'').slice(0,20);inp.value=s.match(/.{1,5}/g)?.join('-')||s;});
  form.addEventListener('submit',async(e)=>{e.preventDefault();const b=document.getElementById('pair-submit');const er=document.getElementById('pair-error');b.disabled=true;er.textContent='';try{await pairWith(inp.value,document.getElementById('device-name').value);}catch(x){if(state.prevKey){state.key=state.prevKey;state.prevKey=null;toast('Codul din QR nu mai este valid; dispozitivul rămâne asociat ca înainte.');await loadApp();return;}er.textContent=x.status===429?'Prea multe încercări. Așteaptă puțin și încearcă din nou.':!x.status?'Fără conexiune la server. Verifică internetul și adresa Cloud, apoi reîncearcă.':'Cod invalid, expirat, revocat sau deja folosit. Generează un cod nou din MAIN.';}finally{b.disabled=false;}});
  document.getElementById('pair-scan')?.addEventListener('click',()=>scanPairQr(inp,form));
  if(autoCode){inp.value=autoCode;inp.dispatchEvent(new Event('input'));form.requestSubmit();}
}
async function pairWith(code,deviceName){
  const r=await api(`${API}/phone-pairing/redeem`,{method:'POST',body:JSON.stringify({code,device_name:deviceName,client_id:getClientId()})});
  state.key=r.binding_key;await kvSet('binding_key',state.key);await loadApp();
  const b=state.bootstrap;if(b&&state.key)toast(`Asociat automat: ${fmtName(b.organization)} — ${fmtName(b.cemetery)}`);
}
function pairCodeFromText(t){const m=/(?:[#&?]|^)pair=([A-Za-z0-9-]{10,40})/.exec(String(t||''));return m?m[1]:'';}
function takePairCodeFromUrl(){const c=pairCodeFromText(location.hash);if(c)history.replaceState(null,'',location.pathname+location.search);return c;}
async function loadJsQr(){if(window.jsQR)return window.jsQR;await new Promise((res,rej)=>{const sc=document.createElement('script');sc.src='/mobile-assets/jsQR.js';sc.onload=res;sc.onerror=rej;document.head.appendChild(sc);});return window.jsQR;}
async function scanPairQr(inp,form){
  const video=document.getElementById('pair-video');let stream;
  try{stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});}catch(_e){toast('Camera nu este disponibilă sau accesul a fost refuzat. Introdu codul manual sau scanează QR-ul cu camera telefonului.');return;}
  video.srcObject=stream;video.hidden=false;await video.play().catch(()=>{});
  let detect;
  if('BarcodeDetector' in window){const det=new BarcodeDetector({formats:['qr_code']});detect=async()=>(await det.detect(video)).map(x=>x.rawValue);}
  else{const jq=await loadJsQr().catch(()=>null);if(!jq){stream.getTracks().forEach(t=>t.stop());video.hidden=true;toast('Scanarea QR nu este disponibilă pe acest browser. Folosește camera nativă a telefonului.');return;}
    const cv=document.createElement('canvas'),cx=cv.getContext('2d',{willReadFrequently:true});
    detect=async()=>{if(!video.videoWidth)return[];cv.width=video.videoWidth;cv.height=video.videoHeight;cx.drawImage(video,0,0);const im=cx.getImageData(0,0,cv.width,cv.height);const r=jq(im.data,im.width,im.height);return r?[r.data]:[];};}
  let live=true;const stop=()=>{live=false;stream.getTracks().forEach(t=>t.stop());video.hidden=true;};
  const tick=async()=>{if(!live)return;try{const code=(await detect()).map(pairCodeFromText).find(Boolean);if(code){stop();inp.value=code;inp.dispatchEvent(new Event('input'));form.requestSubmit();return;}}catch(_e){}setTimeout(tick,250);};
  setTimeout(stop,60000);tick();
}
function getClientId(){let id=localStorage.getItem('cimitir_mobile_client');if(!id){id=(crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9._:-]/g,'');localStorage.setItem('cimitir_mobile_client',id);}return id;}

function shell(){
  const cem=fmtName(state.bootstrap?.cemetery), dev=state.bootstrap?.device?.device_name||'Dispozitiv mobil';
  app.innerHTML=`<div class="shell"><aside class="sidebar"><div class="side-brand"><div class="brand-mark">✦</div><div><div class="side-title">Cimitir Mobile</div><div class="side-sub">${esc(cem)}</div></div></div><nav class="nav">${navBtn('dashboard','⌂','Acasă')}${navBtn('map','⌖','Hartă')}${navBtn('search','⌕','Căutare')}${navBtn('locuri','▦','Locuri')}${navBtn('inhumati','✦','Înhumați')}${navBtn('contracte','▤','Contracte')}${navBtn('plati','▣','Plăți')}${navBtn('documente','❏','Documente')}</nav><div class="side-foot"><div><span class="online-dot"></span>Cloud conectat</div><button class="logout-link" id="unpair">Dezasociază acest dispozitiv</button></div></aside><div class="main"><header class="topbar"><div><div class="top-title">${esc(cem)}</div></div><div class="top-meta"><span class="net-pill" id="net-pill"></span><button class="sync-btn" id="sync-btn" data-testid="sync-button"></button><span class="device-chip">${esc(dev)}</span></div></header><main class="content" id="view"></main></div></div><nav class="bottom-nav">${bottomBtn('dashboard','⌂','Acasă')}${bottomBtn('map','⌖','Hartă')}${bottomBtn('search','⌕','Caută')}${bottomBtn('modules','▦','Registre')}</nav><div class="sheet-backdrop" id="sheet"><div class="sheet" id="sheet-body"></div></div>`;
  document.querySelectorAll('[data-view]').forEach((b)=>b.addEventListener('click',()=>navigate(b.dataset.view)));
  document.getElementById('sync-btn')?.addEventListener('click',openSyncPanel);renderSyncBtn();renderNet();
  document.getElementById('unpair')?.addEventListener('click',async()=>{const np=(await queueCounts().catch(()=>({total:0}))).total;if(confirm(np?`Atenție: ${np} fotografii nu au fost încă trimise și se vor pierde. Dezasociezi acest dispozitiv?`:'Dezasociezi acest dispozitiv? Va fi necesar un cod nou pentru reconectare.')){await kvDel('binding_key');await mirrorClear();await clearLocalPhotos();state.key=null;pairingView();}});
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
    const scope=JSON.stringify(state.bootstrap.cemetery||{});const prev=await kvGet('mirror_scope');
    if(prev&&prev!==scope){const ob=await outbox();if(ob.length){const q=(await kvGet('outbox_quarantine'))||[];await kvSet('outbox_quarantine',q.concat(ob));await kvSet('outbox',[]);toast('Dispozitivul a fost asociat altui cimitir: modificările nesincronizate de la cel vechi au fost puse deoparte, nu trimise.');}await mirrorClear();}
    await kvSet('mirror_scope',scope);
    await loadSyncInfo();
    shell();renderView();
    if((await outbox()).some(o=>o.state==='pending'))syncNow();else refreshMirror();
  }catch(e){
    if(e.status===401){await kvDel('binding_key');await mirrorClear();await kvDel('bootstrap_cache');await kvDel('summary_cache');state.key=null;pairingView('Asocierea nu mai este validă. Generează un cod nou din MAIN.');return;}
    const cachedBootstrap=await kvGet('bootstrap_cache').catch(()=>null), cachedSummary=await kvGet('summary_cache').catch(()=>null);
    if(cachedBootstrap){state.syncInfo=state.syncInfo||await kvGet('sync_info').catch(()=>null);state.lastSync=state.lastSync||await kvGet('last_sync').catch(()=>null);state.bootstrap=cachedBootstrap;state.summary=cachedSummary||{};shell();renderView();toast('Cloud indisponibil: afișez ultima stare memorată.');}
    else pairingView('Cloud indisponibil momentan. Reîncearcă atunci când revine conexiunea.');
  }
}
function renderView(){const v=document.getElementById('view');if(!v)return;if(state.view==='dashboard')renderDashboard(v);else if(state.view==='search')renderSearch(v);else if(state.view==='map')renderMap(v);else if(LISTS[state.view])renderList(v,state.view);else if(state.view==='documente')renderDocs(v);else renderModules(v);}
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
async function doSearch(q){const box=document.getElementById('search-results');if(!box)return;if(q.trim().length<2){box.className='empty';box.textContent='Introdu cel puțin 2 caractere.';return;}box.className='empty';box.textContent='Se caută…';try{const r=await api(`${API}/mobile/search?q=${encodeURIComponent(q.trim())}`);const groups=[['Înhumați','burials'],['Concesionari','concessionaires'],['Locuri de veci','graves'],['Contracte','contracts']];let html='';let total=0;for(const [title,key] of groups){const a=r[key]||[];if(!a.length)continue;total+=a.length;html+=`<div class="section-title">${title} · ${a.length}</div><div class="result-list">${a.map(x=>searchRow(key,x)).join('')}</div>`;}box.className='';box.innerHTML=total?html:'<div class="empty">Niciun rezultat.</div>';box.querySelectorAll('[data-grave]').forEach(b=>b.onclick=()=>openGrave(b.dataset.grave));}catch(e){if(e.status){box.className='empty';box.textContent=e.status===403?'Nu ai dreptul de căutare.':'Căutarea nu este disponibilă momentan.';return;}const r=await offlineSearch(q.trim(),await outbox());const groups=[['Înhumați','burials'],['Concesionari','concessionaires'],['Locuri de veci','graves'],['Contracte','contracts']];let html='<div class="micro">Căutare în copia offline</div>',total=0;for(const [title,key] of groups){const a=r[key]||[];if(!a.length)continue;total+=a.length;html+=`<div class="section-title">${title} · ${a.length}</div><div class="result-list">${a.map(x=>searchRow(key,x)).join('')}</div>`;}box.className='';box.innerHTML=total?html:'<div class="empty">Niciun rezultat în copia offline.</div>';box.querySelectorAll('[data-grave]').forEach(b=>b.onclick=()=>openGrave(b.dataset.grave));}}
function searchRow(type,x){let title='',sub='',uid=type==='graves'?x.uid:(x.grave_uid||'');if(type==='burials'){title=x.name;sub=`Înhumat · ${x.grave_code||'loc neasociat'}${x.death_date?' · deces '+x.death_date:''}`;}else if(type==='concessionaires'){title=x.name;sub=`Concesionar${x.grave_code?' · loc '+x.grave_code:''}${x.phone?' · '+x.phone:''}`;}else if(type==='graves'){title=`Loc ${x.code}`;sub=[x.sector&&`sector ${x.sector}`,x.parcel&&`parcela ${x.parcel}`,x.row&&`rând ${x.row}`].filter(Boolean).join(' · ');uid=x.uid;}else{title=`Contract ${x.number||'—'}`;sub=`${x.concessionaire||'—'}${x.grave_code?' · loc '+x.grave_code:''}`;}return `<button class="result" ${uid?`data-grave="${esc(uid)}"`:'disabled aria-disabled="true"'}><div><b>${esc(title)}</b><small>${esc(sub||'')}</small></div><span class="pill">${type==='graves'?(x.occupied?'Ocupat':'Disponibil'):(uid?'Deschide':'Fără loc asociat')}</span></button>`;}

async function renderMap(v){v.innerHTML=`<div class="page-head"><div><h1>Harta cimitirului</h1><p>Atinge un loc pentru fișa completă. Pan și pinch/zoom funcționează pe telefon.</p></div></div><div class="map-card"><div class="map-toolbar"><input id="map-q" placeholder="Filtrează după codul locului…"><button id="map-home">Încadrează</button><button id="map-reload">Actualizează</button></div><div id="map-stage" class="empty">Se încarcă harta…</div><div class="map-legend" data-testid="map-legend"><span style="color:#4f8a68">■</span> Disponibil &nbsp; <span style="color:#9f5a52">■</span> Ocupat &nbsp; <span style="color:#c9993a">■</span> Rezervat / concesionat</div></div>`;document.getElementById('map-reload').onclick=()=>renderMap(v);try{state.map=await api(`${API}/mobile/map`);drawMap(state.map);}catch(e){if(e.status){document.getElementById('map-stage').textContent='Harta nu este disponibilă momentan.';return;}state.map=await offlineMap(await outbox());drawMap(state.map);}}
function drawMap(data){const stage=document.getElementById('map-stage');if(!stage)return;const plots=(data.plots||[]).filter(p=>Array.isArray(p.points)&&p.points.length>=3);const sectors=(data.sectors||[]).filter(s=>Array.isArray(s.points)&&s.points.length>=3);if(!plots.length){stage.className='empty';stage.textContent=dataNotice()||'Nu există încă geometrii sincronizate pentru hartă. Folosește Actualizează după sincronizarea MAIN.';return;}const all=[...plots,...sectors].flatMap(x=>x.points);const xs=all.map(p=>Number(p[0])),ys=all.map(p=>Number(p[1]));const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);const pad=Math.max(10,(maxX-minX+maxY-minY)*.04);const home={x:minX-pad,y:minY-pad,w:maxX-minX+2*pad,h:maxY-minY+2*pad};let vb={...home};stage.className='';stage.innerHTML=`<svg id="cem-map" class="map-svg" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}" preserveAspectRatio="xMidYMid meet">${sectors.map(s=>`<polygon points="${pts(s.points)}" fill="rgba(24,59,45,.035)" stroke="rgba(24,59,45,.38)" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"></polygon>`).join('')}${plots.map(p=>`<polygon class="plot" data-uid="${esc(p.uid)}" data-code="${esc(p.code)}" points="${pts(p.points)}" fill="${plotColor(p)}" stroke="#fff" stroke-width=".7" vector-effect="non-scaling-stroke"><title>${esc(p.code)}</title></polygon>`).join('')}</svg>`;const svg=document.getElementById('cem-map');const set=()=>svg.setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`);let drag=null,moved=false;const ptrs=new Map();let pinch=0;const zoomAt=(f,cx,cy)=>{const r=svg.getBoundingClientRect(),mx=vb.x+(cx-r.left)/r.width*vb.w,my=vb.y+(cy-r.top)/r.height*vb.h;vb={x:mx-(mx-vb.x)*f,y:my-(my-vb.y)*f,w:vb.w*f,h:vb.h*f};set();};svg.style.touchAction='none';svg.addEventListener('pointerdown',e=>{ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});drag={x:e.clientX,y:e.clientY};moved=false;pinch=0;svg.setPointerCapture?.(e.pointerId);});svg.addEventListener('pointermove',e=>{if(!ptrs.has(e.pointerId))return;ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});if(ptrs.size>=2){const [a,b]=[...ptrs.values()],d=Math.hypot(a.x-b.x,a.y-b.y);if(pinch){zoomAt(pinch/d,(a.x+b.x)/2,(a.y+b.y)/2);}pinch=d;moved=true;return;}if(!drag)return;const r=svg.getBoundingClientRect(),dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)moved=true;vb.x-=dx/r.width*vb.w;vb.y-=dy/r.height*vb.h;drag={x:e.clientX,y:e.clientY};set();});const up=e=>{ptrs.delete(e.pointerId);pinch=0;drag=ptrs.size===1?{...[...ptrs.values()][0]}:null;};svg.addEventListener('pointerup',up);svg.addEventListener('pointercancel',up);svg.addEventListener('wheel',e=>{e.preventDefault();zoomAt(e.deltaY<0?.84:1.19,e.clientX,e.clientY);},{passive:false});svg.querySelectorAll('.plot').forEach(p=>p.addEventListener('click',()=>{if(!moved)openGrave(p.dataset.uid);}));document.getElementById('map-home').onclick=()=>{vb={...home};set();};document.getElementById('map-q').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();svg.querySelectorAll('.plot').forEach(p=>p.style.opacity=!q||p.dataset.code.toLowerCase().includes(q)?'1':'.12');});}
function pts(a){return a.map(p=>`${Number(p[0])},${Number(p[1])}`).join(' ')}


function detailBlock(title,rows){return `<section class="detail-card"><h3>${esc(title)}</h3>${rows.map(([k,v])=>`<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</section>`}

/* ---------------- Sync v2: stare, outbox, reluare ---------------- */
const ENTITY_ORDER=['locuri_de_veci','concesionari','contracte','inhumati','plati','amenajari_avize'];
const REASONS={not_authorized:'Nu ai dreptul de editare pentru acest registru (se acordă din MAIN).',delete_not_allowed:'Ștergerea nu este permisă de pe telefon.',version_mismatch:'Înregistrarea a fost modificată între timp. Se păstrează varianta din MAIN.',natural_key:'Există deja un loc cu acest cod.',deleted_on_server:'Înregistrarea a fost ștearsă între timp.',date_invalide:'Date invalide.'};
const can=(e)=>!!state.syncInfo?.editable?.includes(e);
const newId=()=>((crypto.randomUUID?.()||`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}0000000000000000`)).replace(/-/g,'').slice(0,32);
async function outbox(){return (await kvGet('outbox').catch(()=>null))||[];}
async function saveOutbox(o){await kvSet('outbox',o);renderSyncBtn();}
async function loadSyncInfo(){try{state.syncInfo=await api(`${API}/sync-v2/status`);await kvSet('sync_info',state.syncInfo);}catch(e){if(e.status===401)throw e;state.syncInfo=state.syncInfo||await kvGet('sync_info').catch(()=>null);}state.lastSync=state.lastSync||await kvGet('last_sync').catch(()=>null);}
async function renderSyncBtn(){const b=document.getElementById('sync-btn');if(!b)return;const ob=await outbox();const pending=ob.filter(o=>o.state==='pending').length,bad=ob.filter(o=>o.state==='conflict'||o.state==='error').length;const ls=state.lastSync||{};const ph=await queueCounts().catch(()=>({pending:0,error:0}));const pendingAll=pending+ph.pending,badAll=bad+ph.error;b.dataset.state=state.syncing?'busy':badAll?'bad':ls.offline?'offline':pendingAll?'pending':'ok';b.textContent=state.syncing?'Se sincronizează…':badAll?`Sincronizează · ${badAll} de rezolvat`:pendingAll?`Sincronizează · ${pendingAll} în coadă${ph.pending?` (${ph.pending} foto)`:''}`:ls.offline?'Offline · reîncearcă':'Sincronizează';b.disabled=!!state.syncing;}
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
    const ph=await flushPhotos(api,API);res.photos=ph.sent;res.errors+=ph.errors;if(ph.offline){res.offline=true;res.error='Fără conexiune la internet. Fotografiile rămân în coadă.';}
    state.summary=await api(`${API}/mobile/summary`);state.dataStatus=await api(`${API}/mobile/data-status`).catch(()=>null);state.map=null;
    await loadSyncInfo();await kvSet('summary_cache',state.summary);await refreshMirror();
  }catch(e){
    if(e.status===401){await kvDel('binding_key');await mirrorClear();state.key=null;state.syncing=false;pairingView('Asocierea nu mai este validă. Generează un cod nou din MAIN.');return;}
    res.offline=!e.status;res.error=res.offline?'Fără conexiune la internet. Modificările rămân în coadă.':(e.message||'Eroare');res.errors+=res.offline?0:1;
  }
  state.syncing=false;state.lastSync=res;await kvSet('last_sync',res);renderSyncBtn();if(!document.activeElement?.closest?.('#view input'))renderView();
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

(async function boot(){if('serviceWorker' in navigator){navigator.serviceWorker.register('/mobile/sw.js',{scope:'/mobile/'}).catch(()=>{});}let link=takePairCodeFromUrl();try{state.key=await kvGet('binding_key');}catch(_e){}if(link&&state.key&&!confirm('Acest dispozitiv este deja asociat. Îl reasociezi cu noul cod?'))link='';if(link){state.prevKey=state.key;state.key=null;pairingView('',link);}else if(state.key)await loadApp();else pairingView();})();

/* ---------------- Mobile v2 (Etapele B/C/E): registre, fișă, formulare, offline ---------------- */
const plotColor=(p)=>p.occupied?'#9f5a52':/rezerv|concesion|vândut|vandut/i.test(`${p.state||''} ${p.availability||''}`)?'#c9993a':'#4f8a68';
const TODAY=()=>new Date().toISOString().slice(0,10);
const TIPS=['Taxă concesiune','Taxă întreținere','Taxă înhumare','Alt tip'];
const F=(k,l,t='text',x={})=>({k,l,t,...x});
const FORMS={
  locuri_de_veci:{title:'Loc de veci',fields:[F('sector','Sector'),F('parcela','Parcelă'),F('rand','Rând'),F('numar','Număr'),F('capacitate','Capacitate'),F('suprafata','Suprafață (m²)','number'),F('stare','Stare'),F('disponibilitate','Disponibilitate'),F('observatii','Observații','textarea')],label:d=>`Loc ${d.cod_loc||''}`.trim()},
  concesionari:{title:'Concesionar',fields:[F('nume','Nume','text',{req:1}),F('prenume','Prenume'),F('cnp','CNP'),F('telefon','Telefon','tel'),F('email','E-mail','email'),F('adresa','Adresă'),F('observatii','Observații','textarea')],label:d=>`Concesionar: ${d.nume||''} ${d.prenume||''}`.trim()},
  inhumati:{title:'Persoană înhumată',fields:[F('nume','Nume','text',{req:1}),F('prenume','Prenume'),F('cnp','CNP'),F('sex','Sex'),F('data_nasterii','Data nașterii','date'),F('data_deces','Data decesului','date'),F('data_inhumare','Data înhumării','date'),F('nr_certificat_deces','Nr. certificat deces'),F('nr_adeverinta_inhumare','Nr. adeverință înhumare'),F('observatii','Observații','textarea')],label:d=>`Înhumat: ${d.nume||''} ${d.prenume||''}`.trim()},
  contracte:{title:'Contract',fields:[F('numar_contract','Număr contract','text',{req:1}),F('concesionar_uid','Concesionar','select',{req:1,opts:'concessionaires'}),F('data_contract','Data contractului','date'),F('data_inceput','Valabil de la','date'),F('data_sfarsit','Valabil până la','date'),F('valabilitate','Valabilitate'),F('observatii','Observații','textarea')],label:d=>`Contract ${d.numar_contract||''}`.trim(),confirm:d=>`Salvezi contractul ${d.numar_contract}?`},
  plati:{title:'Plată',fields:[F('suma','Sumă (lei)','number',{req:1}),F('contract_uid','Contract','select',{opts:'contracts'}),F('data_plata','Data plății','date',{def:TODAY}),F('tip','Tip','select',{opts:TIPS,def:()=>TIPS[0]}),F('chitanta_nr','Nr. chitanță'),F('observatii','Observații','textarea')],label:d=>`Plată ${d.suma||''} lei`,confirm:d=>`Confirmi înregistrarea plății de ${d.suma} lei?`},
  amenajari_avize:{title:'Solicitare aviz lucrări funerare',fields:[F('nr_aviz','Nr. aviz / solicitare'),F('data_aviz','Data','date',{def:TODAY}),F('lucrare','Lucrarea solicitată','textarea',{req:1})],label:d=>`Aviz: ${d.lucrare||d.nr_aviz||''}`.slice(0,60)}
};
const NUMERIC=['suma','suprafata'];
const friendly=(s,b)=>b?.message&&s<500&&s!==403&&s!==404?b.message:({401:'Asocierea dispozitivului nu mai este validă.',403:'Nu ai dreptul pentru această operațiune.',404:'Elementul nu a fost găsit.',429:'Prea multe cereri. Reîncearcă peste câteva secunde.'}[s]||(s>=500?'Serverul este momentan indisponibil. Modificările rămân în coadă.':'Cererea nu a putut fi procesată.'));
function renderNet(){const p=document.getElementById('net-pill');if(!p)return;const on=navigator.onLine!==false&&!state.netDown;p.textContent=on?'Conectat':'Offline';p.dataset.state=on?'on':'off';p.setAttribute('data-testid','net-pill');}
window.addEventListener('online',()=>{state.netDown=false;renderNet();toast('Conexiune restabilită. Se sincronizează modificările.');syncNow();});
window.addEventListener('offline',()=>{renderNet();toast('Fără internet. Poți consulta datele salvate și introduce modificări; se trimit la reconectare.');});
async function refreshMirror(){try{const r=await mirrorSync((p)=>api(p),API);state.mirrorAt=Date.now();state.netDown=false;renderNet();if(r.denied.length)state.denied=r.denied;return r;}catch(e){if(!e.status){state.netDown=true;renderNet();}return null;}}
async function getRow(entity,uid){
  try{return await api(`${API}/sync-v2/row?entity=${entity}&uid=${encodeURIComponent(uid)}`);}
  catch(e){if(e.status)throw e;const {rowsOf}=await import('./mobile_offline.js');const r=(await rowsOf(entity)).find(x=>x.uid===uid);if(!r)throw e;return r;}
}
async function concessionOptions(){const {rowsOf}=await import('./mobile_offline.js');const out=new Map();(state.currentGrave?.contracts||[]).forEach(c=>c.concessionaire_uid&&out.set(c.concessionaire_uid,`${c.nume||''} ${c.prenume||''}`.trim()));(await rowsOf('concesionari')).forEach(r=>out.set(r.uid,`${r.data.nume||''} ${r.data.prenume||''}`.trim()));(await outbox()).filter(o=>o.entity==='concesionari').forEach(o=>out.set(o.uid,`${o.data.nume||''} ${o.data.prenume||''}`.trim()+' (nesincronizat)'));return [...out.entries()].sort((a,b)=>a[1].localeCompare(b[1]));}
function contractOptions(){return (state.currentGrave?.contracts||[]).map(c=>[c.uid,`Contract ${c.numar_contract||'—'} · ${`${c.nume||''} ${c.prenume||''}`.trim()}`]);}

async function openRecordForm(entity,uid){
  const f=FORMS[entity];if(!f||!can(entity))return;const body=document.getElementById('sheet-body');state.panel=false;
  let row=null;
  if(uid){try{row=await getRow(entity,uid);}catch(_e){toast('Înregistrarea nu poate fi citită acum. Reîncearcă.');return;}}
  const cur=row?.data||{};const g=state.currentGrave?.grave||{};
  const draftKey=`draft:${entity}:${uid||'nou'}`;const draft=(await kvGet(draftKey).catch(()=>null))||{};
  const opts={concessionaires:await concessionOptions(),contracts:contractOptions()};
  const val=(x)=>draft[x.k]??cur[x.k]??(x.def?x.def():'')??'';
  const input=(x)=>{const id=`f-${x.k}`,v=esc(val(x)),req=x.req?' required':'';
    if(x.t==='textarea')return `<textarea id="${id}" data-testid="field-${x.k}" name="${x.k}" rows="3"${req}>${v}</textarea>`;
    if(x.t==='select'){const list=Array.isArray(x.opts)?x.opts.map(o=>[o,o]):(opts[x.opts]||[]);return `<select id="${id}" data-testid="field-${x.k}" name="${x.k}"${req}><option value="">— alege —</option>${list.map(([a,b])=>`<option value="${esc(a)}"${String(val(x))===String(a)?' selected':''}>${esc(b)}</option>`).join('')}</select>`;}
    return `<input id="${id}" data-testid="field-${x.k}" name="${x.k}" type="${x.t==='number'?'text':x.t}"${x.t==='number'?' inputmode="decimal"':''} value="${v}"${req} autocomplete="off">`;};
  body.innerHTML=`<div class="sheet-head"><button id="sheet-x" data-testid="form-close">×</button><div class="micro">${row?'EDITARE':'ADĂUGARE'}</div><h2>${esc(f.title)}</h2>${g.cod_loc&&entity!=='concesionari'?`<div>Loc ${esc(g.cod_loc)}</div>`:''}</div><div class="sheet-body"><form id="rec-form" novalidate>${f.fields.map(x=>`<div class="field"><label for="f-${x.k}">${esc(x.l)}${x.req?' *':''}</label>${input(x)}</div>`).join('')}<div class="micro" id="form-err" style="color:var(--danger);min-height:18px" data-testid="form-error"></div><button class="edit-btn" id="rec-save" type="submit" data-testid="record-save">Salvează</button></form></div>`;
  const close=()=>document.getElementById('sheet').classList.remove('show');
  document.getElementById('sheet-x').onclick=close;
  const form=document.getElementById('rec-form');
  form.oninput=()=>kvSet(draftKey,Object.fromEntries(new FormData(form).entries()));
  form.onsubmit=async(e)=>{e.preventDefault();if(state.saving)return;
    const vals=Object.fromEntries(new FormData(form).entries());const er=document.getElementById('form-err');
    for(const x of f.fields){if(x.req&&!String(vals[x.k]||'').trim()){er.textContent=`Completează câmpul „${x.l}”.`;return;}if(x.t==='date'&&vals[x.k]&&!/^\d{4}-\d{2}-\d{2}$/.test(vals[x.k])){er.textContent=`Data din „${x.l}” nu este validă.`;return;}}
    for(const k of NUMERIC)if(k in vals){if(vals[k]===''){vals[k]=null;continue;}const n=Number(String(vals[k]).replace(',','.'));if(!isFinite(n)||n<0||(k==='suma'&&n<=0)){er.textContent=k==='suma'?'Suma trebuie să fie un număr pozitiv.':'Valoare numerică invalidă.';return;}vals[k]=n;}
    const data={...cur,...vals};
    if(entity==='inhumati'&&!row)data.loc_uid=g.uid;
    if(entity==='contracte'){data.loc_uid=row?.data?.loc_uid||g.uid;data.cod_loc=g.cod_loc;}
    if(entity==='plati'){data.cod_loc=g.cod_loc;if(!data.contract_uid&&contractOptions().length===1)data.contract_uid=contractOptions()[0][0];}
    if(entity==='amenajari_avize'){data.cod_loc=g.cod_loc;}
    if(f.confirm&&!confirm(f.confirm(data)))return;
    state.saving=true;const b=document.getElementById('rec-save');b.disabled=true;b.textContent='Se salvează…';
    try{
      const op={op_id:'m-'+newId(),entity,uid:row?.uid||newId(),action:'upsert',base_version:row?.version||0,data,state:'pending',label:f.label({...data,cod_loc:g.cod_loc})};
      let ob=(await outbox()).filter(o=>!(o.uid===op.uid&&o.state==='pending'));ob.push(op);await saveOutbox(ob);await kvDel(draftKey);
      close();await syncNow();
    }finally{state.saving=false;}
  };
}

function listBlock(title,arr,titleFn,subFn,entity,uidKey,addLabel){arr=arr||[];const ed=entity&&can(entity);return `<section class="detail-card"><h3>${esc(title)} · ${arr.length}</h3>${arr.length?arr.map(x=>`<div class="kv"><div><b>${esc(titleFn(x))}</b><div class="micro">${esc(subFn(x))}${x._pending?' · nesincronizat':''}</div></div>${ed&&x[uidKey]?`<button class="edit-btn small" data-edit="${entity}" data-uid="${esc(x[uidKey])}" data-testid="edit-${entity}">Editează</button>`:''}</div>`).join(''):'<div class="micro">Nicio înregistrare.</div>'}${entity&&addLabel&&ed?`<button class="edit-btn" data-add="${entity}" data-testid="add-${entity}">${esc(addLabel)}</button>`:''}</section>`}
const EVT={upsert:'Modificat',delete:'Șters'};const ENT_RO={locuri_de_veci:'loc',concesionari:'concesionar',contracte:'contract',inhumati:'înhumat',plati:'plată',amenajari_avize:'aviz'};
async function loadHistory(uid){try{const h=await api(`${API}/sync-v2/history?entity=locuri_de_veci&uid=${encodeURIComponent(uid)}&limit=30`);return h.events||[];}catch(_e){return null;}}
function historyBlock(ev){if(ev===null)return '<section class="detail-card"><h3>Istoric modificări</h3><div class="micro">Disponibil doar online.</div></section>';return `<section class="detail-card" data-testid="grave-history"><h3>Istoric modificări · ${ev.length}</h3>${ev.length?ev.slice(0,12).map(e=>`<div class="kv"><div><b>${esc(EVT[e.action]||e.action)} ${esc(ENT_RO[e.entity]||'')}${e.status==='conflict'?' (conflict)':''}</b><div class="micro">${esc(e.device_type==='MAIN'?'MAIN':(e.device||'Telefon'))} · ${esc(new Date(e.created_at).toLocaleString('ro-RO'))}</div></div></div>`).join(''):'<div class="micro">Fără modificări înregistrate.</div>'}</section>`;}

async function openGrave(uid){
  if(!uid)return;const back=document.getElementById('sheet'),body=document.getElementById('sheet-body');back.classList.add('show');body.innerHTML='<div class="sheet-body">Se încarcă fișa…</div>';back.onclick=(e)=>{if(e.target===back)back.classList.remove('show');};
  let d=null,offline=false,hist=null;
  try{d=await api(`${API}/mobile/graves/${encodeURIComponent(uid)}`);hist=await loadHistory(uid);}
  catch(e){if(e.status===403){body.innerHTML='<div class="sheet-body">Nu ai dreptul să vezi această fișă.</div>';return;}if(e.status&&e.status!==404||false){body.innerHTML='<div class="sheet-body">Fișa nu a putut fi încărcată.</div>';return;}}
  const ob=await outbox();
  if(!d){d=await offlineGrave(uid,ob);offline=!!d;}
  else{const pend=(e)=>ob.filter(o=>o.entity===e&&o.state==='pending'&&!(d[{inhumati:'burials',plati:'payments',amenajari_avize:'permits',contracte:'contracts'}[e]]||[]).some(x=>x.uid===o.uid));
    d.burials=(d.burials||[]).concat(pend('inhumati').filter(o=>o.data?.loc_uid===d.grave.uid).map(o=>({uid:o.uid,...o.data,_pending:true})));
    d.payments=(d.payments||[]).concat(pend('plati').filter(o=>o.data?.cod_loc===d.grave.cod_loc).map(o=>({uid:o.uid,...o.data,_pending:true})));
    d.permits=(d.permits||[]).concat(pend('amenajari_avize').filter(o=>o.data?.cod_loc===d.grave.cod_loc).map(o=>({uid:o.uid,...o.data,_pending:true})));}
  if(!d){body.innerHTML='<div class="sheet-body">Fișa nu este disponibilă offline. Conectează-te și sincronizează.</div>';return;}
  state.currentGrave=d;const g=d.grave||{};
  body.innerHTML=`<div class="sheet-head"><button id="sheet-x" data-testid="sheet-close">×</button><div class="micro">FIȘĂ LOC DE VECI${offline?' · COPIE OFFLINE':''}</div><h2>${esc(g.cod_loc||'Loc')}</h2><div>${esc([g.sector&&'Sector '+g.sector,g.parcela&&'Parcela '+g.parcela,g.rand&&'Rând '+g.rand].filter(Boolean).join(' · '))}</div></div><div class="sheet-body">${detailBlock('Date loc',[['Stare',g.stare||g.disponibilitate||'—'],['Suprafață',g.suprafata?g.suprafata+' m²':'—'],['Capacitate',g.capacitate||'—'],['Nr. vechi',g.nr_vechi||'—'],['Observații',g.observatii||'—']])}${can('locuri_de_veci')?'<button class="edit-btn" data-edit="locuri_de_veci" data-uid="'+esc(g.uid)+'" data-testid="edit-grave">Editează locul</button>':''}${listBlock('Concesionari / contracte',d.contracts,(x)=>`${x.nume||''} ${x.prenume||''}`.trim()||'Concesionar fără nume',x=>`Contract ${x.numar_contract||'—'} · ${x.data_contract||'fără dată'}${x.telefon?' · '+x.telefon:''}`,'contracte','uid','Adaugă contract')}${can('concesionari')?`<button class="edit-btn" data-add="concesionari" data-testid="add-concesionari">Adaugă concesionar</button>${(d.contracts||[]).filter(c=>c.concessionaire_uid).map(c=>`<button class="edit-btn small" data-edit="concesionari" data-uid="${esc(c.concessionaire_uid)}" data-testid="edit-concesionari">Editează ${esc((c.nume||'concesionar'))}</button>`).join('')}`:''}${listBlock('Înhumați',d.burials,(x)=>`${x.nume||''} ${x.prenume||''}`.trim(),x=>`Deces ${x.data_deces||'—'} · Înhumare ${x.data_inhumare||'—'}`,'inhumati','uid','Adaugă înhumat')}${listBlock('Plăți și obligații',d.payments,(x)=>`${x.suma??0} lei${x.tip?' · '+x.tip:''}`,x=>`${x.chitanta_nr||'Fără chitanță'} · ${x.data_plata||'—'}`,'plati','uid','Înregistrează plată')}${listBlock('Avize / lucrări funerare',d.permits,(x)=>`Aviz ${x.nr_aviz||'—'}`,x=>`${x.lucrare||'Lucrare'} · ${x.data_aviz||'fără dată'}`,'amenajari_avize','uid','Solicită aviz')}<section class="detail-card"><h3>Documente și fotografii</h3><div class="micro">Încărcarea documentelor/fotografiilor nu este încă disponibilă în această versiune.</div></section>${historyBlock(offline?null:hist)}</div>`;
  document.getElementById('sheet-x').onclick=()=>back.classList.remove('show');
  body.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>openRecordForm(b.dataset.edit,b.dataset.uid||g.uid));
  body.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>openRecordForm(b.dataset.add,null));
  const sb=body.querySelector('.sheet-body');if(sb){sb.appendChild(docsSection(g,d));}
}

const LISTS={locuri:['Locuri de veci','Toate locurile, cu filtru după cod, sector sau stare.'],inhumati:['Înhumați','Persoane înhumate, cu filtru după nume sau loc.'],contracte:['Contracte','Contracte și concesionari.'],plati:['Plăți','Plăți și taxe înregistrate.']};
async function renderList(v,kind){
  const [t,sub]=LISTS[kind];v.innerHTML=`<div class="page-head"><div><h1>${t}</h1><p>${sub}</p></div></div><div class="big-search"><input id="list-q" type="search" placeholder="Filtrează…" data-testid="list-filter" autocomplete="off"></div><div id="list-box" class="result-list"><div class="empty">Se încarcă…</div></div>`;
  const draw=async()=>{const box=document.getElementById('list-box');if(!box)return;const items=await offlineList(kind,document.getElementById('list-q')?.value||'',await outbox());box.innerHTML=items.length?items.map((x,i)=>`<button class="result" data-i="${i}" ${x.grave?`data-grave="${esc(x.grave)}"`:'disabled'}><div><b>${esc(x.t)}</b><small>${esc(x.s)}${x.p?' · nesincronizat':''}</small></div><span class="pill">${x.grave?'Deschide':'Fără loc'}</span></button>`).join(''):`<div class="empty">${state.mirrorAt||await mirrorAt()?'Nicio înregistrare.':'Datele nu sunt încă descărcate. Apasă Sincronizează cu internet.'}</div>`;box.querySelectorAll('[data-grave]').forEach(b=>b.onclick=()=>openGrave(b.dataset.grave));};
  document.getElementById('list-q').oninput=()=>draw();
  if(!(await mirrorAt()))await refreshMirror();
  draw();
}
const DOC_TYPE_LABEL={fotografie_loc:'Fotografie loc',contract_concesiune:'Contract',chitanta:'Chitanță',alt_document:'Document',aviz_constructie:'Aviz'};
const canUpload=()=>(state.syncInfo?.capabilities||[]).includes('upload_documents');
async function clearLocalPhotos(){try{for(const it of await queueList())await dropPhoto(it.id);}catch(_e){}try{await caches.delete('cimitir-mobile-docs');}catch(_e){}}
async function listCloudDocs(){const out=[];let since=0;for(let i=0;i<10;i++){const r=await api(`${API}/sync-v2/documents?since=${since}&limit=500`);out.push(...r.documents.filter(x=>!x.deleted));since=r.cursor;if(!r.more)break;}return out;}
async function docBlobUrl(doc){
  const key=`/__doc/${doc.sha256}`;let cache=null;try{cache=await caches.open('cimitir-mobile-docs');}catch(_e){}
  let res=cache?await cache.match(key):null;
  if(!res){const r=await fetch(`${API}/sync-v2/documents/${encodeURIComponent(doc.uid)}/file`,{headers:{Authorization:`Binding ${state.key}`},cache:'no-store'});if(!r.ok){const e=new Error('Documentul nu poate fi deschis acum.');e.status=r.status;throw e;}
    const blob=await r.blob();if(cache)await cache.put(key,new Response(blob.slice(0),{headers:{'Content-Type':doc.mime}}));return URL.createObjectURL(blob);}
  return URL.createObjectURL(await res.blob());
}
async function openDoc(doc){try{const u=await docBlobUrl(doc);if(doc.mime==='application/pdf'){window.open(u,'_blank','noopener');return;}
  const back=document.createElement('div');back.className='photo-viewer';back.setAttribute('data-testid','photo-viewer');back.innerHTML=`<img alt="${esc(doc.filename)}" src="${u}"><button type="button">Închide</button>`;back.onclick=()=>{back.remove();URL.revokeObjectURL(u);};document.body.appendChild(back);}
  catch(e){toast(e.status?e.message:'Documentul nu a fost încă memorat pe acest telefon; deschide-l o dată cu internet.');}}
async function pickPhoto(entity_uid,caption,capture){
  return new Promise((resolve)=>{const inp=document.createElement('input');inp.type='file';inp.accept='image/jpeg,image/png,application/pdf';if(capture)inp.setAttribute('capture','environment');
    inp.onchange=async()=>{const f=inp.files&&inp.files[0];if(!f){resolve(null);return;}const r=await enqueue(f,{entity_uid,caption}).catch(()=>({ok:false,error:'Fișierul nu a putut fi salvat pe telefon.'}));
      toast(!r.ok?r.error:r.duplicate?'Această fotografie este deja în coadă.':navigator.onLine?'Fotografie salvată; se trimite…':'Fotografie salvată pe telefon; se trimite când revine internetul.');
      renderSyncBtn();if(r.ok&&navigator.onLine)syncNow();resolve(r);};inp.click();});
}
function docsSection(g,d){
  const sec=document.createElement('section');sec.className='detail-card';sec.setAttribute('data-testid','grave-documents');
  const kids=new Set([g.uid,...(d.contracts||[]).map(x=>x.uid),...(d.burials||[]).map(x=>x.uid),...(d.payments||[]).map(x=>x.uid),...(d.permits||[]).map(x=>x.uid)]);
  const draw=async()=>{
    const q=(await queueList().catch(()=>[])).filter(x=>x.entity_uid===g.uid);let cloud=[],offline=false;
    try{cloud=(await listCloudDocs()).filter(x=>kids.has(x.entity_uid));}catch(_e){offline=true;}
    sec.innerHTML=`<h3>Documente și fotografii · ${cloud.length+q.length}</h3>${q.map(x=>`<div class="kv" data-testid="photo-unsynced"><div><b>${esc(x.filename)}</b><div class="micro">${x.state==='error'?esc(x.error||'Eroare'):'Nesincronizată · se trimite când există internet'}</div></div><span class="pill warn">${x.state==='error'?'Eroare':'În coadă'}</span></div>`).join('')}${cloud.map((x,i)=>`<button class="result" data-di="${i}" data-testid="grave-doc-row"><div><b>${esc(DOC_TYPE_LABEL[x.doc_type]||x.doc_type||'Document')}</b><small>${esc(x.filename)}</small></div><span class="pill">${x.origin==='MOBILE'?'Telefon':'MAIN'}</span></button>`).join('')}${!cloud.length&&!q.length?`<div class="micro">${offline?'Documentele se pot vedea doar cu conexiune la Cloud.':'Niciun document sau fotografie.'}</div>`:''}${canUpload()?'<div class="btn-row"><button class="edit-btn" data-testid="add-grave-photo">Fotografiază</button><button class="edit-btn" data-testid="add-grave-gallery">Alege din galerie</button></div>':''}`;
    sec.querySelectorAll('[data-di]').forEach(b=>b.onclick=()=>openDoc(cloud[+b.dataset.di]));
    sec.querySelector('[data-testid=add-grave-photo]')?.addEventListener('click',async()=>{await pickPhoto(g.uid,g.cod_loc||'',true);draw();});
    sec.querySelector('[data-testid=add-grave-gallery]')?.addEventListener('click',async()=>{await pickPhoto(g.uid,g.cod_loc||'',false);draw();});
  };
  draw();return sec;
}
async function renderDocs(v){
  v.innerHTML='<div class="page-head"><div><h1>Documente</h1><p>Documentele și fotografiile cimitirului (din MAIN și de pe telefoane). Fotografiile făcute fără internet se trimit automat la reconectare.</p></div></div><div id="docs-box" class="result-list" data-testid="docs-list"><div class="empty">Se încarcă…</div></div>';
  const box=document.getElementById('docs-box');const q=await queueList().catch(()=>[]);let cloud=[],offline=false;
  try{cloud=await listCloudDocs();}catch(_e){offline=true;}
  cloud.sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
  const pend=q.map(x=>`<div class="result" data-testid="doc-unsynced"><div><b>${esc(x.filename)}</b><small>${x.state==='error'?esc(x.error||'Eroare'):'Nesincronizată'} · ${esc(new Date(x.created).toLocaleString('ro-RO'))}</small></div><span class="pill warn">${x.state==='error'?'Eroare':'În coadă'}</span></div>`).join('');
  box.innerHTML=pend+(cloud.length?cloud.map((x,i)=>`<button class="result" data-i="${i}" data-testid="doc-row"><div><b>${esc(DOC_TYPE_LABEL[x.doc_type]||x.doc_type||'Document')}</b><small>${esc(x.filename)} · ${esc(String(x.created_at||'').slice(0,10))}</small></div><span class="pill">${x.origin==='MOBILE'?'Telefon':'MAIN'}</span></button>`).join(''):(q.length?'':`<div class="empty">${offline?'Documentele se pot vedea doar cu conexiune la Cloud.':'Niciun document sau fotografie încă.'}</div>`));
  box.querySelectorAll('[data-i]').forEach(b=>b.onclick=()=>openDoc(cloud[+b.dataset.i]));
  if(q.some(x=>x.state==='error')){const rb=document.createElement('button');rb.className='secondary';rb.textContent='Reîncearcă fotografiile cu eroare';rb.onclick=async()=>{await retryErrors();syncNow();};box.prepend(rb);}
}
setInterval(()=>{if(state.key&&navigator.onLine!==false&&!state.syncing)queueCounts().then(c=>{if(c.pending)syncNow();}).catch(()=>{});},45000);
function renderModules(v){const rows=[['locuri','▦','Locuri','Listă și filtre'],['inhumati','✦','Înhumați','Persoane înhumate'],['contracte','▤','Contracte','Contracte și concesionari'],['plati','▣','Plăți','Taxe și plăți'],['documente','❏','Documente','Fotografii și acte'],['sync','⇅','Sincronizare','Stare, coadă, conflicte']];v.innerHTML=`<div class="page-head"><div><h1>Registre</h1><p>Toate registrele funcționează și offline, din copia locală autorizată.</p></div></div><div class="module-grid">${rows.map(r=>`<button class="module-card" data-go="${r[0]}" data-testid="module-${r[0]}"><span class="ico">${r[1]}</span><strong>${r[2]}</strong><small>${r[3]}</small></button>`).join('')}</div>`;v.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>b.dataset.go==='sync'?openSyncPanel():navigate(b.dataset.go));}
