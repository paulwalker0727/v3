@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
echo Creare PRIMUL administrator ^(doar daca nu exista niciun administrator in baza de date^).
echo Numele si parola se introduc de la tastatura; parola nu se afiseaza si nu se salveaza nicaieri.
echo Contul va trebui sa-si schimbe parola la primul login.
echo.
cd /d "%ROOT%"
%PY% scripts\create_first_admin.py %*
echo.
echo Cod de iesire: %ERRORLEVEL%  ^(0 = creat, 1 = exista deja un administrator, 2 = eroare^)
pause
