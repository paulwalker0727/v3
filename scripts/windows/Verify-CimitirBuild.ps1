<#
.SYNOPSIS
  Verificare POST-BUILD pentru Cimitir.exe (Windows). NETESTAT pe Windows real la livrarea acestui script.
.DESCRIPTION
  Rulează automat ce se poate verifica fără interfață: fișierul și resursele din pachet, semnătura (informativ), portalul pornit din exe,
  copia de rezervă programată (`--backup-run`) pe un folder TEMPORAR cu date proprii. Nu atinge datele reale (folosește un folder temporar nou).
  NU înlocuiește validarea manuală (fereastra MAIN, hartă, QR, PDF, telefon): aceasta apare mai jos ca PENDING MANUAL VALIDATION.
.EXAMPLE
  .\scripts\windows\Verify-CimitirBuild.ps1 -DistDir desktop\dist\Cimitir
#>
[CmdletBinding()]
param(
    [string]$DistDir = "desktop\dist\Cimitir",
    [int]$Port = 8766,
    [switch]$SkipPortal
)
$ErrorActionPreference = "Continue"
$script:results = New-Object System.Collections.ArrayList
function Add-Result([string]$Status, [string]$Name, [string]$Detail = "") {
    [void]$script:results.Add([pscustomobject]@{ Status = $Status; Name = $Name; Detail = $Detail })
    Write-Host ("[{0}] {1}  {2}" -f $Status, $Name, $Detail)
}

$exe = Join-Path $DistDir "Cimitir.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    Add-Result "FAIL" "Cimitir.exe există" "lipsește: $exe (rulează mai întâi desktop\build_exe.bat)"
    exit 1
}
$item = Get-Item -LiteralPath $exe
Add-Result "PASS" "Cimitir.exe există" ("{0:N1} MB" -f ($item.Length / 1MB))
if ($item.Length -lt 1MB) { Add-Result "FAIL" "Dimensiune exe plauzibilă" "sub 1 MB" }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
Add-Result "PASS" "SHA-256 (notează-l în raportul de livrare)" $hash
$ver = $item.VersionInfo
Add-Result "PASS" "Versiune fișier" ("{0} / {1}" -f $ver.FileVersion, $ver.ProductVersion)

$sig = Get-AuthenticodeSignature -LiteralPath $exe
if ($sig.Status -eq "Valid") { Add-Result "PASS" "Semnătură digitală" $sig.SignerCertificate.Subject }
else { Add-Result "WARN" "Semnătură digitală" ("{0}: SmartScreen va avertiza utilizatorii (vezi desktop\BUILD.md)" -f $sig.Status) }

# Resurse necesare (PyInstaller 6+ le pune în _internal; versiunile mai vechi în rădăcină)
$roots = @((Join-Path $DistDir "_internal"), $DistDir)
function Find-Resource([string]$rel) {
    foreach ($r in $roots) { $p = Join-Path $r $rel; if (Test-Path -LiteralPath $p) { return $p } }
    return $null
}
$required = @("fonts", "templates", "docs", "icon.ico", "LICENSE.txt", "portal\templates", "portal\static", "portal\fonts",
              "backend\cimitir\pg_migrations", "tools\setup_manager")
foreach ($rel in $required) {
    $p = Find-Resource $rel
    if ($p) { Add-Result "PASS" "Resursă în pachet: $rel" $p } else { Add-Result "FAIL" "Resursă în pachet: $rel" "lipsește din $DistDir" }
}
$mig = Find-Resource "backend\cimitir\pg_migrations"
if ($mig) {
    $count = (Get-ChildItem -LiteralPath $mig -Filter "*.sql").Count
    if ($count -ge 28) { Add-Result "PASS" "Migrări incluse (nu se execută)" "$count fișiere .sql" } else { Add-Result "FAIL" "Migrări incluse" "$count < 28" }
}
$font = Find-Resource "fonts\LiberationSans-Regular.ttf"
if ($font) { Add-Result "PASS" "Font PDF" $font } else { Add-Result "FAIL" "Font PDF (LiberationSans)" "lipsește: PDF-urile și etichetele ar eșua" }

# Date temporare: nu se folosește niciodată folderul real al utilizatorului
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("cimitir-verify-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$data = Join-Path $tmp "data"
New-Item -ItemType Directory -Path $data -Force | Out-Null
$env:CIMITIR_DATA_DIR = $data
$env:CIMITIR_ENV_FILE = Join-Path $tmp "nu.env"

if ($SkipPortal) {
    Add-Result "SKIP" "Portal pornit din exe" "-SkipPortal"
} else {
    $p = Start-Process -FilePath $exe -ArgumentList @("--portal", "--host", "127.0.0.1", "--port", "$Port") -PassThru
    $ok = $false
    foreach ($i in 1..60) {
        Start-Sleep -Seconds 1
        try { if ((Invoke-RestMethod "http://127.0.0.1:$Port/api/public/health").ok) { $ok = $true; break } } catch { }
    }
    if ($ok) {
        Add-Result "PASS" "Portal pornit din Cimitir.exe" "/api/public/health"
        foreach ($path in @("/portal", "/admin/access", "/portal/harta")) {
            try { $code = (Invoke-WebRequest "http://127.0.0.1:$Port$path" -UseBasicParsing).StatusCode } catch { $code = $_.Exception.Response.StatusCode.value__ }
            if ($code -eq 200) { Add-Result "PASS" "GET $path" "200" } else { Add-Result "FAIL" "GET $path" "cod $code" }
        }
    } else {
        Add-Result "FAIL" "Portal pornit din Cimitir.exe" "nu a răspuns în 60 s (rulează exe-ul cu console=True pentru eroare)"
    }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Copie programată fără interfață, pe baza creată în folderul TEMPORAR (dacă portalul a creat-o)
$dbFile = Get-ChildItem -LiteralPath $data -Recurse -Include "cimitir.db", "database.sqlite" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $dbFile) {
    Add-Result "SKIP" "Copie programată (--backup-run)" "nu există bază în folderul temporar (portalul nu a rulat sau nu a creat-o)"
} else {
    $b = Start-Process -FilePath $exe -ArgumentList @("--backup-run", "--force", "--data-dir", "`"$data`"") -PassThru -Wait
    if ($b.ExitCode -eq 0) { Add-Result "PASS" "Cimitir.exe --backup-run" "cod 0" } else { Add-Result "FAIL" "Cimitir.exe --backup-run" "cod $($b.ExitCode)" }
    $manifest = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "manifest.json" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($manifest) { Add-Result "PASS" "Copie cu manifest SHA-256" $manifest.DirectoryName } else { Add-Result "FAIL" "Copie cu manifest SHA-256" "nu s-a creat nicio copie sub $tmp" }
    $log = Get-ChildItem -LiteralPath $tmp -Filter "*.backup-log.jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($log) { Add-Result "PASS" "Jurnal backup" $log.FullName } else { Add-Result "FAIL" "Jurnal backup" "lipsește" }
}

Remove-Item Env:\CIMITIR_DATA_DIR, Env:\CIMITIR_ENV_FILE -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "PENDING MANUAL VALIDATION (nu se pot verifica automat):"
foreach ($m in @("Pornirea ferestrei MAIN din exe/instalator pe un Windows curat (fără Python)",
                 "Autentificare, hartă, deschiderea ferestrelor Copii de rezervă / Integritate / Persoane duplicate / Avize",
                 "Generare PDF (fișă loc, contract) cu diacritice", "QR + asociere telefon + sincronizare reală (Cloud TEST doar cu aprobare)",
                 "Instalator Inno Setup: instalare/dezinstalare, scurtături", "Programarea Task Scheduler cu scripts\windows\Register-CimitirBackupTask.ps1",
                 "Comportamentul antivirus/SmartScreen")) {
    Write-Host "  [PENDING] $m"
}
$fail = ($script:results | Where-Object { $_.Status -eq "FAIL" }).Count
$summary = ($script:results | Group-Object Status | ForEach-Object { "{0} {1}" -f $_.Count, $_.Name }) -join ", "
Write-Host ""
Write-Host "TOTAL: $summary"
if ($fail -gt 0) { exit 1 } else { exit 0 }
