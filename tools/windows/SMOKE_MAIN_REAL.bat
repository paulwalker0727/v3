@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Smoke-test REAL MAIN (launcher + portal 127.0.0.1:8000 + registre). Foloseste un folder NOU temporar; nu atinge datele reale.
echo Pentru testul offline: dezactiveaza Wi-Fi/Ethernet INAINTE, apoi ruleaza cu argumentul --offline.
echo Ferestrele MAIN se vad si se actioneaza singure; nu apasa nimic pana la final (~2-3 minute).
echo.
cd /d "%ROOT%"
%PY% -m pip install --quiet psutil
%PY% desktop\start_local.py --check-deps || (echo Ruleaza intai tools\windows\INSTALL_DEPENDENCIES.bat & pause & exit /b 2)
%PY% scripts\smoke_main_real\run.py %*
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = PASS, 1 = verificari esuate, 2 = folder de date nenul^)
pause
