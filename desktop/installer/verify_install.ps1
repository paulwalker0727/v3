<#
 Verifică pe un PC Windows o instalare Cimitir (după CimitirSetup-<ver>.exe sau varianta folder).
 Rulează:  powershell -ExecutionPolicy Bypass -File verify_install.ps1 [-InstallDir "C:\Program Files\Cimitir"] [-Port 8766]
 Nu atinge datele reale: folosește un folder temporar pentru baza de date.
#>
param(
  [string]$InstallDir = "",
  [int]$Port = 8766
)
$ErrorActionPreference = "Stop"
$candidates = @($InstallDir, "$env:ProgramFiles\Cimitir", "${env:ProgramFiles(x86)}\Cimitir", "$env:LOCALAPPDATA\Programs\Cimitir") | Where-Object { $_ }
$dir = $candidates | Where-Object { Test-Path (Join-Path $_ "Cimitir.exe") } | Select-Object -First 1
$results = @()
function Check($name, [bool]$ok, $detail = "") { $script:results += [pscustomobject]@{ Verificare = $name; Rezultat = $(if ($ok) { "PASS" } else { "FAIL" }); Detalii = $detail } }

Check "Cimitir.exe găsit" ([bool]$dir) "$dir"
if ($dir) {
  Check "Șabloane portal incluse" (Test-Path (Join-Path $dir "_internal\portal\templates\base.html")) "_internal\portal\templates"
  Check "Fișiere statice portal" (Test-Path (Join-Path $dir "_internal\portal\static\app.css")) "_internal\portal\static"
  Check "Licența (EULA) inclusă" (Test-Path (Join-Path $dir "_internal\LICENSE.txt")) ""
  $data = Join-Path $env:TEMP ("cimitir-verify-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Path $data | Out-Null
  $env:CIMITIR_DATA_DIR = $data
  $proc = Start-Process -FilePath (Join-Path $dir "Cimitir.exe") -ArgumentList "--portal", "--host", "127.0.0.1", "--port", $Port -PassThru
  $up = $false
  foreach ($i in 1..60) {
    Start-Sleep -Seconds 1
    try { if ((Invoke-RestMethod "http://127.0.0.1:$Port/api/public/health").ok) { $up = $true; break } } catch {}
  }
  Check "Portalul pornește din Cimitir.exe --portal" $up "port $Port"
  if ($up) {
    foreach ($p in "/portal", "/portal/harta", "/portal/cerere", "/admin/access") {
      $code = 0; try { $code = (Invoke-WebRequest "http://127.0.0.1:$Port$p" -UseBasicParsing).StatusCode } catch {}
      Check "GET $p" ($code -eq 200) "HTTP $code"
    }
    $code = 0; try { Invoke-WebRequest "http://127.0.0.1:$Port/api/sync/ping" -UseBasicParsing | Out-Null } catch { $code = [int]$_.Exception.Response.StatusCode }
    Check "/api/sync/ping dezactivat în modul local" ($code -eq 404) "HTTP $code"
    Check "Baza SQLite creată" (Test-Path (Join-Path $data "cimitir.db")) $data
  }
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
$results | Format-Table -AutoSize
if ($results | Where-Object { $_.Rezultat -eq "FAIL" }) { Write-Host "UNELE VERIFICĂRI AU EȘUAT" -ForegroundColor Red; exit 1 }
Write-Host "TOATE VERIFICĂRILE AUTOMATE AU TRECUT. Continuă cu pașii manuali din WINDOWS_VERIFICARE.md" -ForegroundColor Green
