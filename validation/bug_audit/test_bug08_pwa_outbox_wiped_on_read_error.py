"""BUG-08 — PWA: o eroare trecătoare la citirea cozii offline (IndexedDB) face ca următoarea salvare să ȘTEARGĂ coada.

Fișier/cauză: backend/static/mobile_app.js:127 `outbox()` înghite orice eroare a lui `kvGet('outbox')` și întoarce `[]`;
apelanții scriu apoi lista „goală + operațiunea nouă” peste coadă: :240 (salvarea unei fișe: `ob.push(op); saveOutbox(ob)`) și
:141/:149 în `syncNow` (după push: lista recitită goală -> `saveOutbox([])`). Pe iPhone/Safari, „Connection to Indexed Database
server lost” este o eroare cunoscută, trecătoare. Rezultat: modificările făcute offline și încă netrimise dispar fără avertisment.
Testul rulează EXACT liniile din fișier (extrase cu regex) în Node, cu `kvGet` care eșuează o singură dată.
AȘTEPTAT: coada păstrează operațiunile vechi (sau salvarea e refuzată). OBȚINUT (fără fix): rămâne doar operațiunea nouă / nimic.
"""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT

APP = ROOT / "backend" / "static" / "mobile_app.js"

NODE = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const defs = src.match(/async function outbox\(\)\{[^\n]*\}\nasync function saveOutbox\(o\)\{[^\n]*\}/)[0];
const addOp = src.match(/let ob=\(await outbox\(\)\)\.filter\(o=>!\(o\.uid===op\.uid&&o\.state==='pending'\)\);ob\.push\(op\);await saveOutbox\(ob\);/)[0];
const freshRead = src.match(/ob=await outbox\(\);\s*\/\/ citire proaspătă/)[0].replace(/\s*\/\/.*$/, '');
const ctx = {store: {}, fail: 0, out: {}, renderSyncBtn() {},
  async kvGet(k) { if (ctx.fail > 0) { ctx.fail--; throw new Error('Connection to Indexed Database server lost'); } return ctx.store[k] ?? null; },
  async kvSet(k, v) { ctx.store[k] = JSON.parse(JSON.stringify(v)); }};
vm.createContext(ctx);
vm.runInContext(defs + `
async function scenarioAdd(){ try { const op={op_id:'m-new',uid:'u-new',state:'pending'}; ${addOp} } catch(_e){ /* producție: form.onsubmit prinde eroarea și afișează toast; coada rămâne intactă */ } }
async function scenarioSync(){ try { let ob=[]; ${freshRead}; for(const x of [{op_id:'m-1',status:'applied'}]){const o=ob.find(z=>z.op_id===x.op_id);if(!o)continue;ob=ob.filter(z=>z!==o);} await saveOutbox(ob); } catch(_e){ /* producție: syncNow are try/catch exterior; saveOutbox nu rulează */ } }
`, ctx);
(async () => {
  const seed = [{op_id:'m-1',uid:'u1',state:'pending'},{op_id:'m-2',uid:'u2',state:'pending'},{op_id:'m-3',uid:'u3',state:'conflict'}];
  ctx.store.outbox = seed; ctx.fail = 1; await vm.runInContext('scenarioAdd()', ctx); ctx.out.add = ctx.store.outbox.map(o => o.op_id);
  ctx.store.outbox = seed; ctx.fail = 1; await vm.runInContext('scenarioSync()', ctx); ctx.out.sync = ctx.store.outbox.map(o => o.op_id);
  console.log(JSON.stringify(ctx.out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node lipsește")
def test_transient_indexeddb_error_does_not_wipe_offline_queue(tmp_path):
    script = tmp_path / "repro.js"
    script.write_text(NODE, encoding="utf-8")
    p = subprocess.run(["node", str(script), str(APP)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    lost_add = {"m-1", "m-2", "m-3"} - set(out["add"])
    lost_sync = {"m-2", "m-3"} - set(out["sync"])
    assert not lost_add and not lost_sync, (
        f"coada offline pierdută după o eroare IndexedDB: la salvarea unei fișe lipsesc {sorted(lost_add)} (rămas {out['add']}); "
        f"la sincronizare lipsesc {sorted(lost_sync)} (rămas {out['sync']})")
