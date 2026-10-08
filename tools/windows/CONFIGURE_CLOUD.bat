@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
set "ENVF=%ROOT%\backend\.env"
if not exist "%ENVF%" (
  if exist "%ROOT%\backend\.env.example" (
    copy "%ROOT%\backend\.env.example" "%ENVF%" >nul
    echo Am creat backend\.env din backend\.env.example.
  ) else (
    echo [EROARE] Nu exista nici backend\.env, nici backend\.env.example.
    pause & exit /b 1
  )
)
echo Deschid backend\.env in Notepad. Completeaza sectiunea "CONFIGURARE CLOUD - SUPABASE", salveaza ^(Ctrl+S^) si inchide.
echo Fisierul NU este versionat in Git. Nu il trimite si nu il publica.
echo Dupa salvare ruleaza CHECK_CLOUD_CONFIG.bat, apoi RESTART_BACKEND.bat.
start "" notepad "%ENVF%"
