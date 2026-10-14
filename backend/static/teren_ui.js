/* Trimite din teren — legarea paginii (DOM) de TerenCapture. Fără loguri, fără persistență în afară de cheia legăturii (în teren.js). */
(function () {
  'use strict';
  var T = window.TerenCapture, CFG = window.TEREN_CFG || {};
  function $(id) { return document.getElementById(id); }
  var client = new T.Client(CFG.cloud_url, window.localStorage, window.fetch.bind(window), { cemetery: CFG.cemetery_uid });
  var state = { type: '', loc: CFG.loc || null, ready: false, sent: [] };

  function show(el, on) { el.classList.toggle('hide', !on); }
  function msg(kind, text, ref) {
    var m = $('tr-msg');
    m.className = 'tr-msg ' + kind;
    m.textContent = text + (ref ? ' — ref. ' + ref : '');
    m.setAttribute('data-testid', kind === 'ok' ? 'teren-success' : 'teren-error');
    show(m, true);
  }
  function clearMsg() { show($('tr-msg'), false); }

  function renderLoc() {
    var l = state.loc;
    $('tr-cem').textContent = CFG.cemetery_label || '—';
    $('tr-cod').textContent = l ? l.cod_loc : 'neales';
    $('tr-guid').textContent = l && l.grave_uid ? l.grave_uid : (l ? 'lipsă (loc fără cod unic)' : '—');
    var back = $('tr-back-map');
    if (back) { back.href = l ? '/admin/harta?open=' + encodeURIComponent(l.cod_loc) : '/admin/harta'; }
    var sel = $('f-target');
    sel.innerHTML = '';
    var opts = (l ? l.avize : []).concat(state.sent.filter(function (s) { return l && s.cod_loc === l.cod_loc; }));
    var o0 = document.createElement('option'); o0.value = ''; o0.textContent = '— alege lucrarea —'; sel.appendChild(o0);
    opts.forEach(function (a) { var o = document.createElement('option'); o.value = a.uid; o.textContent = a.label; sel.appendChild(o); });
    updateSend();
  }

  function setState(s) {
    state.ready = s.state === 'ready';
    var blocked = !state.ready;
    show($('tr-pair'), s.state === 'unpaired' || s.state === 'revoked');
    show($('tr-main'), state.ready);
    var b = $('tr-block');
    b.textContent = s.message || '';
    b.setAttribute('data-state', s.state);
    show(b, blocked);
    $('tr-retry-session').classList.toggle('hide', s.state !== 'offline');
  }

  function checkSession() {
    return client.session().then(setState);
  }

  function updateSend() { $('tr-send').disabled = !(state.ready && state.type && state.loc && state.loc.grave_uid) || state.busy; }

  function pickType(t) {
    state.type = t;
    ['DECEASED', 'WORK', 'PERMIT', 'DOCUMENT', 'GRAVE_PHOTO', 'WORK_PHOTO'].forEach(function (k) {
      show($('sec-' + k), k === t);
      $('tile-' + k).classList.toggle('on', k === t);
    });
    show($('tr-form'), true);
    clearMsg();
    updateSend();
  }

  function val(name) { var e = document.querySelector('[name="' + name + '"]'); return e ? e.value : ''; }
  function formFor(t) {
    var sec = $('sec-' + t), out = {};
    Array.prototype.forEach.call(sec.querySelectorAll('[name]'), function (e) { if (e.type !== 'file') { out[e.name] = e.value; } });
    return out;
  }
  function fileFor(t) {
    var e = document.querySelector('#sec-' + t + ' input[type=file]');
    return e && e.files && e.files[0] ? e.files[0] : null;
  }

  function resetForm(t) {
    var sec = $('sec-' + t);
    Array.prototype.forEach.call(sec.querySelectorAll('input,select,textarea'), function (e) { if (e.type === 'file') { e.value = ''; } else { e.value = ''; } });
    var pk = sec.querySelector('.tr-picked'); if (pk) { pk.textContent = ''; }
  }

  function send() {
    if (state.busy) { return; }
    state.busy = true;                                   // înainte de orice await: al doilea tap nu mai ajunge la rețea
    var btn = $('tr-send'), t = state.type;
    btn.disabled = true; btn.textContent = T.MSG.sending;
    clearMsg();
    var item, file;
    try {
      var ctx = { grave_uid: state.loc && state.loc.grave_uid };
      if (t === 'WORK_PHOTO') { ctx.work_stage = val('work_stage'); ctx.target_uid = val('target_uid'); }
      item = T.buildItem(t, formFor(t), ctx);
      file = fileFor(t);
      T.checkFile(t, file);
    } catch (e) {
      state.busy = false; btn.textContent = 'Trimite'; updateSend();
      msg('err', e.message || T.MSG.fail);
      return;
    }
    client.send(item, file).then(function (r) {
      state.busy = false; btn.textContent = 'Trimite';
      if (r.ok) {
        if (t === 'WORK' && state.loc) { state.sent.push({ uid: r.uid, cod_loc: state.loc.cod_loc, label: 'Lucrare trimisă acum: ' + (item.payload.descriere || '').slice(0, 40) + ' (' + r.ref + ')' }); }
        resetForm(t); renderLoc();
        msg('ok', r.message, r.ref);
      } else if (r.blocked) {
        checkSession().then(function () { msg('err', r.message); });
      } else {
        msg('err', r.message);
      }
      updateSend();
    });
  }

  function lookup() {
    var cod = $('tr-q').value.trim();
    if (!cod) { return; }
    fetch('/admin/teren/loc/' + encodeURIComponent(cod), { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } }).then(function (r) {
      if (!r.ok) { throw new Error('nf'); }
      return r.json();
    }).then(function (l) { state.loc = l; renderLoc(); clearMsg(); }, function () { state.loc = null; renderLoc(); msg('err', 'Locul nu există în registrul activ.'); });
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll('.tr-tile'), function (b) { b.onclick = function () { pickType(b.getAttribute('data-type')); }; });
    Array.prototype.forEach.call(document.querySelectorAll('input[type=file]'), function (i) {
      i.onchange = function () {
        var pk = i.parentNode.querySelector('.tr-picked');
        if (pk) { pk.textContent = i.files[0] ? i.files[0].name + ' (' + Math.max(1, Math.round(i.files[0].size / 1024)) + ' KB)' : ''; }
      };
    });
    $('tr-send').onclick = send;
    $('tr-find').onclick = lookup;
    $('tr-retry-session').onclick = checkSession;
    $('tr-pair-btn').onclick = function () {
      var b = $('tr-pair-btn'); if (b.disabled) { return; }
      b.disabled = true;
      var code = $('tr-code').value;
      client.pair(code, $('tr-devname').value).then(function (r) {
        b.disabled = false;
        $('tr-code').value = '';
        if (r.ok) { checkSession(); } else { var m = $('tr-pair-msg'); m.textContent = r.message; show(m, true); }
      });
    };
    renderLoc();
    checkSession();
  }
  init();
}());
