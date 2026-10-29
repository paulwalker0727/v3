// mobile_ext.js — extensii Mobile complet: permisiuni pe categorii (aplicate și pe server), poligoane, creare/ștergere cu confirmare, concesionari, PDF-uri emise.
// Interfața doar OGLINDEȘTE permisiunile (butoane ascunse/dezactivate); autoritatea este serverul (verifică fiecare operațiune, inclusiv cele din coada offline).
import {rowsOf} from './mobile_offline.js';
let C=null;
export const initExt=(ctx)=>{C=ctx;};
const CAT={locuri_de_veci:'locuri',concesionari:'concesionari',inhumati:'inhumati',contracte:'contracte',plati:'plati',amenajari_avize:'avize'};
const esc=(v)=>String(v??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mx=()=>C.state.syncInfo?.permissions?.matrix||null;
export const canCat=(cat,act)=>{const m=mx();return m?!!m[cat]?.[act]:(act==='view'?true:act==='delete'?false:!!C.state.syncInfo?.editable?.length);};
/** act: view | add | edit | delete (pe entitatea înregistrării). */
export const canDo=(entity,act)=>{const m=mx();if(!m)return act==='delete'?false:!!C.state.syncInfo?.editable?.includes(entity);return !!m[CAT[entity]]?.[act];};
/** Câmpurile pe care telefonul le poate MODIFICA (null = toate). Restul se afișează dezactivate. */
export const allowedFields=(entity)=>{const m=mx();if(!m)return null;const f=m[CAT[entity]]?.fields;return f?new Set(f):null;};
export const visibleModule=(v)=>({locuri:canCat('locuri','view')||canCat('harta','view'),map:canCat('harta','view'),inhumati:canCat('inhumati','view'),contracte:canCat('contracte','view'),
  plati:canCat('plati','view'),concesionari:canCat('concesionari','view'),documente:canCat('fisiere','view')}[v]??true);
export const readOnly=()=>!!mx()&&!['locuri','harta','concesionari','inhumati','contracte','plati','avize','fisiere'].some(c=>canCat(c,'add')||canCat(c,'edit')||canCat(c,'delete'));
export function renderPermPill(){const p=document.getElementById('perm-pill');if(!p)return;const info=C.state.syncInfo?.permissions;
  p.textContent=readOnly()?'Doar citire':(info?.profile?({administrator:'Administrator',secretar:'Secretar',personal_cimitir:'Personal cimitir',doar_consultare:'Doar consultare'}[info.profile]||'Permisiuni personalizate'):'Permisiuni');p.dataset.testid='perm-pill';p.setAttribute('data-testid','perm-pill');}

// ---------------------------------------------------------------- dialog de confirmare (în aplicație, nu alert)
export function askConfirm(title,msg,okLabel='Confirmă',danger=false){return new Promise((res)=>{
  const w=document.createElement('div');w.className='ext-modal';w.setAttribute('data-testid','confirm-dialog');
  w.innerHTML=`<div class="ext-card"><h3>${esc(title)}</h3><p>${esc(msg)}</p><div class="ext-actions"><button class="edit-btn small" data-testid="confirm-cancel" id="cf-no">Anulează</button><button class="edit-btn small ${danger?'danger':''}" data-testid="confirm-ok" id="cf-ok">${esc(okLabel)}</button></div></div>`;
  document.body.appendChild(w);const done=(v)=>{w.remove();res(v);};w.querySelector('#cf-no').onclick=()=>done(false);w.querySelector('#cf-ok').onclick=()=>done(true);});}

// ---------------------------------------------------------------- ștergere/anulare (op logică; relațiile se verifică pe server)
export async function deleteRecord(entity,uid,label){
  if(!canDo(entity,'delete')){C.toast('Nu ai dreptul de ștergere/anulare (se acordă din MAIN).');return false;}
  if(!await askConfirm('Confirmi ștergerea?',`${label} va fi anulat(ă). Operațiunea este auditată, iar istoricul se păstrează.`,'Șterge',true))return false;
  let row=null;try{row=await C.getRow(entity,uid);}catch(_e){C.toast('Înregistrarea nu poate fi citită acum. Reîncearcă online.');return false;}
  const ob=(await C.outbox()).filter(o=>!(o.uid===uid&&o.state==='pending'));
  ob.push({op_id:'m-'+C.newId(),entity,uid,action:'delete',base_version:row.version,data:{},state:'pending',label:`Ștergere: ${label}`});
  await C.saveOutbox(ob);document.getElementById('sheet')?.classList.remove('show');await C.syncNow();return true;}

// ---------------------------------------------------------------- PDF: vizualizare, descărcare, tipărire
async function fetchPdf(path){const r=await fetch(`${C.API}/sync-v2${path}`,{headers:{Authorization:`Binding ${C.state.key}`},cache:'no-store'});
  if(!r.ok){const e=new Error(r.status===403?'Nu ai dreptul pentru acest document.':'Documentul nu poate fi generat acum (necesită conexiune).');e.status=r.status;throw e;}return r.blob();}
export async function openPdf(path,name='document.pdf'){
  let blob;try{blob=await fetchPdf(path);}catch(e){C.toast(e.message);return;}
  const url=URL.createObjectURL(blob);document.getElementById('pdf-viewer')?.remove();
  const w=document.createElement('div');w.id='pdf-viewer';w.className='ext-modal full';w.setAttribute('data-testid','pdf-viewer');
  w.innerHTML=`<div class="ext-bar"><b>Document PDF</b><span><a class="edit-btn small" href="${url}" download="${esc(name)}" data-testid="pdf-download">Descarcă</a> <button class="edit-btn small" id="pdf-print" data-testid="pdf-print">Tipărește</button> <button class="edit-btn small" id="pdf-x" data-testid="pdf-close">Închide</button></span></div><iframe id="pdf-frame" title="PDF" src="${url}"></iframe>`;
  document.body.appendChild(w);w.querySelector('#pdf-x').onclick=()=>{URL.revokeObjectURL(url);w.remove();};
  w.querySelector('#pdf-print').onclick=()=>{try{const f=w.querySelector('#pdf-frame');f.contentWindow.focus();f.contentWindow.print();}catch(_e){window.open(url,'_blank','noopener');}};}
const KIND_CAT={contract:'contracte',chitanta:'plati',aviz:'avize',adeverinta_inhumare:'avize',adeverinta_concesiune:'avize'};
const KIND_LABEL={contract:'Contract PDF',chitanta:'Chitanță PDF',aviz:'Aviz PDF',adeverinta_inhumare:'Adeverință înhumare',adeverinta_concesiune:'Adeverință concesiune'};
export async function issuedMap(){try{const r=await C.api(`${C.API}/sync-v2/issue?limit=500`);const m={};(r.issued||[]).forEach(x=>{m[`${x.kind}:${x.entity_uid}`]=x;});return m;}catch(_e){return null;}}
async function runIssue(kind,uid,issued,btn){
  const key=`${kind}:${uid}`;
  try{let it=issued?.[key];
    if(!it){if(!canCat(KIND_CAT[kind],'add')){if(kind==='contract'){await openPdf(`/issue/preview/pdf?kind=contract&entity_uid=${encodeURIComponent(uid)}`,'contract-previzualizare.pdf');return;}C.toast('Nu ai dreptul de emitere (se acordă din MAIN).');return;}
      if(!await askConfirm('Emiți documentul?',`${KIND_LABEL[kind]} va primi număr din serie și va fi înregistrat în istoric (o singură dată).`,'Emite'))return;
      btn.disabled=true;it=await C.api(`${C.API}/sync-v2/issue`,{method:'POST',body:JSON.stringify({kind,entity_uid:uid})});if(issued)issued[key]=it;btn.textContent=`${KIND_LABEL[kind]} · ${it.number}`;C.toast(it.duplicate?`Document deja emis: ${it.number}`:`Emis: ${it.number}`);}
    await openPdf(`/issue/${it.uid}/pdf`,`${kind}-${it.number}.pdf`);}
  catch(e){C.toast(e.status?(e.status===403?'Nu ai dreptul pentru această operațiune.':e.message):'Fără conexiune: documentele PDF se generează online.');}
  finally{btn.disabled=false;}}
export async function pdfSection(g,d){
  const sec=document.createElement('section');sec.className='detail-card';sec.setAttribute('data-testid','pdf-section');
  const items=[];(d.contracts||[]).forEach(x=>{items.push(['contract',x.uid,`Contract ${x.numar_contract||'—'}`,x._pending]);items.push(['adeverinta_concesiune',x.uid,`Concesiune ${x.numar_contract||'—'}`,x._pending]);});
  (d.payments||[]).forEach(x=>items.push(['chitanta',x.uid,`Plată ${x.suma||''} lei`,x._pending]));(d.permits||[]).forEach(x=>items.push(['aviz',x.uid,`Aviz ${x.nr_aviz||''}`,x._pending]));
  (d.burials||[]).forEach(x=>items.push(['adeverinta_inhumare',x.uid,`${x.nume||''} ${x.prenume||''}`.trim(),x._pending]));
  const vis=items.filter(([k])=>canCat(KIND_CAT[k],'view'));if(!vis.length){return null;}
  const issued=await issuedMap();
  sec.innerHTML=`<h3>Documente PDF (contracte, chitanțe, avize, adeverințe)</h3>${vis.map(([k,uid,lbl,pend],i)=>{const it=issued?.[`${k}:${uid}`];return `<div class="kv"><div><b>${esc(KIND_LABEL[k])}</b><div class="micro">${esc(lbl)}${it?' · emis '+esc(it.number):''}${pend?' · nesincronizat':''}</div></div><button class="edit-btn small" data-i="${i}" data-testid="pdf-${k}" ${pend?'disabled':''}>${it?'Deschide':(canCat(KIND_CAT[k],'add')?'Emite':(k==='contract'?'Previzualizează':'Neemis'))}</button></div>`;}).join('')}`;
  sec.querySelectorAll('[data-i]').forEach(b=>{const [k,uid]=vis[+b.dataset.i];b.onclick=()=>runIssue(k,uid,issued,b);if(!canCat(KIND_CAT[k],'add')&&!issued?.[`${k}:${uid}`]&&k!=='contract')b.disabled=true;});
  return sec;}

// ---------------------------------------------------------------- editor poligon (desenare, modificare puncte, repoziționare)
const NS='http://www.w3.org/2000/svg';
export async function openPolygonEditor(uid,code,startPoints){
  const hasPoly=(startPoints||[]).length>=3;
  const act=hasPoly?'edit':'add';
  if(!canCat('harta',act)){C.toast('Nu ai dreptul să modifici poligoane (se acordă din MAIN).');return;}
  const af=allowedFields('locuri_de_veci');
  let map=C.state.map;if(!map){try{map=await C.api(`${C.API}/mobile/map`);C.state.map=map;}catch(_e){map={plots:[],sectors:[]};}}
  const others=(map.plots||[]).filter(p=>p.uid!==uid&&(p.points||[]).length>=3);
  let pts=(startPoints||[]).map(p=>[Number(p[0]),Number(p[1])]);if(pts.length>3&&pts[0][0]===pts.at(-1)[0]&&pts[0][1]===pts.at(-1)[1])pts.pop();
  const all=[...others.flatMap(p=>p.points),...pts];const xs=all.map(p=>+p[0]),ys=all.map(p=>+p[1]);
  const b=all.length?{x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs)||100,h:Math.max(...ys)-Math.min(...ys)||100}:{x:0,y:0,w:1000,h:1000};
  const pad=Math.max(b.w,b.h)*.06;let vb={x:b.x-pad,y:b.y-pad,w:b.w+2*pad,h:b.h+2*pad};const home={...vb};
  let mode=hasPoly?'move':'add';const W=document.createElement('div');W.className='ext-modal full';W.id='poly-editor';W.setAttribute('data-testid','polygon-editor');
  W.innerHTML=`<div class="ext-bar"><b>Poligon · ${esc(code||'loc nou')}</b><span><button class="edit-btn small" id="pe-cancel" data-testid="polygon-cancel">Anulează</button> <button class="edit-btn small primary" id="pe-save" data-testid="polygon-save">Salvează</button></span></div><div class="ext-tools"><button class="edit-btn small" id="pe-add" data-testid="polygon-mode-add">Adaugă puncte</button><button class="edit-btn small" id="pe-move" data-testid="polygon-mode-move">Mută / trage puncte</button><button class="edit-btn small" id="pe-undo" data-testid="polygon-undo">Anulează ultimul punct</button>${canCat('harta','delete')&&hasPoly?'<button class="edit-btn small danger" id="pe-clear" data-testid="polygon-clear">Șterge poligonul</button>':''}<button class="edit-btn small" id="pe-zi" data-testid="polygon-zoom-in">＋</button><button class="edit-btn small" id="pe-zo" data-testid="polygon-zoom-out">－</button><span class="micro" id="pe-hint"></span></div><svg id="pe-svg" class="map-svg" preserveAspectRatio="xMidYMid meet"></svg>`;
  document.body.appendChild(W);const svg=W.querySelector('#pe-svg');svg.style.touchAction='none';
  const sc=()=>vb.w/55;
  const draw=()=>{svg.setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    svg.innerHTML=`${others.map(p=>`<polygon points="${p.points.map(q=>q.join(',')).join(' ')}" fill="rgba(120,120,120,.22)" stroke="#999" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join('')}${pts.length>=2?`<polygon id="pe-poly" points="${pts.map(q=>q.join(',')).join(' ')}" fill="rgba(79,138,104,.45)" stroke="#183b2d" stroke-width="2" vector-effect="non-scaling-stroke"/>`:''}${pts.map((q,i)=>`<circle data-v="${i}" cx="${q[0]}" cy="${q[1]}" r="${sc()}" fill="#fff" stroke="#183b2d" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('')}`;
    W.querySelector('#pe-add').dataset.on=mode==='add';W.querySelector('#pe-move').dataset.on=mode==='move';W.querySelector('#pe-hint').textContent=mode==='add'?`Atinge harta pentru a adăuga puncte (${pts.length}). Minim 3.`:'Trage un punct sau poligonul întreg. Trage fundalul pentru deplasare.';};
  const toSvg=(e)=>{const m=svg.getScreenCTM().inverse();const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const q=p.matrixTransform(m);return [q.x,q.y];};
  let drag=null;
  svg.addEventListener('pointerdown',e=>{svg.setPointerCapture?.(e.pointerId);const t=e.target;const [x,y]=toSvg(e);
    if(t.dataset?.v!==undefined)drag={k:'v',i:+t.dataset.v,moved:false};else if(t.id==='pe-poly'&&mode==='move')drag={k:'all',x,y,moved:false};else drag={k:'pan',cx:e.clientX,cy:e.clientY,moved:false,x,y};});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const [x,y]=toSvg(e);
    if(drag.k==='v'){pts[drag.i]=[+x.toFixed(2),+y.toFixed(2)];drag.moved=true;draw();}
    else if(drag.k==='all'){const dx=x-drag.x,dy=y-drag.y;pts=pts.map(q=>[+(q[0]+dx).toFixed(2),+(q[1]+dy).toFixed(2)]);drag.x=x;drag.y=y;drag.moved=true;draw();}
    else{const r=svg.getBoundingClientRect(),dx=e.clientX-drag.cx,dy=e.clientY-drag.cy;if(Math.abs(dx)+Math.abs(dy)>4)drag.moved=true;if(drag.moved){vb.x-=dx/r.width*vb.w;vb.y-=dy/r.height*vb.h;drag.cx=e.clientX;drag.cy=e.clientY;draw();}}});
  svg.addEventListener('pointerup',e=>{if(drag&&drag.k==='pan'&&!drag.moved&&mode==='add'){const [x,y]=toSvg(e);pts.push([+x.toFixed(2),+y.toFixed(2)]);draw();}drag=null;});
  svg.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?.85:1.18);},{passive:false});
  const zoom=(f)=>{const cx=vb.x+vb.w/2,cy=vb.y+vb.h/2;vb={x:cx-vb.w*f/2,y:cy-vb.h*f/2,w:vb.w*f,h:vb.h*f};draw();};
  W.querySelector('#pe-zi').onclick=()=>zoom(.7);W.querySelector('#pe-zo').onclick=()=>zoom(1.4);
  W.querySelector('#pe-add').onclick=()=>{mode='add';draw();};W.querySelector('#pe-move').onclick=()=>{mode='move';draw();};
  W.querySelector('#pe-undo').onclick=()=>{pts.pop();draw();};
  W.querySelector('#pe-clear')?.addEventListener('click',()=>{pts=[];draw();});
  W.querySelector('#pe-cancel').onclick=()=>W.remove();
  W.querySelector('#pe-save').onclick=async()=>{
    if(pts.length&&pts.length<3){C.toast('Un poligon are nevoie de cel puțin 3 puncte.');return;}
    if(!pts.length&&!hasPoly){C.toast('Nu ai desenat nimic.');return;}
    if(af&&!af.has('points')&&!af.has('color')&&false)return;
    try{
      let ob=await C.outbox();const pend=ob.find(o=>o.uid===uid&&o.entity==='locuri_de_veci'&&o.state==='pending');
      if(pend){pend.data={...pend.data,points:pts};}   // loc încă nesincronizat: poligonul se adaugă aceleiași operațiuni
      else{const row=await C.getRow('locuri_de_veci',uid);ob=ob.filter(o=>!(o.uid===uid&&o.state==='pending'));
        ob.push({op_id:'m-'+C.newId(),entity:'locuri_de_veci',uid,action:'upsert',base_version:row.version,data:{...row.data,points:pts},state:'pending',label:`Poligon ${code||''}`.trim()});}
      await C.saveOutbox(ob);W.remove();C.state.map=null;await C.syncNow();
    }catch(_e){C.toast('Poligonul nu poate fi salvat acum. Reîncearcă online.');}
  };
  draw();}

// ---------------------------------------------------------------- loc nou (cod, sector…) + poligon
export async function newGrave(){
  if(!canDo('locuri_de_veci','add')){C.toast('Nu ai dreptul de a crea locuri (se acordă din MAIN).');return;}
  await C.openRecordForm('locuri_de_veci',null);}

// ---------------------------------------------------------------- concesionari: listă, căutare, adăugare/editare
export async function renderConcesionari(v){
  v.innerHTML=`<div class="page-head"><div><h1>Concesionari</h1><p>Caută, adaugă sau editează concesionari. Legătura cu locul se face prin contract.</p></div></div><div class="big-search"><input id="cq" type="search" placeholder="Nume, telefon…" data-testid="concesionari-filter" autocomplete="off"></div>${canDo('concesionari','add')?'<button class="edit-btn" id="cadd" data-testid="add-concesionari-top">Adaugă concesionar</button>':''}<div id="cbox" class="result-list"><div class="empty">Se încarcă…</div></div>`;
  const draw=async()=>{const q=(document.getElementById('cq')?.value||'').toLowerCase();const ob=await C.outbox();const rows=(await rowsOf('concesionari')).map(r=>({uid:r.uid,...r.data}));
    ob.filter(o=>o.entity==='concesionari'&&o.state==='pending'&&!rows.some(r=>r.uid===o.uid)).forEach(o=>rows.push({uid:o.uid,...o.data,_p:1}));
    const items=rows.filter(r=>!q||`${r.nume||''} ${r.prenume||''} ${r.telefon||''}`.toLowerCase().includes(q)).sort((a,b)=>`${a.nume}`.localeCompare(`${b.nume}`)).slice(0,200);
    document.getElementById('cbox').innerHTML=items.length?items.map(r=>`<button class="result" data-uid="${esc(r.uid)}" data-testid="concesionar-row"><div><b>${esc(`${r.nume||''} ${r.prenume||''}`.trim()||'Fără nume')}</b><small>${esc(r.telefon||'')}${r._p?' · nesincronizat':''}</small></div><span class="pill">${canDo('concesionari','edit')?'Editează':'Vezi'}</span></button>`).join(''):'<div class="empty">Nicio înregistrare. Apasă Sincronizează.</div>';
    document.querySelectorAll('#cbox [data-uid]').forEach(b=>b.onclick=()=>C.openRecordForm('concesionari',b.dataset.uid,true));};
  document.getElementById('cq').oninput=draw;document.getElementById('cadd')?.addEventListener('click',()=>C.openRecordForm('concesionari',null));draw();}
