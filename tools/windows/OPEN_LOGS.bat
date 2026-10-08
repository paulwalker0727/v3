@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
set "DATA_DIR="
cd /d "%ROOT%"
for /f "usebackq delims=" %%D in (`%PY% -c "import sys; sys.path.insert(0, r'%ROOT%\shared'); import cimitir_config as c; print(c.data_dir())"`) do set "DATA_DIR=%%D"
if not defined DATA_DIR (
  echo [EROARE] Nu am putut afla folderul de date. Verifica instalarea Python si fisierul .env.
  pause & exit /b 1
)
if not exist "%DATA_DIR%\logs" (
  echo Nu exista inca jurnale in "%DATA_DIR%\logs". Porneste mai intai serverul ^(START_SERVER.bat^).
  pause & exit /b 1
)
start "" explorer "%DATA_DIR%\logs"
