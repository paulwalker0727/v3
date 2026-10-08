@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Verific configurarea cloud ^(nu se afiseaza parole, chei sau connection string^)...
echo.
cd /d "%ROOT%"
%PY% scripts\check_cloud_config.py %*
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = totul ONLINE, 1 = lipseste ceva sau este OFFLINE^)
pause
