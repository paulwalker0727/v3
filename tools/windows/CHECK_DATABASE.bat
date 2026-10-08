@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Verific conexiunea PostgreSQL ^(doar citire, nu modifica nimic, nu afiseaza parola^)...
echo.
cd /d "%ROOT%"
%PY% scripts\check_postgres.py
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = OK, 1 = eroare de configurare/conexiune, 2 = schema incompleta^)
pause
