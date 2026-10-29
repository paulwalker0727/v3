# Verificare Livrările 2/4 + inițializare admin (Windows, PowerShell): doar teste locale (SQLite + Qt offscreen + teste unitare de grant). Nu atinge Supabase/PROD/Docker.
# Utilizare:  powershell -ExecutionPolicy Bypass -File scripts\verify_livrari_2_4.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONPATH = "$root\shared;$root\desktop;$root\backend\tests"
$env:CIMITIR_DB_ENGINE = "sqlite"
$env:CIMITIR_MODE = "local"
$env:CIMITIR_ALLOW_TEST_SECRETSTORE = "1"
Remove-Item Env:CIMITIR_TEST_PG_ADMIN_URL -ErrorAction SilentlyContinue
python -m pytest -n 0 -q backend\tests\test_portable_backup.py backend\tests\test_cloud_associate_isolation.py backend\tests\test_cloud_init_admin_pg.py backend\tests\test_etapa3_registries.py
if ($LASTEXITCODE -ne 0) { throw "FAIL" }
Write-Host "Testele PostgreSQL (RLS, inițializare admin prin HTTP) rulează doar cu CIMITIR_TEST_PG_ADMIN_URL setat; altfel sunt SKIP."
