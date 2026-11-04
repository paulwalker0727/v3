<#
.SYNOPSIS
  Elimină activitatea programată de copie de rezervă. Nu șterge nicio copie și nu atinge datele.
#>
[CmdletBinding()]
param([string]$TaskName = "Cimitir - Copie de rezerva")
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Activitatea '$TaskName' a fost eliminată. Copiile existente au rămas pe disc."
} else {
    Write-Host "Activitatea '$TaskName' nu există."
}
