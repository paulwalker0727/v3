/* Trimite din teren — pagina de captură a telefonului asociat (Phone Pairing v1 + Field Inbox v1).
   Nu loghează nimic. Cheia legăturii (a acestui telefon) e singurul lucru păstrat pe telefon; codul de asociere, URL-urile semnate și
   orice alt secret nu sunt păstrate. Fără coadă offline: formularul rămâne în pagină, iar retry-ul refolosește același idempotency_key. */
(function (root) {
  'use strict';

  var STORE_KEY = 'cimitir.phone.binding.v1';
  var MAX_BYTES = 25 * 1024 * 1024;
  var MIME_OK = ['application/pdf', 'image/jpeg', 'image/png'];
  var IMG_OK = ['image/jpeg', 'image/png'];
  var STAGES = ['BEFORE', 'DURING', 'AFTER'];
  var MSG = {
    sending: 'Trimitere...',
    ok: 'Trimis în Field Inbox',
    fail: 'Nu s-a putut trimite. Încearcă din nou.',
    unpaired: 'Telefonul nu este asociat. Introdu codul primit din MAIN.',
    revoked: 'Telefonul nu mai este autorizat (revocat sau expirat). Cere un cod nou din MAIN.',
    wrongCem: 'Telefonul este asociat altui cimitir. Trimiterea este blocată.',
    notMobile: 'Cheia salvată nu este a unui telefon. Trimiterea este blocată.',
    noCloud: 'Cloud neconfigurat pe portal.',
    pairFail: 'Codul nu a putut fi folosit. Verifică codul sau cere unul nou din MAIN.'
  };

  function ValidationError(message) { this.name = 'ValidationError'; this.message = message; }
  ValidationError.prototype = Object.create(Error.prototype);

  function uuid4(c) {
    var b = new Uint8Array(16);
    (c || root.crypto).getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  function norm(v) { return String(v || '').trim().toLowerCase(); }
  function clean(v) { return typeof v === 'string' ? v.trim() : ''; }

  // <input type="date"> dă AAAA-LL-ZZ; MAIN lucrează cu ZZ.LL.AAAA. Gol rămâne gol (fără data de azi).
  function toRoDate(v) {
    v = clean(v);
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
    return m ? m[3] + '.' + m[2] + '.' + m[1] : v;
  }

  var FIELDS = {
    DECEASED: { req: ['nume'], opt: ['prenume', 'data_nasterii', 'data_deces', 'data_inhumare', 'sex', 'observatii'], dates: ['data_nasterii', 'data_deces', 'data_inhumare'] },
    WORK: { req: ['descriere'], opt: ['data_inceput', 'data_sfarsit', 'executant', 'observatii'], dates: ['data_inceput', 'data_sfarsit'] },
    PERMIT: { req: ['nr_aviz'], opt: ['data_aviz', 'lucrare', 'observatii'], dates: ['data_aviz'] },
    DOCUMENT: { req: ['doc_type'], opt: ['caption', 'nr_evidenta'], dates: [] },
    GRAVE_PHOTO: { req: [], opt: ['caption'], dates: [] },
    WORK_PHOTO: { req: [], opt: ['caption'], dates: [] }
  };
  var NEEDS_FILE = { DOCUMENT: true, GRAVE_PHOTO: true, WORK_PHOTO: true };
  var PHOTO = { GRAVE_PHOTO: true, WORK_PHOTO: true };

  // form: {câmp: valoare}; ctx: {grave_uid, target_uid?, work_stage?}. Câmpurile goale nu intră în payload.
  function buildItem(type, form, ctx) {
    var spec = FIELDS[type];
    if (!spec) { throw new ValidationError('Alege ce trimiți.'); }
    form = form || {}; ctx = ctx || {};
    var grave = clean(ctx.grave_uid);
    if (!grave) { throw new ValidationError('Alege locul de veci.'); }
    var payload = {}, all = spec.req.concat(spec.opt), i, k, v;
    for (i = 0; i < all.length; i++) {
      k = all[i];
      v = clean(form[k]);
      if (spec.dates.indexOf(k) >= 0) { v = toRoDate(v); }
      if (v) { payload[k] = v; }
      else if (spec.req.indexOf(k) >= 0) { throw new ValidationError('Completează câmpul obligatoriu: ' + k.replace(/_/g, ' ') + '.'); }
    }
    var item = { item_type: type, payload: payload, grave_uid: grave };
    if (type === 'WORK_PHOTO') {
      var stage = clean(ctx.work_stage);
      if (STAGES.indexOf(stage) < 0) { throw new ValidationError('Alege etapa: ÎNAINTE, ÎN TIMPUL sau DUPĂ.'); }
      var target = clean(ctx.target_uid);
      if (!target) { throw new ValidationError('Alege lucrarea pentru fotografie.'); }
      item.work_stage = stage; item.target_uid = target;
    }
    return item;
  }

  function checkFile(type, file) {
    if (!NEEDS_FILE[type]) { return; }
    if (!file) { throw new ValidationError(PHOTO[type] ? 'Fă sau alege o fotografie.' : 'Alege un fișier PDF, JPEG sau PNG.'); }
    var t = norm(file.type);
    if ((PHOTO[type] ? IMG_OK : MIME_OK).indexOf(t) < 0) { throw new ValidationError(PHOTO[type] ? 'Fotografia trebuie să fie JPEG sau PNG.' : 'Acceptăm doar PDF, JPEG sau PNG.'); }
    if (file.size > MAX_BYTES) { throw new ValidationError('Fișierul depășește 25 MB.'); }
    if (file.size <= 0) { throw new ValidationError('Fișierul este gol.'); }
  }

  function safeName(n) { return (String(n || 'fisier').replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 100)) || 'fisier'; }

  function bytesToHex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (x) { return ('0' + x.toString(16)).slice(-2); }).join(''); }

  function NetErr() { this.name = 'NetErr'; }
  NetErr.prototype = Object.create(Error.prototype);

  /* Client: base = URL-ul API-ului cloud; storage = Web Storage; fetchFn = fetch; opts.cemetery = cimitirul registrului activ al portalului */
  function Client(base, storage, fetchFn, opts) {
    this.base = String(base || '').replace(/\/+$/, '');
    this.storage = storage;
    this.fetch = fetchFn || (root.fetch && root.fetch.bind(root));
    this.opts = opts || {};
    this.crypto = this.opts.crypto || root.crypto;
    this.pending = null;      // trimiterea curentă: {sig, key, fileUid}
    this.inflight = null;
  }

  Client.prototype.binding = function () {
    try {
      var o = JSON.parse(this.storage.getItem(STORE_KEY) || 'null');
      return o && typeof o.binding_key === 'string' && o.binding_key ? o : null;
    } catch (e) { return null; }
  };
  Client.prototype.forget = function () { try { this.storage.removeItem(STORE_KEY); } catch (e) { /* nimic */ } };

  Client.prototype.call = function (method, path, o) {
    o = o || {};
    var h = {}, k;
    for (k in (o.headers || {})) { h[k] = o.headers[k]; }
    if (o.auth !== false) {
      var b = this.binding();
      if (!b) { return Promise.resolve({ status: 401, body: null }); }
      h.Authorization = 'Binding ' + b.binding_key;
    }
    var init = { method: method, headers: h, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', redirect: 'error' };
    if (o.body !== undefined) { init.body = o.body; }
    return this.fetch(this.base + '/api/v1' + path, init).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = JSON.parse(t); } catch (e) { /* non-JSON */ }
        return { status: r.status, body: j };
      }, function () { throw new NetErr(); });
    }, function () { throw new NetErr(); });
  };

  // Starea telefonului: unpaired | revoked | wrong_cemetery | not_mobile | offline | ready
  Client.prototype.session = function () {
    var self = this;
    if (!self.base) { return Promise.resolve({ state: 'no_cloud', message: MSG.noCloud }); }
    if (!self.binding()) { return Promise.resolve({ state: 'unpaired', message: MSG.unpaired }); }
    return self.call('GET', '/field-inbox/me').then(function (r) {
      if (r.status === 401 || r.status === 404) { self.forget(); return { state: 'revoked', message: MSG.revoked }; }
      if (r.status !== 200 || !r.body || !r.body.ok) { return { state: 'offline', message: MSG.fail }; }
      var res = r.body.result;
      if (res.device_type !== 'MOBILE') { return { state: 'not_mobile', message: MSG.notMobile }; }
      if (self.opts.cemetery && norm(res.cemetery_uid) !== norm(self.opts.cemetery)) { return { state: 'wrong_cemetery', message: MSG.wrongCem }; }
      return { state: 'ready', cemetery_uid: res.cemetery_uid };
    }, function () { return { state: 'offline', message: MSG.fail }; });
  };

  // Asociere: codul se folosește o singură dată și nu se păstrează; se salvează doar cheia legăturii acestui telefon.
  Client.prototype.pair = function (code, deviceName) {
    var self = this;
    var body = { code: clean(code), device_name: clean(deviceName) || 'Telefon' };
    return self.call('POST', '/phone-pairing/redeem', { auth: false, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) {
      var res = r.body && r.body.result;
      if (r.status !== 201 || !res || !res.binding_key) { return { ok: false, message: MSG.pairFail }; }
      if (res.device_type !== 'MOBILE') { return { ok: false, message: MSG.notMobile }; }
      self.storage.setItem(STORE_KEY, JSON.stringify({ binding_key: res.binding_key, cemetery_uid: res.cemetery_uid }));
      return { ok: true };
    }, function () { return { ok: false, message: MSG.fail }; });
  };

  function signature(item, file) {
    return JSON.stringify([item, file ? [file.name, file.size, file.type, file.lastModified || 0] : null]);
  }

  Client.prototype.hashOf = function (file) {
    var c = this.crypto;
    if (!c || !c.subtle || !file.arrayBuffer) { return Promise.resolve(''); }
    return file.arrayBuffer().then(function (b) { return c.subtle.digest('SHA-256', b); }).then(bytesToHex, function () { return ''; });
  };

  // O singură trimitere în zbor (dublu-tap = aceeași promisiune). Retry cu același conținut = același idempotency_key și același file_uid.
  Client.prototype.send = function (item, file) {
    var self = this;
    if (self.inflight) { return self.inflight; }
    var sig = signature(item, file);
    if (!self.pending || self.pending.sig !== sig) {
      self.pending = { sig: sig, key: 'tc-' + uuid4(self.crypto), fileUid: uuid4(self.crypto) };
    }
    var p = self.pending;
    var p0 = Promise.resolve(self.localGate()).then(function (g) {
      if (g) { return g; }
      return self.session().then(function (s) {
        if (s.state === 'ready') { return null; }
        if (s.state === 'offline') { return { ok: false, retry: true, message: MSG.fail }; }
        return { ok: false, blocked: s.state, message: s.message };
      });
    });
    self.inflight = p0.then(function (gate) {
      if (gate) { return gate; }
      var req = { idempotency_key: p.key, item_type: item.item_type, payload: item.payload, grave_uid: item.grave_uid };
      if (item.work_stage) { req.work_stage = item.work_stage; req.target_uid = item.target_uid; }
      return self.call('POST', '/field-inbox/items', { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) }).then(function (r) {
        var out = self.classify(r);
        if (out) { return out; }
        var uid = r.body.result.item.uid;
        if (!file) { return self.done(uid); }
        return self.hashOf(file).then(function (sha) {
          var h = { 'Content-Type': norm(file.type), 'X-File-Uid': p.fileUid, 'X-File-Name': safeName(file.name) };
          if (sha) { h['X-File-Sha256'] = sha; }
          return self.call('POST', '/field-inbox/items/' + encodeURIComponent(uid) + '/files', { headers: h, body: file }).then(function (u) {
            return self.classify(u) || self.done(uid);
          });
        });
      });
    }).then(function (res) { self.inflight = null; return res; }, function (e) {
      self.inflight = null;
      return { ok: false, retry: true, message: MSG.fail };
    });
    return self.inflight;
  };

  Client.prototype.localGate = function () {
    var b = this.binding();
    if (!b) { return { ok: false, blocked: 'unpaired', message: MSG.unpaired }; }
    if (this.opts.cemetery && norm(b.cemetery_uid) !== norm(this.opts.cemetery)) { return { ok: false, blocked: 'wrong_cemetery', message: MSG.wrongCem }; }
    return null;
  };

  Client.prototype.classify = function (r) {
    if (r.status === 401) { this.forget(); return { ok: false, blocked: 'revoked', message: MSG.revoked }; }
    if (r.status === 200 || r.status === 201) { return r.body && r.body.ok ? null : { ok: false, retry: true, message: MSG.fail }; }
    var code = r.body && r.body.code;
    if (r.status === 400 && (code === 'ITEM_INVALID' || code === 'REQUEST_INVALID')) { return { ok: false, retry: false, message: (r.body.message || MSG.fail) }; }
    if (r.status === 415 || (r.status === 400 && code === 'STORAGE_INVALID')) { return { ok: false, retry: false, message: 'Fișier neacceptat (doar PDF, JPEG sau PNG).' }; }
    if (r.status === 409 && code === 'IDEMPOTENCY_CONFLICT') { this.pending = null; return { ok: false, retry: true, message: MSG.fail }; }
    return { ok: false, retry: true, message: MSG.fail };
  };

  Client.prototype.done = function (uid) {
    this.pending = null;
    return { ok: true, uid: uid, ref: String(uid).slice(0, 8).toUpperCase(), message: MSG.ok };
  };

  root.TerenCapture = {
    MSG: MSG, STORE_KEY: STORE_KEY, STAGES: STAGES, ValidationError: ValidationError,
    uuid4: uuid4, toRoDate: toRoDate, buildItem: buildItem, checkFile: checkFile, Client: Client, signature: signature
  };
  if (typeof module !== 'undefined' && module.exports) { module.exports = root.TerenCapture; }
}(typeof window !== 'undefined' ? window : globalThis));
