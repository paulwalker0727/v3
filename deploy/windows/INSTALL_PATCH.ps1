# INSTALL_PATCH.ps1 - instalare sigura a patch-ului Sync v2 pe PC MAIN (Windows) sau pe laptop. NU suprascrie modificari locale, NU face commit/push.
# Folosire (PowerShell, din folderul unde ai dezarhivat ZIP-ul):
#   .\INSTALL_PATCH.ps1 -Repo "C:\Users\xboxw\Desktop\v3-main" -DryRun     # doar verificari
#   .\INSTALL_PATCH.ps1 -Repo "C:\Users\xboxw\Desktop\v3-main"             # instalare
#   .\INSTALL_PATCH.ps1 -Repo "C:\Users\xboxw\Desktop\v3-main" -Rollback   # anulare (doar daca fisierele nu s-au schimbat dupa instalare)
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Branch = "sync-v2-emergent-wip",
    [string]$Base = "12a3c7ba83f680d10a4da457643d73cd5c484ddb",
    [string]$Patch = (Join-Path $PSScriptRoot "sync_v2.patch"),
    [switch]$DryRun,
    [switch]$Rollback
)
$ErrorActionPreference = "Stop"
$py = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue) }
if (-not $py) { Write-Error "Python nu a fost gasit (py/python)."; exit 2 }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Error "git nu a fost gasit."; exit 2 }
$installer = Join-Path $PSScriptRoot "install_patch.py"
if ($Rollback) {
    & $py.Source $installer --repo $Repo --rollback
    exit $LASTEXITCODE
}
if (-not (Test-Path $Patch)) { Write-Error "Lipseste fisierul patch: $Patch"; exit 2 }
$extra = @()
if ($DryRun) { $extra += "--dry-run" }
& $py.Source $installer --repo $Repo --patch $Patch --branch $Branch --base $Base @extra
exit $LASTEXITCODE
