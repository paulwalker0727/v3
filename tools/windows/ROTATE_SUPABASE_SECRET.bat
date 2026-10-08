@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Rotire secrete Supabase. Valoarea noua se introduce de la tastatura si NU se afiseaza.
echo   ROTATE_SUPABASE_SECRET.bat               = cheia secreta SUPABASE_SECRET_KEY
echo   ROTATE_SUPABASE_SECRET.bat --what db-password = parola bazei de date
echo Se testeaza inainte de salvare; daca testul pica, backend\.env ramane neschimbat.
echo.
cd /d "%ROOT%"
%PY% scripts\rotate_supabase_secrets.py %*
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = rotit, 1 = refuzat, 2 = eroare^)
pause
