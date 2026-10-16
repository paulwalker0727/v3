# laptop_cloud_test.ps1 - comenzi Docker pentru laptopul Cloud TEST (D:\v3-main). NU atinge PROD. NU afiseaza secrete.
# Containere existente (nu se reconstruiesc toate): cimitir-cloud-test:8080, cimitir-cloud-mobile-v2-test:8081, cimitir-cloud-mobile-v2-next:8082, cimitir-cloud-mobile-v2-import:8083.
# Strategie sigura: 1) status  2) build cu TAG NOU  3) canary pe portul 8084 cu mediul copiat din containerul sursa  4) (dupa aprobarea migrarii 022 pe TEST) switch  5) rollback instant.
#   .\laptop_cloud_test.ps1 status
#   .\laptop_cloud_test.ps1 build   -Tag sync-v2-1
#   .\laptop_cloud_test.ps1 canary  -Tag sync-v2-1 [-Source cimitir-cloud-mobile-v2-next]
#   .\laptop_cloud_test.ps1 switch  -Tag sync-v2-1 -Target cimitir-cloud-mobile-v2-next     # abia DUPA migrarea 022 aprobata si aplicata pe TEST
#   .\laptop_cloud_test.ps1 rollback -Target cimitir-cloud-mobile-v2-next
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("status", "build", "canary", "switch", "rollback", "canary-stop")][string]$Action,
    [string]$Tag = "",
    [string]$Source = "cimitir-cloud-mobile-v2-next",
    [string]$Target = "cimitir-cloud-mobile-v2-next",
    [string]$RepoDir = "D:\v3-main",
    [int]$CanaryPort = 8084,
    [string]$Image = "cimitir-cloud"
)
$ErrorActionPreference = "Stop"
$Canary = "cimitir-cloud-sync-v2-canary"
$Protected = @("cimitir-cloud-test")   # containerul 8080 nu se inlocuieste de acest script

function Test-Url([string]$url, [int[]]$ok = @(200)) {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 8; return ($ok -contains [int]$r.StatusCode) }
    catch { if ($_.Exception.Response) { return ($ok -contains [int]$_.Exception.Response.StatusCode) } else { return $false } }
}
function Get-Inspect([string]$name) { (docker inspect $name | ConvertFrom-Json)[0] }
function Write-EnvFile([string]$from) {
    # mediul containerului sursa -> fisier temporar (doar utilizatorul curent); NU se afiseaza, se sterge dupa folosire
    $f = Join-Path $env:TEMP ("cimitir_env_" + [guid]::NewGuid().ToString("N") + ".env")
    (Get-Inspect $from).Config.Env | Where-Object { $_ -match "^[A-Za-z_][A-Za-z0-9_]*=" -and $_ -notmatch "^(PATH|HOME|HOSTNAME|PYTHON_|LANG|GPG_KEY)" } | Set-Content -Path $f -Encoding ascii
    icacls $f /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    return $f
}
function Assert-NotProtected([string]$name) {
    if ($Protected -contains $name) { throw "Containerul $name este protejat: nu este inlocuit de acest script." }
}

switch ($Action) {
    "status" {
        docker ps -a --filter "name=cimitir-cloud" --format "table {{.Names}}`t{{.Image}}`t{{.Status}}`t{{.Ports}}"
        foreach ($p in 8080, 8081, 8082, 8083, $CanaryPort) {
            "{0}: /healthz={1}  /api/v1/health={2}" -f $p, (Test-Url "http://127.0.0.1:$p/healthz"), (Test-Url "http://127.0.0.1:$p/api/v1/health")
        }
        "Imagini:"; docker images $Image --format "{{.Repository}}:{{.Tag}}  {{.CreatedSince}}"
    }
    "build" {
        if (-not $Tag) { throw "Cere -Tag (ex. sync-v2-1). Nu se foloseste 'latest'." }
        Set-Location $RepoDir
        git rev-parse --abbrev-ref HEAD
        git status --short | Select-Object -First 20
        docker build -t "${Image}:$Tag" .
        "Imagine construita: ${Image}:$Tag (containerele existente NU au fost atinse)."
    }
    "canary" {
        if (-not $Tag) { throw "Cere -Tag." }
        docker rm -f $Canary 2>$null | Out-Null
        $envf = Write-EnvFile $Source
        try {
            docker run -d --name $Canary --env-file $envf -p "127.0.0.1:${CanaryPort}:8080" --restart no --read-only --tmpfs /tmp "${Image}:$Tag" | Out-Null
        } finally { Remove-Item $envf -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 12
        $h = Test-Url "http://127.0.0.1:$CanaryPort/healthz"
        $r = Test-Url "http://127.0.0.1:$CanaryPort/api/v1/health"
        # ruta Sync v2 trebuie sa existe si sa refuze fara cheie (401), nu 404
        $s = Test-Url "http://127.0.0.1:$CanaryPort/api/v1/sync-v2/status" @(401, 403)
        "healthz=$h  health=$r  sync-v2 (fara cheie => 401/403)=$s"
        if (-not ($h -and $s)) { docker logs --tail 40 $Canary 2>&1 | ForEach-Object { $_ -replace "(postgres(ql)?://)[^@\s]+@", '$1***@' }; throw "Canary NEREUSIT. Containerele existente nu au fost atinse." }
        "Canary OK pe http://127.0.0.1:$CanaryPort . Containerele existente neatinse."
    }
    "canary-stop" { docker rm -f $Canary }
    "switch" {
        if (-not $Tag) { throw "Cere -Tag." }
        Assert-NotProtected $Target
        $old = Get-Inspect $Target
        $prev = "$Target-prev"
        $ports = @(); foreach ($k in $old.HostConfig.PortBindings.PSObject.Properties) { foreach ($b in $k.Value) { $ports += "-p"; $ports += ("{0}:{1}:{2}" -f $(if ($b.HostIp) { $b.HostIp } else { "0.0.0.0" }), $b.HostPort, ($k.Name -replace "/tcp", "")) } }
        $mounts = @(); foreach ($m in $old.Mounts) { $mounts += "-v"; $mounts += ("{0}:{1}" -f $m.Source, $m.Destination) }
        $restart = $old.HostConfig.RestartPolicy.Name; if (-not $restart) { $restart = "unless-stopped" } elseif ($restart -eq "no") { $restart = "no" }
        $envf = Write-EnvFile $Target
        try {
            docker rm -f $prev 2>$null | Out-Null
            docker stop $Target | Out-Null
            docker rename $Target $prev
            docker run -d --name $Target --env-file $envf @ports @mounts --restart $restart "${Image}:$Tag" | Out-Null
        } finally { Remove-Item $envf -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 12
        $port = ($old.HostConfig.PortBindings.PSObject.Properties | Select-Object -First 1).Value[0].HostPort
        if (Test-Url "http://127.0.0.1:$port/healthz") { "Switch OK: $Target ruleaza ${Image}:$Tag. Versiunea veche pastrata ca $prev (oprita) - rollback: .\laptop_cloud_test.ps1 rollback -Target $Target" }
        else {
            "NESANATOS - revin automat la versiunea veche."
            docker rm -f $Target | Out-Null; docker rename $prev $Target; docker start $Target | Out-Null
            throw "Switch esuat; $Target restaurat."
        }
    }
    "rollback" {
        Assert-NotProtected $Target
        $prev = "$Target-prev"
        if (-not (docker ps -a --format "{{.Names}}" | Select-String -SimpleMatch -Quiet $prev)) { throw "Nu exista $prev." }
        docker rm -f $Target 2>$null | Out-Null
        docker rename $prev $Target
        docker start $Target | Out-Null
        "Rollback facut: $Target = versiunea anterioara."
    }
}
