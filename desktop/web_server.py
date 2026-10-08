"""
Server LAN pentru scanarea documentelor de pe telefon (Cimitir MAIN).

Singura responsabilitate: pagina `/mobile-scan` (cameră telefon) + `/api/ai-scan-upload` / `/api/ai-scan-result/{session}`.
Portalul public și administrarea web sunt în backend/server.py (pornite de start_local.py / launcher.py);
API-ul operator vechi și interfața React au fost eliminate.

Pornit de launcher.py („Server local") pe portul portalului + 1; rulează pe aceeași bază ca desktopul (database.py).
"""
import os
import sys
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import database as cim  # noqa: E402

app = FastAPI(title="Cimitir — scanare de pe telefon")

# ============================================================================
# AI SCANARE — endpoints pentru scanare din telefon (session-based)
# ============================================================================
_AI_SCAN_SESSIONS: dict = {}  # session_id -> {"status": "...", "extracted": {...}, "file_path": "..."}


@app.get("/mobile-scan", response_class=HTMLResponse)
def mobile_scan_page(session: str = "", type: str = "buletin"):
    """Pagină simplă pentru telefon: capture cameră → upload către /api/ai-scan-upload."""
    if not session:
        return HTMLResponse("<h3>Sesiune lipsă</h3>", status_code=400)
    doc_label = "Buletin CI" if type == "buletin" else "Certificat deces"
    return HTMLResponse(f"""<!doctype html>
<html lang="ro"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"/>
<title>Scanare {doc_label}</title>
<style>
  * {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
  body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,sans-serif;
    background:linear-gradient(180deg,#0f172a 0%,#020617 100%); color:#e2e8f0; min-height:100vh; padding:16px; }}
  .card {{ background:#1e293b; border-radius:12px; padding:20px; max-width:540px; margin:0 auto; }}
  h1 {{ margin:0 0 4px; font-size:18pt; color:#f1f5f9; }}
  .sub {{ color:#94a3b8; font-size:11pt; margin-bottom:20px; }}
  .badge {{ display:inline-block; background:#0ea5e9; color:white; padding:4px 12px;
    border-radius:20px; font-weight:600; font-size:10pt; margin-bottom:16px; }}
  input[type=file] {{ display:none; }}
  label.pick {{ display:block; text-align:center; background:#0ea5e9; color:white;
    padding:24px; border-radius:10px; font-size:14pt; font-weight:600; cursor:pointer;
    border:2px dashed rgba(255,255,255,.3); margin-bottom:14px; }}
  label.pick:active {{ background:#0284c7; }}
  #preview {{ max-width:100%; border-radius:8px; margin:14px 0; display:none; }}
  button {{ width:100%; padding:16px; background:#22c55e; color:white; border:none;
    border-radius:10px; font-size:13pt; font-weight:700; margin-top:8px; }}
  button:disabled {{ background:#334155; color:#64748b; }}
  #status {{ text-align:center; padding:16px; margin-top:12px; border-radius:8px;
    background:#0f172a; border:1px solid #334155; font-size:11pt; }}
  .ok {{ color:#22c55e; }}
  .err {{ color:#ef4444; }}
</style>
</head><body>
<div class="card">
  <div class="badge">📱  Scanare de pe telefon</div>
  <h1>{doc_label}</h1>
  <div class="sub">Fă o poză clară a documentului sau alege una din galerie.</div>

  <label for="fileInput" class="pick" id="pickLbl">
    📷  Deschide camera / Alege poză
  </label>
  <input id="fileInput" type="file" accept="image/*" capture="environment"/>

  <img id="preview"/>

  <button id="uploadBtn" disabled>⏫  Trimite pentru analiză AI</button>

  <div id="status">Selectează o poză pentru a începe.</div>
</div>

<script>
const SESSION = "{session}";
const TYPE = "{type}";
const $ = id => document.getElementById(id);
let file = null;

$("fileInput").addEventListener("change", e => {{
  file = e.target.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  const img = $("preview");
  img.src = url;
  img.style.display = "block";
  $("uploadBtn").disabled = false;
  $("status").textContent = "Poză selectată: " + file.name + " (" + (file.size/1024).toFixed(0) + " KB)";
  $("pickLbl").textContent = "🔄  Alege altă poză";
}});

$("uploadBtn").addEventListener("click", async () => {{
  if (!file) return;
  const btn = $("uploadBtn");
  btn.disabled = true;
  btn.textContent = "🔍  Se analizează cu Gemini AI…";
  $("status").textContent = "⏳  Trimit poza către server…";
  $("status").className = "";
  const fd = new FormData();
  fd.append("session", SESSION);
  fd.append("doc_type", TYPE);
  fd.append("file", file);
  try {{
    const res = await fetch("/api/ai-scan-upload", {{ method:"POST", body:fd }});
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (data.status === "done") {{
      const fields = data.extracted && data.extracted.fields || {{}};
      const nume = (fields.nume && fields.nume.value) || "";
      const prenume = (fields.prenume && fields.prenume.value) || "";
      $("status").className = "ok";
      $("status").innerHTML = "✅  <b>Date extrase cu succes!</b><br>" +
        "Persoană: " + (nume + " " + prenume).trim() + "<br>" +
        "Datele au fost trimise către aplicația desktop.";
      btn.textContent = "✅  Trimis către desktop";
    }} else {{
      $("status").className = "err";
      $("status").textContent = "❌  Extragere eșuată: " + (data.error || "necunoscut");
      btn.disabled = false;
      btn.textContent = "🔁  Încearcă din nou";
    }}
  }} catch (e) {{
    $("status").className = "err";
    $("status").textContent = "❌  Eroare: " + e.message;
    btn.disabled = false;
    btn.textContent = "🔁  Încearcă din nou";
  }}
}});
</script>
</body></html>""")


@app.post("/api/ai-scan-upload")
async def ai_scan_upload(
    session: str = Form(...),
    doc_type: str = Form("buletin"),
    file: UploadFile = File(...),
):
    """Endpoint pentru telefonul mobil: primește poza + rulează extragerea Gemini."""
    if doc_type not in ("buletin", "certificat_deces"):
        raise HTTPException(400, "Tip document invalid")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Fișier gol")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(400, "Fișier prea mare (max 15 MB)")

    # salvăm poza pe disc
    media_dir = cim._media_dir("mobile_scan")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    ext = os.path.splitext(file.filename or "photo.jpg")[1].lower() or ".jpg"
    dest = os.path.join(media_dir, f"{session}_{ts}{ext}")
    with open(dest, "wb") as f:
        f.write(content)

    _AI_SCAN_SESSIONS[session] = {"status": "processing"}

    try:
        from ai_scanare.ai_client import _extract_async, _file_to_base64, _compress_and_encode
        b64, _mime = _file_to_base64(dest)
        if len(b64) > 6 * 1024 * 1024:
            b64, _mime = _compress_and_encode(dest)
        result = await _extract_async(b64, doc_type)
        _AI_SCAN_SESSIONS[session] = {
            "status": "done",
            "document_type": doc_type,
            "extracted": result,
            "file_path": dest,
        }
        return _AI_SCAN_SESSIONS[session]
    except Exception as e:
        _AI_SCAN_SESSIONS[session] = {"status": "failed", "error": str(e)}
        raise HTTPException(500, f"Extragere eșuată: {e}")


@app.get("/api/ai-scan-result/{session}")
def ai_scan_result(session: str):
    """Desktop face polling la acest endpoint pentru rezultat."""
    return _AI_SCAN_SESSIONS.get(session, {"status": "waiting"})


@app.delete("/api/ai-scan-result/{session}")
def ai_scan_clear(session: str):
    _AI_SCAN_SESSIONS.pop(session, None)
    return {"ok": True}


def build_app():
    cim.init_db()
    return app


def run(host: str = "127.0.0.1", port: int = 8001):
    build_app()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
