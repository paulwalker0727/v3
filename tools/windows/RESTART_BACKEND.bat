@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Opresc serverul de pe portul %CIMITIR_PORT% ^(doar procese uvicorn pentru server:app^) si il pornesc din nou...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=%CIMITIR_PORT%; $c=Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; foreach($x in $c){ $w=Get-CimInstance Win32_Process -Filter ('ProcessId='+$x.OwningProcess); if($w.CommandLine -match 'uvicorn' -and $w.CommandLine -match 'server:app'){ Stop-Process -Id $x.OwningProcess -Force; Write-Host ('Oprit PID '+$x.OwningProcess) } else { Write-Host ('Portul este folosit de alt program (PID '+$x.OwningProcess+'); nu l-am oprit.') } }"
timeout /t 2 /nobreak >nul
start "Cimitir Server" "%~dp0START_SERVER.bat"
echo Serverul se porneste intr-o fereastra noua. Verifica apoi SERVER_STATUS.bat.
