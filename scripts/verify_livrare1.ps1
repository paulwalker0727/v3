# Verificare Livrarea 1 (Windows, PowerShell) — rulează DOAR teste locale (SQLite simulat + Qt offscreen); nu atinge Supabase/PROD/Docker.
# Utilizare (din rădăcina depozitului):  powershell -ExecutionPolicy Bypass -File scripts\verify_livrare1.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONPATH = "$root\shared;$root\desktop;$root\backend\tests"
$env:CIMITIR_DB_ENGINE = "sqlite"
$env:CIMITIR_MODE = "local"
$env:CIMITIR_ALLOW_TEST_SECRETSTORE = "1"
Remove-Item Env:CIMITIR_TEST_PG_ADMIN_URL -ErrorAction SilentlyContinue

Write-Host "== 1/3 izolare copii + URL Supabase + flux A/B local (SQLite) =="
python -m pytest -n 0 -q backend\tests\test_cloud_associate_isolation.py backend\tests\test_etapa4_pas1.py backend\tests\test_etapa4_pas4_flux_b.py
if ($LASTEXITCODE -ne 0) { throw "FAIL: teste backend locale" }

Write-Host "== 2/3 configurare Cloud simplă (mediu TEST/PROD) =="
Push-Location desktop
python -m pytest -n 0 -q tests\test_cloud_simple.py tests\test_cloud_profiles.py tests\test_cloud_center.py
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { throw "FAIL: teste desktop Cloud" }

Write-Host "== 3/3 teste PostgreSQL + HTTP real (se rulează doar dacă ai PostgreSQL local de test) =="
if ($env:CIMITIR_TEST_PG_ADMIN_URL) {
    python -m pytest -n 0 -q backend\tests\test_cloud_associate_flux_b_pg.py backend\tests\test_cloud_associate_pg.py
} else {
    Write-Host "SKIP: setează CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres pentru testele PostgreSQL (altfel nu rulează)."
}
Write-Host "Verificare terminată."
