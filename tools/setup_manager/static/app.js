"use strict";
const TOKEN = document.querySelector('meta[name="sm-token"]').content;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let STATE = null, PLAN = null;

async function api(method, path, body) {
  const r = await fetch("/api" + path, {method, headers: {"X-SM-Token": TOKEN, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || ("HTTP " + r.status));
  return data;
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === "class") e.className = v; else if (k.startsWith("data-")) e.setAttribute(k, v); else e[k] = v; }
  for (const k of kids) e.append(k instanceof Node ? k : document.createTextNode(k == null ? "" : String(k)));
  return e;
}
const msg = (node, text, cls = "") => { node.textContent = text; node.className = "msg " + cls; };
const guard = (fn, out) => async (...a) => { try { await fn(...a); } catch (e) { if (out) msg(out, e.message, "bad"); else alert(e.message); } };

function renderResults(list, items, prefix = "check-result-") {
  list.replaceChildren(...items.map((r, i) => {
    const body = el("div", {}, el("b", {}, r.title), el("span", {}, r.detail));
    if (r.hint) body.append(el("em", {}, r.hint));
    const li = el("li", {class: r.status, "data-testid": prefix + (r.id || i)}, el("div", {class: "st"}, r.status), body);
    li.style.animationDelay = i * 60 + "ms";
    return li;
  }));
}

async function loadState() {
  STATE = await api("GET", "/state");
  const c = STATE.config;
  $$("#cfg-form input[name]").forEach(inp => {
    const v = c[inp.name];
    if (inp.dataset.secret !== undefined) { inp.value = ""; inp.placeholder = v && v.set ? "•••••• (setat — lasă gol ca să păstrezi)" : "nesetat"; }
    else if (document.activeElement !== inp) inp.value = v == null ? "" : v;
  });
  $("#persist").checked = !!c.persist_secrets; $("#persist").disabled = !c.dpapi_available;
  $("#persist").title = c.dpapi_available ? "" : "Disponibil doar pe Windows";
  $("#issues").textContent = STATE.issues.join("\n");
  $("#exec-phrase").textContent = STATE.exec_phrase;
  renderMode();
  if (STATE.checks && STATE.checks.length) renderResults($("#checks-list"), STATE.checks);
}
function renderMode() {
  const ex = STATE.mode === "execute", b = $("#mode-badge");
  b.textContent = ex ? `SCRIERE ACTIVĂ · ${Math.ceil(STATE.mode_remaining / 60)} min` : "DRY RUN / CHECK ONLY";
  b.className = "badge " + (ex ? "badge-exec" : "badge-dry");
  $("#mode-btn").textContent = ex ? "Revino la Dry Run" : "Activează scrierea…";
  $("#prod-badge").textContent = STATE.prod_blocked ? "PROD DETECTAT — BLOCAT" : "PROD BLOCAT";
  $("#prod-badge").className = "badge " + (STATE.prod_blocked ? "badge-alert" : "badge-lock");
}

$$(".nav").forEach(b => b.addEventListener("click", () => {
  $$(".nav").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "pairing") loadPairing();
  if (b.dataset.tab === "docker") dockerRefresh();
}));
$$("[data-eye]").forEach(b => b.addEventListener("click", () => { const i = b.previousElementSibling; i.type = i.type === "password" ? "text" : "password"; }));

$("#cfg-form").addEventListener("submit", guard(async ev => {
  ev.preventDefault();
  const values = {};
  $$("#cfg-form input[name]").forEach(i => { if (i.dataset.secret !== undefined) { if (i.value) values[i.name] = i.value; } else values[i.name] = i.value; });
  values.persist_secrets = $("#persist").checked;
  STATE = await api("PUT", "/config", {values});
  $$("[data-secret]").forEach(i => { i.value = ""; i.type = "password"; });
  await loadState();
  msg($("#cfg-msg"), "Configurarea a fost salvată (secretele rămân doar în memorie" + (values.persist_secrets ? " + DPAPI" : "") + ")." + (STATE.issues.length ? " ATENȚIE: configurația are probleme (vezi mai sus)." : ""), STATE.issues.length ? "warn" : "ok");
}, $("#cfg-msg")));

$("#mode-btn").addEventListener("click", guard(async () => {
  if (STATE.mode === "execute") { await api("POST", "/mode", {mode: "dry_run"}); await loadState(); return; }
  $("#mode-phrase").value = ""; $("#mode-err").textContent = ""; $("#mode-dialog").showModal();
}));
$("#mode-ok").addEventListener("click", async ev => {
  ev.preventDefault();
  try { await api("POST", "/mode", {mode: "execute", confirm: $("#mode-phrase").value}); $("#mode-dialog").close(); await loadState(); }
  catch (e) { $("#mode-err").textContent = e.message; }
});

$("#run-checks").addEventListener("click", guard(async () => {
  msg($("#checks-sum"), "Se rulează…");
  const r = await api("POST", "/checks");
  renderResults($("#checks-list"), r.results);
  const s = r.summary;
  msg($("#checks-sum"), `PASS ${s.PASS} · WARN ${s.WARN} · FAIL ${s.FAIL} · SKIP ${s.SKIP}`, s.FAIL ? "bad" : "ok");
}, $("#checks-sum")));
$("#bucket-btn").addEventListener("click", guard(async () => {
  const r = await api("POST", "/storage/bucket", {confirm: $("#bucket-confirm").value});
  $("#bucket-out").textContent = r.message + (r.detail ? "\n" + r.detail : ""); await loadState();
}, $("#bucket-out")));

const stateTag = s => ({"APLICATĂ": "good", "APLICATĂ_NEÎNREGISTRATĂ": "warn", "LIPSĂ": "warn", "INCONSISTENTĂ": "err", "PARȚIALĂ": "err", "NEVERIFICABILĂ": "lock"}[s] || "lock");
$("#plan-btn").addEventListener("click", guard(async () => {
  msg($("#plan-msg"), "Se analizează baza de date…");
  const r = await api("GET", "/migrations/plan");
  PLAN = r.plan;
  $("#apply-out").textContent = "";
  $("#plan-box").classList.remove("hidden");
  $("#mig-rows").replaceChildren(...r.migrations.map(m => {
    const tr = el("tr", {class: m.version >= 23 ? "hi" : "", "data-testid": "migration-row-" + String(m.version).padStart(3, "0")},
      el("td", {}, m.name), el("td", {}, el("span", {class: "tag " + stateTag(m.state)}, m.state)),
      el("td", {}, `${m.present}/${m.checked}`), el("td", {}, (m.recorded ? "înregistrată" : "neînregistrată") + (m.missing.length ? " · lipsesc: " + m.missing.slice(0, 5).join(", ") : "")));
    return tr;
  }));
  $("#plan-steps").replaceChildren(...PLAN.steps.map(s => el("li", {}, `${String(s.version).padStart(3, "0")} ${s.name} → ${s.action === "apply" ? "APLICĂ" : "ÎNREGISTREAZĂ ÎN ISTORIC"} (${s.why})`)));
  if (!PLAN.steps.length) $("#plan-steps").replaceChildren(el("li", {}, "Nimic de aplicat."));
  $("#plan-blocked").replaceChildren(...PLAN.blocked.map(b => el("li", {}, "BLOCAT: " + b)));
  $("#apply-hint").textContent = PLAN.confirm_phrase ? "Confirmare: " + PLAN.confirm_phrase : "";
  $("#backup-path").value = $("#backup-path").value || STATE.config.backup_path || "";
  $("#apply-btn").disabled = !PLAN.executable;
  msg($("#plan-msg"), `Plan ${PLAN.plan_hash} · ${PLAN.steps.length} pași · ${PLAN.executable ? "executabil" : "NEexecutabil"} · mod: ${r.mode === "execute" ? "scriere" : "Dry Run"}`, PLAN.blocked.length ? "bad" : "ok");
}, $("#plan-msg")));
$("#backup-btn").addEventListener("click", guard(async () => {
  const r = await api("POST", "/migrations/backup", {path: $("#backup-path").value});
  msg($("#backup-out"), (r.ok ? "BACKUP OK — " : "BACKUP INVALID — ") + r.detail + (r.ok ? ` · ${(r.size / 1048576).toFixed(2)} MB · ${r.age_hours} h` : ""), r.ok ? (r.stale ? "" : "ok") : "bad");
}, $("#backup-out")));
$("#apply-btn").addEventListener("click", guard(async () => {
  const r = await api("POST", "/migrations/apply", {plan_hash: PLAN.plan_hash, confirm: $("#apply-confirm").value, backup_path: $("#backup-path").value});
  $("#apply-out").textContent = r.message + "\n" + (r.log || []).join("\n");
  await loadState();
}, $("#apply-out")));

async function dockerRefresh() {
  const r = await api("GET", "/docker/status");
  msg($("#docker-msg"), r.docker ? `Docker ${r.docker_version} · port ${r.port}: ${r.port_free ? "liber" : "ocupat" + (r.port_owner ? " de " + r.port_owner : "")}` : (r.error || "Docker indisponibil"), r.docker ? "ok" : "bad");
  $("#docker-rows").replaceChildren(...(r.containers || []).map(c => el("tr", {"data-testid": "docker-row-" + c.name},
    el("td", {}, c.name, c.protected ? el("span", {class: "tag lock"}, " PROTEJAT") : ""), el("td", {}, c.image), el("td", {}, c.status), el("td", {}, c.ports), el("td", {}))));
}
$("#docker-refresh").addEventListener("click", guard(dockerRefresh, $("#docker-msg")));
let PHRASE = "";
$$("[data-dplan]").forEach(b => b.addEventListener("click", guard(async () => {
  const r = await api("POST", "/docker/plan", {kind: b.dataset.dplan});
  $("#docker-plan").textContent = [`Comandă: ${r.command}`, `Pregătit: ${r.ready ? "DA" : "NU"}`, ...r.issues.map(i => "✖ " + i), ...r.warnings.map(i => "⚠ " + i),
    ...(r.env_keys ? ["Variabile de mediu (doar nume): " + r.env_keys.map(k => `${k.key} [${k.source}]`).join(", ")] : [])].join("\n");
}, $("#docker-plan"))));
$$("[data-drun]").forEach(b => b.addEventListener("click", guard(async () => {
  const r = await api("POST", "/docker/run", {kind: b.dataset.drun, confirm: $("#docker-confirm").value});
  if (r.dry_run) { $("#docker-job").textContent = `Dry Run — nimic executat. Confirmare cerută în modul scriere: ${r.confirm_phrase}`; return; }
  await followJob(r.job, $("#docker-job")); await loadState(); dockerRefresh();
}, $("#docker-job"))));
$("#docker-logs").addEventListener("click", guard(async () => { const r = await api("GET", "/docker/logs"); $("#docker-job").textContent = r.logs || "(fără loguri)"; }, $("#docker-job")));
async function followJob(id, out) {
  for (;;) {
    const j = await api("GET", "/jobs/" + id);
    out.textContent = j.lines.join("\n") || "…";
    out.scrollTop = out.scrollHeight;
    if (j.status !== "running") { out.textContent += "\n— " + (j.status === "done" ? "terminat" : "EȘUAT") + " —" + (j.result ? "\n" + JSON.stringify(j.result) : ""); return; }
    await new Promise(r => setTimeout(r, 1200));
  }
}

async function loadPairing() {
  const r = await api("GET", "/pairing");
  $("#snippet").textContent = r.snippet;
  $("#manual").replaceChildren(...r.manual.map(s => {
    const note = el("input", {value: s.note || "", placeholder: "Notă (opțional)", "data-testid": "manual-note-" + s.id});
    const mk = (label, st, cls) => el("button", {class: "btn " + cls, "data-testid": `manual-${st}-${s.id}`, onclick: guard(async () => { await api("PUT", "/pairing/manual", {step: s.id, status: st, note: note.value}); loadPairing(); })}, label);
    return el("div", {class: "manual-item", "data-testid": "manual-item-" + s.id}, el("b", {}, s.title), el("span", {class: "tag " + (s.status === "pass" ? "good" : s.status === "fail" ? "err" : "lock"), style: "margin-left:10px"}, s.status.toUpperCase()),
      el("p", {}, s.how), el("div", {class: "row"}, note, mk("PASS", "pass", ""), mk("FAIL", "fail", "btn-danger"), mk("Resetează", "pending", "")));
  }));
}
$("#probe-btn").addEventListener("click", guard(async () => { const r = await api("POST", "/pairing/probe"); renderResults($("#probe-list"), r.results, "probe-result-"); }));
$$("[data-iso]").forEach(b => b.addEventListener("click", guard(async () => {
  const r = await api("POST", "/pairing/isolated", {which: b.dataset.iso, approval: $("#iso-approval").value, org_uid: $("#iso-org").value, email: $("#iso-email").value, password: $("#iso-pass").value});
  $("#iso-pass").value = "";
  if (r.dry_run) { $("#iso-out").textContent = r.message; return; }
  await followJob(r.job, $("#iso-out")); await loadState();
}, $("#iso-out"))));

$("#report-btn").addEventListener("click", guard(async () => {
  const r = await api("POST", "/report"); $("#report-out").textContent = r.text; msg($("#report-msg"), "Salvat: " + r.path, "ok");
}, $("#report-msg")));

loadState().catch(e => { $("#issues").textContent = e.message; });
setInterval(() => { if (STATE && STATE.mode === "execute") loadState().catch(() => {}); }, 20000);
