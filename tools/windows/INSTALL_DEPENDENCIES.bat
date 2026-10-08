@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Instalez dependentele MAIN + portal in ACELASI Python folosit de launcher: %PY%
echo ^(nu se foloseste backend\requirements.txt; emergentintegrations nu e necesar pe Windows^)
echo.
cd /d "%ROOT%\desktop"
%PY% -m pip install --upgrade pip
findstr /v /b /c:"emergentintegrations" requirements.txt > "%TEMP%\cimitir-req.txt"
%PY% -m pip install -r "%TEMP%\cimitir-req.txt"
if errorlevel 1 (echo [EROARE] Instalarea din requirements.txt a esuat. & pause & exit /b 1)
%PY% -m pip install -r web_requirements.txt
if errorlevel 1 (echo [EROARE] Instalarea din web_requirements.txt a esuat. & pause & exit /b 1)
echo.
echo Verific dependentele portalului in acelasi interpreter...
%PY% start_local.py --check-deps
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = OK, 2 = lipsesc pachete^)
pause
