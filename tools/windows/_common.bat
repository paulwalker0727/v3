@echo off
rem Fisier ajutator, folosit de celelalte scurtaturi. Nu se ruleaza singur. NU contine parole.
rem ROOT = radacina repo-ului (doua foldere mai sus de tools\windows).
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"

rem Python: mai intai mediul virtual .venv, apoi "python", apoi "py -3".
set "PY="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (
  echo [EROARE] Python nu a fost gasit. Instaleaza Python 3.11 sau 3.12 ^(bifeaza "Add python.exe to PATH"^).
  exit /b 1
)

rem Port: variabila CIMITIR_PORT, apoi linia CIMITIR_PORT= din .env, apoi 8000.
if not defined CIMITIR_PORT (
  for %%F in ("%ROOT%\.env" "%ROOT%\backend\.env") do (
    if exist %%F for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"CIMITIR_PORT=" %%F`) do if not defined CIMITIR_PORT set "CIMITIR_PORT=%%B"
  )
)
if not defined CIMITIR_PORT set "CIMITIR_PORT=8000"
set "BASE_URL=http://127.0.0.1:%CIMITIR_PORT%"
exit /b 0
