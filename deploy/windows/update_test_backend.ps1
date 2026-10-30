# update_test_backend.ps1 - actualizare CONTROLATA a backendului Cloud TEST (ex. cel de pe 8086) la versiunea noua, fara pierderea datelor,
# fara resetarea pairingului si fara recrearea organizatiilor. Datele stau in baza TEST (Supabase), nu in container: containerul se inlocuieste, baza nu se atinge.
# NU atinge PROD. NU afiseaza secrete. Nu face nimic distructiv fara -Confirm. Containerul vechi se pastreaza (oprit, redenumit) pentru rollback instant.
#
# Ordinea recomandata (fiecare pas e reversibil pana la "switch"):
#   $env:CIMITIR_MAIN_KEY = "<cheia MAIN din SecretStore>"        # doar in aceasta sesiune PowerShell; nu se afiseaza
#   .\update_test_backend.ps1 inspect                              # ce ruleaza acum pe 8086/8087 + identitate/release (doar citire)
#   .\update_test_backend.ps1 build   -Tag mobile-perm-1           # imagine noua cu TAG; containerele existente nu se ating
#   .\update_test_backend.ps1 canary  -Old <nume-container-8086> -Tag mobile-perm-1      # porneste pe 8088 cu ACELASI mediu (aceeasi baza)
#   .\update_test_backend.ps1 verify  -OldPort 8086                # amprenta bazei trebuie sa fie IDENTICA; release diferit = actualizare sigura
#   .\update_test_backend.ps1 switch  -Old <nume-container-8086> -OldPort 8086 -Tag mobile-perm-1 -Confirm
#   .\update_test_backend.ps1 rollback -Old <nume-container-8086> -OldPort 8086        # revenire la containerul vechi
#   .\update_test_backend.ps1 canary-stop
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("inspect", "build", "canary", "verify", "switch", "rollback", "canary-stop")][string]$Action,
    [string]$Tag = "",
    [string]$Old = "",
    [int]$OldPort = 8086,
    [int]$CanaryPort = 8088,
    [string]$BindHost = "127.0.0.1",
    [string]$RepoDir = ".",
    [string]$Image = "cimitir-cloud",
    [switch]$Confirm
)
$ErrorActionPreference = "Stop"
$Canary = "cimitir-cloud-canary"

function Test-Url([string]$url) {
    try { return ([int](Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 8).StatusCode) -eq 200 } catch { return $false }
}
function Get-Inspect([string]$name) { (docker inspect $name | ConvertFrom-Json)[0] }
function Write-EnvFile([string]$from) {
    $f = Join-Path $env:TEMP ("cimitir_env_" + [guid]::NewGuid().ToString("N") + ".env")
    (Get-Inspect $from).Config.Env | Where-Object { $_ -match "^[A-Za-z_][A-Za-z0-9_]*=" -and $_ -notmatch "^(PATH|HOME|HOSTNAME|PYTHON_|LANG|GPG_KEY)" } | Set-Content -Path $f -Encoding ascii
    icacls $f /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    return $f
}
function Wait-Healthy([int]$port, [int]$seconds = 60) {
    for ($i = 0; $i -lt $seconds; $i += 2) { if (Test-Url "http://127.0.0.1:$port/api/v1/health") { return $true }; Start-Sleep -Seconds 2 }
    return $false
}
function Need([string]$value, [string]$what) { if (-not $value) { throw "Lipseste $what." } }
function Run-Verify([int[]]$ports) {
    if (-not $env:CIMITIR_MAIN_KEY) { throw "Seteaza `$env:CIMITIR_MAIN_KEY (cheia MAIN) in aceasta sesiune; nu se afiseaza." }
    $verifyArgs = @(); foreach ($p in $ports) { $verifyArgs += @("--url", "http://127.0.0.1:$p") }
    python (Join-Path $RepoDir "scripts\verify_backend_instance.py") @verifyArgs
    return $LASTEXITCODE
}

switch ($Action) {
    "inspect" {
        docker ps -a --format "table {{.Names}}`t{{.Image}}`t{{.Status}}`t{{.Ports}}"
        foreach ($p in 8086, 8087, $CanaryPort) { "{0}: /healthz={1}  /api/v1/health={2}" -f $p, (Test-Url "http://127.0.0.1:$p/healthz"), (Test-Url "http://127.0.0.1:$p/api/v1/health") }
        if ($env:CIMITIR_MAIN_KEY) { [void](Run-Verify @(8086, 8087)) } else { "Pentru identitatea instantelor seteaza `$env:CIMITIR_MAIN_KEY." }
    }
    "build" {
        Need $Tag "-Tag (ex. mobile-perm-1; nu se foloseste 'latest')"
        if ($Tag -eq "latest") { throw "Nu folosi 'latest'." }
        Set-Location $RepoDir
        git rev-parse --short HEAD
        docker build -t "${Image}:$Tag" .
        "Imagine construita: ${Image}:$Tag. Containerele existente NU au fost atinse."
    }
    "canary" {
        Need $Tag "-Tag"; Need $Old "-Old (numele containerului vechi, sursa mediului)"
        docker rm -f $Canary 2>$null | Out-Null
        $envf = Write-EnvFile $Old
        try { docker run -d --name $Canary --env-file $envf -p "127.0.0.1:${CanaryPort}:8080" --restart no --read-only --tmpfs /tmp "${Image}:$Tag" | Out-Null }
        finally { Remove-Item $envf -Force -ErrorAction SilentlyContinue }
        if (Wait-Healthy $CanaryPort) { "Canary sanatos pe $CanaryPort (aceeasi baza ca $Old). Ruleaza: verify." } else { docker logs --tail 40 $Canary; throw "Canary nu a devenit sanatos; $Old nu a fost atins." }
    }
    "verify" {
        $rc = Run-Verify @($OldPort, $CanaryPort)
        if ($rc -ne 0) { throw "Verificarea a esuat (cod $rc). NU face switch." } else { "Verificare OK: aceeasi baza, backend nou compatibil." }
    }
    "switch" {
        Need $Tag "-Tag"; Need $Old "-Old"
        if (-not $Confirm) { throw "Switch cere -Confirm explicit. Nimic nu a fost modificat." }
        if ((Run-Verify @($OldPort, $CanaryPort)) -ne 0) { throw "Verificarea identitatii a esuat: nu se face switch." }
        $info = Get-Inspect $Old
        $restart = $info.HostConfig.RestartPolicy.Name; if (-not $restart) { $restart = "unless-stopped" }
        $envf = Write-EnvFile $Old
        $prev = "$Old-previous-" + (Get-Date -Format "yyyyMMddHHmmss")
        try {
            docker stop $Old | Out-Null
            docker rename $Old $prev
            docker run -d --name $Old --env-file $envf -p "${BindHost}:${OldPort}:8080" --restart $restart --read-only --tmpfs /tmp "${Image}:$Tag" | Out-Null
            if (-not (Wait-Healthy $OldPort)) { throw "Noul backend nu a devenit sanatos." }
            if ((Run-Verify @($OldPort)) -ne 0) { throw "Noul backend nu trece verificarea de identitate." }
            "Actualizat: $Old ruleaza ${Image}:$Tag pe $OldPort. Vechiul container: $prev (oprit, pastrat pentru rollback)."
        } catch {
            "ESEC ($_). Revenire automata la containerul vechi..."
            docker rm -f $Old 2>$null | Out-Null
            docker rename $prev $Old
            docker start $Old | Out-Null
            throw
        } finally { Remove-Item $envf -Force -ErrorAction SilentlyContinue }
    }
    "rollback" {
        Need $Old "-Old"
        $prev = (docker ps -a --filter "name=^$Old-previous-" --format "{{.Names}}" | Sort-Object -Descending | Select-Object -First 1)
        Need $prev "un container '$Old-previous-*' de restaurat"
        docker rm -f $Old 2>$null | Out-Null
        docker rename $prev $Old
        docker start $Old | Out-Null
        if (Wait-Healthy $OldPort) { "Rollback facut: $Old (versiunea veche) ruleaza pe $OldPort." } else { throw "Containerul restaurat nu raspunde; verifica docker logs $Old." }
    }
    "canary-stop" { docker rm -f $Canary 2>$null | Out-Null; "Canary oprit si sters." }
}
