"use strict";
(function () {
  var API = "/api/v1/admin";
  var app = document.getElementById("app");
  var S = { me: null, org: null, cem: null, code: null, timer: null };

  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] === null) return;
      if (k === "text") e.textContent = attrs[k];
      else if (k === "on") Object.keys(attrs.on).forEach(function (ev) { e.addEventListener(ev, attrs.on[ev]); });
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function toast(msg) { var t = document.getElementById("toast"); t.textContent = msg; t.classList.remove("hidden"); setTimeout(function () { t.classList.add("hidden"); }, 3500); }
  function api(method, path, body) {
    var opt = { method: method, credentials: "same-origin", headers: { "X-Cimitir-Admin": "1" } };
    if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    return fetch(API + path, opt).then(function (r) { return r.json().catch(function () { return { ok: false, message: "răspuns invalid" }; }).then(function (j) { j.httpStatus = r.status; return j; }); });
  }
  function field(id, label, type, extra) {
    var i = h("input", Object.assign({ id: id, type: type || "text", "data-testid": id, autocomplete: "off" }, extra || {}));
    return h("div", {}, [h("label", { "for": id, text: label }), i]);
  }
  function val(id) { return document.getElementById(id).value.trim(); }
  function errBox(id) { return h("div", { id: id, class: "err", "data-testid": id }); }
  function showErr(id, j) { document.getElementById(id).textContent = j.message || "Eroare"; }
  function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString("ro-RO"); }

  function renderBootstrap(st) {
    app.replaceChildren(h("div", { class: "card" }, [
      h("h2", { text: "Inițializare primul administrator" }),
      h("p", { class: "muted", text: st.bootstrap_open ? "Introdu tokenul de inițializare primit la instalare (valabil o singură dată)." : "Tokenul de inițializare lipsește sau a expirat pe server. Generează unul nou cu scripts/generate_bootstrap_token.py și repornește containerul." }),
      field("bs-token", "Token de inițializare", "password"), field("bs-email", "Email administrator", "email"),
      field("bs-name", "Nume afișat"), field("bs-pass", "Parolă (minim 12 caractere)", "password"),
      h("h3", { text: "Organizație / cimitir (opțional — pentru o instalare existentă completează UID-urile)" }),
      field("bs-org-uid", "UID organizație existentă (gol = creează una nouă)"), field("bs-org-name", "Denumire organizație nouă"),
      field("bs-cem-uid", "UID cimitir (gol = fără cimitir nou)"), field("bs-cem-name", "Denumire cimitir"),
      h("button", { "data-testid": "bs-submit", text: "Creează administratorul", on: { click: function () {
        api("POST", "/bootstrap", { token: val("bs-token"), email: val("bs-email"), password: document.getElementById("bs-pass").value, display_name: val("bs-name"),
          organization_uid: val("bs-org-uid") || null, organization_name: val("bs-org-name"), cemetery_uid: val("bs-cem-uid") || null, cemetery_name: val("bs-cem-name") })
          .then(function (j) { if (j.ok) { toast("Administrator creat. Autentifică-te."); start(); } else showErr("bs-err", j); });
      } } }), errBox("bs-err")]));
  }

  function renderLogin() {
    app.replaceChildren(h("div", { class: "card" }, [
      h("h2", { text: "Autentificare administrator" }), field("lg-email", "Email", "email"), field("lg-pass", "Parolă", "password"),
      h("button", { "data-testid": "lg-submit", text: "Intră", on: { click: function () {
        api("POST", "/login", { email: val("lg-email"), password: document.getElementById("lg-pass").value }).then(function (j) { if (j.ok) start(); else showErr("lg-err", j); });
      } } }), errBox("lg-err"),
      h("p", {}, [h("a", { href: "#", "data-testid": "lg-recover-link", text: "Am pierdut accesul (recuperare)", on: { click: function (e) { e.preventDefault(); renderRecover(); } } })])]));
  }

  function renderRecover() {
    app.replaceChildren(h("div", { class: "card" }, [
      h("h2", { text: "Recuperare acces administrator" }),
      h("p", { class: "muted", text: "Pe serverul Cloud rulează: python scripts/cloud_admin_recovery.py --email <emailul-tău>. Tokenul afișat e valabil 15 minute și se folosește o singură dată. Datele nu se șterg." }),
      field("rc-token", "Token de recuperare", "password"), field("rc-email", "Email", "email"), field("rc-pass", "Parolă nouă (minim 12 caractere)", "password"),
      h("button", { "data-testid": "rc-submit", text: "Setează parola nouă", on: { click: function () {
        api("POST", "/recover", { token: val("rc-token"), email: val("rc-email"), new_password: document.getElementById("rc-pass").value })
          .then(function (j) { if (j.ok) { toast("Parola a fost schimbată."); renderLogin(); } else showErr("rc-err", j); });
      } } }), h("button", { class: "ghost", text: "Înapoi", on: { click: renderLogin } }), errBox("rc-err")]));
  }

  function curOrg() { return S.me.organizations.filter(function (o) { return o.organization_uid === S.org; })[0]; }

  function renderDash() {
    var me = S.me;
    document.getElementById("who").textContent = me.email;
    document.getElementById("btn-logout").classList.remove("hidden");
    if (!me.organizations.length) { app.replaceChildren(h("div", { class: "card" }, [h("p", { text: "Contul nu are nicio organizație." })])); return; }
    if (!S.org) S.org = me.organizations[0].organization_uid;
    var org = curOrg();
    if (!S.cem || !org.cemeteries.some(function (c) { return c.uid === S.cem; })) S.cem = org.cemeteries.length ? org.cemeteries[0].uid : null;
    var orgSel = h("select", { id: "sel-org", "data-testid": "select-organization", on: { change: function (e) { S.org = e.target.value; S.cem = null; S.code = null; renderDash(); } } },
      me.organizations.map(function (o) { return h("option", { value: o.organization_uid, text: o.name || o.organization_uid }); }));
    orgSel.value = S.org;
    var cemSel = h("select", { id: "sel-cem", "data-testid": "select-cemetery", on: { change: function (e) { S.cem = e.target.value; S.code = null; renderDash(); } } },
      org.cemeteries.map(function (c) { return h("option", { value: c.uid, text: c.name + " (" + c.status + ")" }); }));
    if (S.cem) cemSel.value = S.cem;
    var owner = org.is_owner;
    var box = h("div", { class: "card" }, [
      h("h2", { text: "Asociere dispozitive" }),
      h("div", { class: "row" }, [h("div", {}, [h("label", { text: "Organizație" }), orgSel]), h("div", {}, [h("label", { text: "Cimitir" }), cemSel])]),
      h("div", { class: "row" }, [
        h("button", { "data-testid": "issue-main-code", text: "Generează cod pentru MAIN", disabled: owner && S.cem ? null : "disabled", on: { click: function () { issue("main"); } } }),
        h("button", { "data-testid": "issue-phone-code", text: "Generează cod pentru telefon", disabled: owner && S.cem ? null : "disabled", on: { click: function () { issue("phone"); } } })]),
      owner ? null : h("p", { class: "muted", text: "Doar Owner-ul poate emite sau revoca coduri." }),
      h("div", { id: "code-box" }), errBox("issue-err")]);
    var cemNew = h("div", { class: "card" }, [h("h2", { text: "Cimitir nou în această organizație" }), h("div", { class: "row" }, [field("nc-name", "Denumire cimitir"),
      h("button", { "data-testid": "create-cemetery", text: "Creează cimitir", disabled: owner ? null : "disabled", on: { click: function () {
        api("POST", "/cemeteries", { organization_uid: S.org, name: val("nc-name") }).then(function (j) { if (j.ok) { S.cem = j.result.cemetery_uid; toast("Cimitir creat (DRAFT)."); loadMe(); } else showErr("issue-err", j); });
      } } })])]);
    var pw = h("div", { class: "card" }, [h("h2", { text: "Schimbă parola" }), h("div", { class: "row" }, [field("pw-old", "Parola curentă", "password"), field("pw-new", "Parola nouă", "password"),
      h("button", { "data-testid": "change-password", text: "Schimbă", on: { click: function () {
        api("POST", "/password", { old_password: document.getElementById("pw-old").value, new_password: document.getElementById("pw-new").value })
          .then(function (j) { if (j.ok) toast("Parola a fost schimbată."); else showErr("issue-err", j); });
      } } })])]);
    app.replaceChildren(box, h("div", { class: "card", id: "codes-card" }), h("div", { class: "card", id: "devices-card" }), cemNew, pw);
    drawCode(); loadCodes(); loadDevices();
  }

  function issue(kind) {
    api("POST", "/codes", { organization_uid: S.org, cemetery_uid: S.cem, kind: kind }).then(function (j) {
      if (!j.ok) { showErr("issue-err", j); return; }
      document.getElementById("issue-err").textContent = ""; S.code = j.result; drawCode(); loadCodes();
    });
  }

  function drawCode() {
    clearInterval(S.timer);
    var box = document.getElementById("code-box"); if (!box) return;
    if (!S.code) { box.replaceChildren(); return; }
    var c = S.code, left = h("span", { "data-testid": "code-countdown" });
    box.replaceChildren(h("h3", { text: c.kind === "main" ? "Cod pentru MAIN (calculator Windows)" : "Cod pentru telefon" }),
      h("div", { class: "code", "data-testid": "issued-code", text: c.display }),
      h("div", { class: "row" }, [h("div", {}, [h("span", { class: "muted", text: "Valabil încă: " }), left]),
        h("button", { "data-testid": "copy-code", text: "Copiază codul", on: { click: function () { (navigator.clipboard ? navigator.clipboard.writeText(c.code) : Promise.reject()).then(function () { toast("Cod copiat."); }, function () { toast("Copierea a eșuat; selectează codul manual."); }); } } }),
        h("button", { class: "danger", "data-testid": "revoke-issued-code", text: "Revocă acest cod", on: { click: function () { revokeCode(c.code_id); S.code = null; drawCode(); } } })]),
      h("p", { class: "muted", text: "Codul se afișează o singură dată și se folosește o singură dată." }));
    function tick() {
      var s = Math.round(c.expires_at - Date.now() / 1000);
      if (s <= 0) { box.replaceChildren(h("p", { class: "err", "data-testid": "code-expired", text: "Codul a expirat. Generează unul nou." }),
        h("button", { "data-testid": "regenerate-code", text: "Regenerează", on: { click: function () { issue(c.kind); } } })); clearInterval(S.timer); return; }
      left.textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
    }
    tick(); S.timer = setInterval(tick, 1000);
  }

  function revokeCode(id) { api("POST", "/codes/revoke", { organization_uid: S.org, code_id: id }).then(function (j) { if (j.ok) toast("Cod revocat."); else showErr("issue-err", j); loadCodes(); }); }

  function loadCodes() {
    api("GET", "/codes?organization_uid=" + encodeURIComponent(S.org) + (S.cem ? "&cemetery_uid=" + encodeURIComponent(S.cem) : "")).then(function (j) {
      var card = document.getElementById("codes-card"); if (!card || !j.ok) return;
      var rows = j.result.map(function (r) { return h("tr", { "data-testid": "code-row" }, [h("td", { text: r.kind === "main" ? "MAIN" : "Telefon" }), h("td", { text: fmtTime(r.created_at) }),
        h("td", { text: fmtTime(r.expires_at) }), h("td", {}, [h("span", { class: "tag " + r.state, text: r.state })]),
        h("td", {}, [r.state === "active" ? h("button", { class: "ghost", "data-testid": "revoke-code-" + r.code_id, text: "Revocă", on: { click: function () { revokeCode(r.code_id); } } }) : null])]); });
      card.replaceChildren(h("h2", { text: "Coduri recente (7 zile)" }), h("table", {}, [h("tr", {}, ["Tip", "Emis", "Expiră", "Stare", ""].map(function (t) { return h("th", { text: t }); }))].concat(rows)));
    });
  }

  function loadDevices() {
    api("GET", "/devices?organization_uid=" + encodeURIComponent(S.org)).then(function (j) {
      var card = document.getElementById("devices-card"); if (!card || !j.ok) return;
      var owner = curOrg().is_owner;
      var rows = j.result.map(function (d) { return h("tr", { "data-testid": "device-row" }, [h("td", { text: d.device_name }), h("td", { text: d.kind === "main" ? "MAIN" : "Telefon" }), h("td", { text: d.cemetery_name }),
        h("td", { text: d.last_seen_at ? d.last_seen_at.slice(0, 16).replace("T", " ") : "—" }), h("td", {}, [h("span", { class: "tag " + d.state, text: d.state })]),
        h("td", {}, [d.state !== "revoked" && owner ? h("button", { class: "danger", "data-testid": "revoke-device-" + d.binding_uid, text: "Revocă", on: { click: function () {
          if (confirm("Revoci dispozitivul „" + d.device_name + "”? Nu va mai putea sincroniza.")) api("POST", "/devices/revoke", { organization_uid: S.org, binding_uid: d.binding_uid }).then(function (r) { toast(r.ok ? "Dispozitiv revocat." : r.message); loadDevices(); });
        } } }) : null])]); });
      card.replaceChildren(h("h2", { text: "Dispozitive asociate" }), h("table", {}, [h("tr", {}, ["Nume", "Tip", "Cimitir", "Ultima activitate", "Stare", ""].map(function (t) { return h("th", { text: t }); }))].concat(rows)));
    });
  }

  function loadMe() { return api("GET", "/me").then(function (j) { if (j.ok) { S.me = j.result; renderDash(); } else start(); }); }

  function start() {
    clearInterval(S.timer); S.me = null; S.code = null;
    document.getElementById("who").textContent = ""; document.getElementById("btn-logout").classList.add("hidden");
    api("GET", "/me").then(function (j) {
      if (j.ok) { S.me = j.result; renderDash(); return; }
      api("GET", "/status").then(function (s) { if (!s.ok) { app.textContent = "Serverul nu răspunde."; return; } if (!s.result.initialized) renderBootstrap(s.result); else renderLogin(); });
    });
  }

  document.getElementById("btn-logout").addEventListener("click", function () { api("POST", "/logout", {}).then(function () { S.org = null; S.cem = null; start(); }); });
  start();
})();
