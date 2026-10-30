# Cimitir Cloud TEST only. Nu modifică baza de date; înlocuiește exclusiv containerul indicat.
# PowerShell 5.1+; validare fără secrete tipărite. Nu folosi pentru PROD.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true,Position=0)][ValidateSet('inspect','build','canary','verify','switch','rollback','canary-stop')][string]$Action,
    [string]$Tag = '', [string]$Old = 'cimitir-cloud-test-8086',
    [int]$OldPort = 8086, [int]$CanaryPort = 8088,
    [string]$RepoDir = '.', [string]$Image = 'cimitir-cloud',
    [switch]$Confirm, [switch]$AllowLegacyWithoutKey
)
$ErrorActionPreference = 'Stop'
$Canary = 'cimitir-cloud-canary'
$RepoDir = (Resolve-Path -LiteralPath $RepoDir).Path
function Assert-Docker($ArgsList) {
    & docker @ArgsList
    if ($LASTEXITCODE -ne 0) { throw "Docker a eșuat (cod $LASTEXITCODE): $($ArgsList[0])" }
}
function Inspect-Container([string]$name) {
    $raw = & docker inspect --type container $name 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Containerul '$name' nu există." }
    return ($raw | ConvertFrom-Json)[0]
}
function Exists-Container([string]$name) {
    & docker container inspect $name *> $null
    return ($LASTEXITCODE -eq 0)
}
function Status([int]$port,[string]$path) {
    try { return [int](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port$path" -TimeoutSec 8).StatusCode }
    catch { if ($_.Exception.Response) {return [int]$_.Exception.Response.StatusCode}; return 0 }
}
function Wait-Healthy([int]$port) {
    for($i=0;$i -lt 30;$i++) {if ((Status $port '/api/v1/health') -eq 200) {return $true}; Start-Sleep -Seconds 2}
    return $false
}
function Get-Environment([string]$name) {
    $x = Inspect-Container $name
    $map = @{}
    foreach($item in $x.Config.Env) {
        if ($item -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {$map[$Matches[1]] = $Matches[2]}
    }
    return $map
}
function Fingerprint([string]$name) {
    $m = Get-Environment $name
    # Valorile sunt comparate exclusiv local și nu sunt tipărite.
    foreach($k in @('CIMITIR_CP_DATABASE_URL','SUPABASE_URL','SUPABASE_BUCKET','CIMITIR_CLOUD_PROJECT_REF')) {
        if (-not $m.ContainsKey($k) -or -not $m[$k]) {throw "Lipsește configurarea '$k' în $name."}
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $s = (@('CIMITIR_CP_DATABASE_URL','SUPABASE_URL','SUPABASE_BUCKET','CIMITIR_CLOUD_PROJECT_REF') | ForEach-Object { "$($_)=$($m[$_])" }) -join "`n"
        return [Convert]::ToBase64String($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($s)))
    } finally {$sha.Dispose()}
}
function Assert-SecureSource([string]$name) {
    $x = Inspect-Container $name
    if (-not $x.HostConfig.ReadonlyRootfs) {throw 'Containerul vechi nu este read-only; verifică manual înainte de migrare.'}
    if ($x.HostConfig.NetworkMode -ne 'bridge') {throw 'Rețeaua nu este bridge; scriptul nu copiază configurații de rețea personalizate.'}
    if (@($x.Mounts).Count -gt 0) {throw 'Containerul vechi are volume; scriptul nu le copiază.'}
    if (@($x.HostConfig.CapDrop) -notcontains 'ALL') {throw 'Containerul vechi nu are CapDrop ALL.'}
    if (@($x.HostConfig.SecurityOpt) -notcontains 'no-new-privileges') {throw 'Containerul vechi nu are no-new-privileges.'}
}
function Make-EnvFile([string]$name) {
    $f = Join-Path $env:TEMP ('cimitir_env_' + [guid]::NewGuid().ToString('N') + '.env')
    try {
        $data = (Inspect-Container $name).Config.Env | Where-Object {$_ -match '^[A-Za-z_][A-Za-z0-9_]*=' -and $_ -notmatch '^(PATH|HOME|HOSTNAME|PYTHON_|LANG|GPG_KEY)='}
        # Fără UTF-8 BOM pe Windows PowerShell 5.1.
        [System.IO.File]::WriteAllLines($f, [string[]]$data, (New-Object System.Text.UTF8Encoding($false)))
        & icacls $f '/inheritance:r' '/grant:r' "${env:USERNAME}:(F)" *> $null
        if($LASTEXITCODE -ne 0){throw 'Nu pot restricționa permisiunile fișierului temporar.'}
        return $f
    } catch {Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue; throw}
}
function Assert-Verified() {
    if (-not (Exists-Container $Old)) {throw "Nu există $Old."}
    if (-not (Exists-Container $Canary)) {throw "Nu există canary $Canary."}
    Assert-SecureSource $Old
    Assert-SecureSource $Canary
    if ((Inspect-Container $Canary).Config.Image -ne "${Image}:$Tag" -and $Tag) {throw 'Imaginea canary diferă de imaginea solicitată.'}
    if (-not (Inspect-Container $Old).State.Running -or -not (Inspect-Container $Canary).State.Running) {throw 'Unul din containere este oprit.'}
    if ((Fingerprint $Old) -ne (Fingerprint $Canary)) {throw 'Configurațiile DB/Project/Storage diferă! STOP.'}
    foreach($p in @($OldPort,$CanaryPort)) {
        if ((Status $p '/api/v1/health') -ne 200) {throw "Backendul $p nu este sănătos."}
    }
    $new = Status $CanaryPort '/api/v1/sync-v2/instance'
    if ($new -ne 401 -and $new -ne 403) {throw "Endpointul nou /instance pe $CanaryPort a returnat HTTP $new (așteptat 401/403 fără autentificare)."}
    $oldstatus = Status $OldPort '/api/v1/sync-v2/instance'
    if ($oldstatus -eq 404) {
        if (-not $AllowLegacyWithoutKey) {throw 'Backend vechi fără /instance: pentru verificarea limitată a mediului existent folosește explicit -AllowLegacyWithoutKey. Nu este echivalent cu verificarea autentificată.'}
        Write-Warning 'Upgrade LEGACY: am confirmat sănătatea + egalitatea configurațiilor DB/Project/Storage, NU identitatea autentificată a legăturii MAIN.'
    } elseif ($oldstatus -ne 401 -and $oldstatus -ne 403) {throw "HTTP neașteptat $oldstatus pe $OldPort."}
    if ($env:CIMITIR_MAIN_KEY -and $oldstatus -ne 404) {
        & python (Join-Path $RepoDir 'scripts\verify_backend_instance.py') --url "http://127.0.0.1:$OldPort" --url "http://127.0.0.1:$CanaryPort"
        if ($LASTEXITCODE -ne 0) {throw 'Verificarea autentificată nu a trecut.'}
    } elseif ($env:CIMITIR_MAIN_KEY) {
        & python (Join-Path $RepoDir 'scripts\verify_backend_instance.py') --url "http://127.0.0.1:$CanaryPort"
        if ($LASTEXITCODE -ne 0) {throw 'Cheia MAIN nu a fost acceptată de backendul nou.'}
    }
    Write-Host 'Verificare preliminară OK; fără operațiuni de scriere în baza TEST.'
}
function Start-New([string]$name,[string]$envf,[int]$port,[string]$restart) {
    Assert-Docker @('run','-d','--name',$name,'--env-file',$envf,'--network','bridge','-p',"127.0.0.1:${port}:8080",'--restart',$restart,'--read-only','--tmpfs','/tmp','--cap-drop','ALL','--security-opt','no-new-privileges',"${Image}:$Tag") | Out-Null
}
switch($Action) {
    inspect {
        Assert-Docker @('ps','-a','--format','table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}')
        foreach($p in @($OldPort,8087,$CanaryPort)) {Write-Host "${p}: health=$(Status $p '/api/v1/health') instance=$(Status $p '/api/v1/sync-v2/instance')"}
    }
    build {
        if (-not $Tag -or $Tag -eq 'latest') {throw 'Specifică -Tag, diferit de latest.'}
        Push-Location $RepoDir
        try { Assert-Docker @('build','-t',"${Image}:$Tag",'.') } finally {Pop-Location}
    }
    canary {
        if(-not $Tag){throw 'Specifică -Tag.'}
        Assert-SecureSource $Old
        if (Exists-Container $Canary) {throw "Canary $Canary există deja. Nu îl șterg automat."}
        $f = Make-EnvFile $Old
        try {Start-New $Canary $f $CanaryPort 'no'} finally {Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue}
        if (-not (Wait-Healthy $CanaryPort)) {throw 'Canary a pornit dar health a eșuat. Verifică docker logs cimitir-cloud-canary.'}
        Write-Host 'Canary sănătos.'
    }
    verify {Assert-Verified}
    switch {
        if (-not $Confirm) {throw 'Switch necesită -Confirm.'}
        if(-not $Tag){throw 'Specifică -Tag.'}
        Assert-Verified
        $oldInfo = Inspect-Container $Old
        if ($oldInfo.HostConfig.RestartPolicy.Name) {$restart = $oldInfo.HostConfig.RestartPolicy.Name} else {$restart = 'unless-stopped'}
        $prev = "$Old-previous-$(Get-Date -Format yyyyMMddHHmmss)"
        if (Exists-Container $prev) {throw 'Există deja container cu numele backupului.'}
        $f = Make-EnvFile $Old
        $renamed = $false
        try {
            Assert-Docker @('stop',$Old) | Out-Null
            Assert-Docker @('rename',$Old,$prev) | Out-Null
            $renamed = $true
            Start-New $Old $f $OldPort $restart
            if (-not (Wait-Healthy $OldPort)) {throw 'Noul backend nu a devenit sănătos.'}
            if ($env:CIMITIR_MAIN_KEY) {
                & python (Join-Path $RepoDir 'scripts\verify_backend_instance.py') --url "http://127.0.0.1:$OldPort"
                if($LASTEXITCODE -ne 0){throw 'Verificarea autentificată după switch a eșuat.'}
            } else {
                if ((Status $OldPort '/api/v1/sync-v2/instance') -notin @(401,403)) {throw 'Ruta nouă nu este protejată/a dispărut după switch.'}
                Write-Warning 'Fără cheie MAIN, verificarea pairingului autenticat rămâne de făcut în MAIN.'
            }
            Write-Host "Switch reușit. Backup păstrat: $prev"
        } catch {
            $failure = $_
            Write-Warning "Switch eșuat: $failure. Încerc restaurarea containerului vechi."
            if ($renamed) {
                if(Exists-Container $Old){Assert-Docker @('rm','-f',$Old) | Out-Null}
                Assert-Docker @('rename',$prev,$Old) | Out-Null
                Assert-Docker @('start',$Old) | Out-Null
            } elseif (Exists-Container $Old) {
                Assert-Docker @('start',$Old) | Out-Null
            }
            throw $failure
        } finally {Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue}
    }
    rollback {
        if (-not $Confirm) {throw 'Rollback necesită -Confirm.'}
        $matches = @(& docker ps -a --format '{{.Names}}' | Where-Object {$_ -like "$Old-previous-*"})
        if($matches.Count -ne 1){throw "Sunt $($matches.Count) backupuri. Nu aleg automat; verifică manual."}
        $prev = $matches[0]
        if (-not (Exists-Container $Old)) {throw 'Containerul curent lipsește; restaurarea trebuie efectuată manual.'}
        $currentBackup = "$Old-failed-$(Get-Date -Format yyyyMMddHHmmss)"
        Assert-Docker @('stop',$Old) | Out-Null
        Assert-Docker @('rename',$Old,$currentBackup) | Out-Null
        try {
            Assert-Docker @('rename',$prev,$Old) | Out-Null
            Assert-Docker @('start',$Old) | Out-Null
            if(-not(Wait-Healthy $OldPort)){throw 'Containerul restaurat nu este sănătos.'}
            Write-Host "Rollback reușit. Noul container a fost păstrat ca $currentBackup."
        } catch {Write-Warning "Rollback incomplet; containerele au fost păstrate. Verifică docker ps -a.";throw}
    }
    'canary-stop' {
        if (-not $Confirm) {throw 'Ștergerea canary necesită -Confirm.'}
        if (-not (Exists-Container $Canary)) {Write-Host 'Nu există canary.';break}
        Assert-Docker @('rm','-f',$Canary) | Out-Null
        Write-Host 'Doar containerul canary a fost șters.'
    }
}
