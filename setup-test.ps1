<#
.SYNOPSIS
  Pornește Cimitir Sync v2 — Setup Manager (local, doar TEST) printr-o singură comandă.
.DESCRIPTION
  - creează (o singură dată) un mediu Python izolat în %LOCALAPPDATA%\CimitirSetupManager\venv și instalează dependențele din tools\setup_manager\requirements.txt;
  - pornește interfața pe http://127.0.0.1:<Port>/ (DOAR loopback) și deschide browserul;
  - mod implicit: Dry Run / Check Only. Nu cere și nu primește parole/chei ca parametri; le introduci în interfață.
  NU rulează docker, git, migrări sau comenzi SQL. NU atinge containerul de pe 8084.
.PARAMETER Port       Portul interfeței Setup Manager (implicit 8765; nu poate fi 8084/8085).
.PARAMETER NoBrowser  Nu deschide browserul.
.PARAMETER Reinstall  Recreează mediul Python.
.PARAMETER SelfTest   Rulează testele automate ale Setup Manager (fără rețea externă, fără PostgreSQL decât dacă dai -PgUrl).
.PARAMETER PgUrl      (doar cu -SelfTest) URL admin către un PostgreSQL LOCAL descartabil pentru testele de migrare (ex. postgresql://postgres:***@127.0.0.1:5432/postgres). Nu folosi Supabase.
.EXAMPLE
  .\setup-test.ps1
  .\setup-test.ps1 -SelfTest
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$Reinstall,
    [switch]$SelfTest,
    [string]$PgUrl = ""
)
$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$SmDir = Join-Path $Repo "tools\setup_manager"
$Home_ = Join-Path $(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME ".cache" }) "CimitirSetupManager"
$Venv = Join-Path $Home_ "venv"

if ($Port -in 8084, 8085) { throw "Portul $Port este rezervat (8084 = container vechi, 8085 = Cloud TEST). Alege altul pentru interfață." }
if (-not (Test-Path (Join-Path $SmDir "server.py"))) { throw "Rulează scriptul din rădăcina repo-ului (lipsește tools\setup_manager)." }
if ($PgUrl -and $PgUrl -match "supabase") { throw "Testele de migrare NU se rulează pe Supabase. Folosește un PostgreSQL local descartabil." }

function Find-Python {
    foreach ($c in @(@("py", "-3"), @("python"), @("python3"))) {
        try {
            $exe = $c[0]; $extra = @($c | Select-Object -Skip 1)
            $v = & $exe @extra -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [version]$v -ge [version]"3.10") { return ,(@($exe) + $extra) }
        } catch { }
    }
    throw "Nu am găsit Python 3.10+. Instalează-l de pe python.org (bifează „Add to PATH”) și rulează din nou."
}

New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
if ($Reinstall -and (Test-Path $Venv)) { Remove-Item -Recurse -Force $Venv }
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $py = Find-Python
    Write-Host "Creez mediul Python izolat în $Venv ..." -ForegroundColor Cyan
    & $py[0] @($py | Select-Object -Skip 1) -m venv $Venv
}
$reqFile = Join-Path $SmDir $(if ($SelfTest) { "requirements-dev.txt" } else { "requirements.txt" })
$stamp = Join-Path $Venv ("deps-" + (Split-Path $reqFile -Leaf) + ".sha256")
$hash = (Get-FileHash $reqFile -Algorithm SHA256).Hash
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $hash) {
    Write-Host "Instalez dependențele (o singură dată)..." -ForegroundColor Cyan
    & $VenvPy -m pip install --disable-pip-version-check -q -r $reqFile
    if ($LASTEXITCODE -ne 0) { throw "Instalarea dependențelor a eșuat." }
    Set-Content -Path $stamp -Value $hash
}

$env:PYTHONPATH = "$Repo;$Repo\shared"
$env:PYTHONDONTWRITEBYTECODE = "1"
Push-Location $Repo
try {
    if ($SelfTest) {
        if ($PgUrl) { $env:SM_TEST_PG_URL = $PgUrl }
        & $VenvPy -m pytest "tools/setup_manager/tests" -c "tools/setup_manager/pytest.ini" --rootdir=. -q
        exit $LASTEXITCODE
    }
    $inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($inUse) { throw "Portul $Port este ocupat. Rulează cu -Port <altul>." }
    $argsList = @("-m", "tools.setup_manager", "--port", "$Port")
    if ($NoBrowser) { $argsList += "--no-browser" }
    Write-Host "Setup Manager pornește pe http://127.0.0.1:$Port/  (Dry Run / Check Only). Oprire: Ctrl+C" -ForegroundColor Green
    & $VenvPy @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
