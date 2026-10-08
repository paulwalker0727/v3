@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Pornesc serverul Cimitir pe %BASE_URL%  ^(oprire: Ctrl+C sau inchide fereastra^)
echo Setarile ^(inclusiv baza de date^) se citesc din fisierul .env, nu din acest script.
cd /d "%ROOT%\backend"
%PY% -m uvicorn server:app --host 127.0.0.1 --port %CIMITIR_PORT% --no-proxy-headers
echo.
echo Serverul s-a oprit. Daca a aparut o eroare mai sus, vezi README_ADMIN_WINDOWS.md.
pause
