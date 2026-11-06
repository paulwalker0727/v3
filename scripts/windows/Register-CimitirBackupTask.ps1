<#
.SYNOPSIS
  Programează copia de rezervă automată Cimitir în Planificatorul de activități Windows (NETESTAT pe Windows real la livrare).
.DESCRIPTION
  Creează o activitate care rulează `Cimitir.exe --backup-run` (sau `python scripts\backup_scheduled.py`), zilnic la ora aleasă și,
  opțional, repetat la N ore. Copia se verifică și se jurnalizează; la eșec activitatea se termină cu cod 1 (vizibil în Task Scheduler)
  și MAIN afișează avertizarea. Activitatea NU restaurează nimic. Setările (folder, retenție) se țin în MAIN > Copii de rezervă.
.EXAMPLE
  .\Register-CimitirBackupTask.ps1 -Exe "C:\Program Files\Cimitir\Cimitir.exe" -DataDir "C:\CimitirData" -At 02:00
  .\Register-CimitirBackupTask.ps1 -Python "C:\Cimitir\.venv\Scripts\python.exe" -At 23:30 -RepeatHours 6 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Exe,
    [string]$Python,
    [string]$DataDir,
    [string]$At = "02:00",
    [int]$RepeatHours = 0,
    [string]$TaskName = "Cimitir - Copie de rezerva",
    [switch]$RunWhetherLoggedOn,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"

if ($At -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "-At trebuie să fie HH:mm (ex. 02:00)." }
if ($RepeatHours -lt 0 -or $RepeatHours -gt 24) { throw "-RepeatHours trebuie să fie între 0 și 24." }
if (-not $Exe -and -not $Python) { throw "Indică -Exe (Cimitir.exe instalat) sau -Python (mediul Python al depozitului)." }

if ($RunWhetherLoggedOn -and -not $DataDir) {
    throw "Pentru -RunWhetherLoggedOn trebuie specificat -DataDir explicit."
}
if ($DataDir) {
    $DataDir = [System.IO.Path]::GetFullPath($DataDir)
    $root = [System.IO.Path]::GetPathRoot($DataDir)
    if ($DataDir.Length -gt $root.Length) { $DataDir = $DataDir.TrimEnd([char]92) }
}
# Windows CommandLineToArgvW: backslash-ul final înainte de ghilimele trebuie dublat.
function Quote-WinArg([string]$v) {
    $tail = [regex]::Match($v, '(\\+)$')
    if ($tail.Success) { $v = $v + ('\' * $tail.Value.Length) }
    return '"' + $v + '"'
}
$extra = @()
if ($DataDir) { $extra += @("--data-dir", (Quote-WinArg $DataDir)) }
if ($Exe) {
    if (-not (Test-Path -LiteralPath $Exe)) { throw "Nu există: $Exe" }
    $program = $Exe
    $arguments = (@("--backup-run") + $extra) -join " "
} else {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Nu există: $Python" }
    $script = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "backup_scheduled.py"
    $program = $Python
    $arguments = (@((Quote-WinArg $script)) + $extra) -join " "
}

Write-Host "Activitate : $TaskName"
Write-Host "Comandă    : `"$program`" $arguments"
Write-Host "Programare : zilnic la $At" -NoNewline
if ($RepeatHours -gt 0) { Write-Host ", repetat la fiecare $RepeatHours ore (copia se face doar dacă ultima e mai veche decât intervalul din MAIN)" } else { Write-Host "" }
if ($RunWhetherLoggedOn) {
    Write-Warning "S4U rulează fără sesiune interactivă, dar poate să NU deblocheze cheile DPAPI, volumele de rețea sau fișierele EFS. Testează pe Windows înainte de PROD."
}
if ($DryRun) { Write-Host "[DryRun] Nu s-a creat nimic."; return }

$action = New-ScheduledTaskAction -Execute $program -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $At
if ($RepeatHours -gt 0) {
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Hours $RepeatHours) -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
}
# StartWhenAvailable: dacă PC-ul era oprit la ora programată, copia rulează la următoarea pornire.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
if ($RunWhetherLoggedOn) {
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Copie de rezervă Cimitir (verificată și jurnalizată; nu restaurează)." -Force | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Copie de rezervă Cimitir (verificată și jurnalizată; nu restaurează)." -Force | Out-Null
}
Write-Host "Activitatea a fost creată. Test imediat:  Start-ScheduledTask -TaskName `"$TaskName`"  apoi verifică MAIN > Copii de rezervă > Jurnal."
