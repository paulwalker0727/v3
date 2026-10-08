@echo off
REM ==========================================================================
REM  Cimitir Desktop - construire .exe + instalator (Windows)
REM
REM  Rulare simpla:  dublu-click pe build_exe.bat
REM
REM  Ce produce:
REM    dist\Cimitir\Cimitir.exe                   - varianta folder (pornire instanta)
REM    dist\Cimitir-portabil.exe                  - un singur fisier portabil
REM    dist_installer\CimitirSetup-<ver>.exe      - instalator (necesita Inno Setup)
REM
REM  Semnare digitala (opțional) - inainte de rulare, in cmd:
REM    set CIMITIR_CERT=C:\cale\certificat.pfx
REM    set CIMITIR_CERT_PASS=parola
REM  sau certificat din magazia Windows / token USB:
REM    set CIMITIR_CERT_SUBJECT=Numele Firmei SRL
REM
REM  Detalii complete: vezi BUILD.md
REM ==========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================
echo   CIMITIR DESKTOP - CONSTRUIRE EXECUTABIL
echo ============================================
echo.

REM ---------- 1. Verifica Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [EROARE] Python nu este instalat sau nu este in PATH.
    echo          Descarca de la https://www.python.org/downloads/windows/
    echo          si bifeaza "Add Python to PATH" la instalare.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/6] Python !PYVER! gasit.

REM ---------- 2. Mediu virtual ----------
set PY=.venv-build\Scripts\python.exe
if not exist "!PY!" (
    echo [2/6] Creez mediul virtual .venv-build ...
    python -m venv .venv-build
    if errorlevel 1 (
        echo [EROARE] Nu am putut crea mediul virtual.
        pause
        exit /b 1
    )
) else (
    echo [2/6] Mediul virtual .venv-build exista deja.
)

REM ---------- 3. Dependinte ----------
echo [3/6] Instalez dependintele ^(prima rulare poate dura 3-5 minute^) ...
"!PY!" -m pip install --upgrade pip --quiet
"!PY!" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [EROARE] Instalarea din requirements.txt a esuat.
    pause
    exit /b 1
)
"!PY!" -m pip install -r requirements-build.txt --quiet
if errorlevel 1 (
    echo [EROARE] Instalarea PyInstaller a esuat.
    pause
    exit /b 1
)

REM ---------- 4. Curatare build vechi ----------
echo [4/6] Curat build-urile vechi ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dist_onefile rmdir /s /q dist_onefile
if exist dist_installer rmdir /s /q dist_installer

REM ---------- 5. PyInstaller ----------
echo [5/6] Construiesc varianta FOLDER ^(pornire instanta^) ...
set CIMITIR_ONEFILE=0
"!PY!" -m PyInstaller --noconfirm --clean cimitir.spec
if errorlevel 1 (
    echo [EROARE] Build-ul onedir a esuat. Citeste mesajele de mai sus.
    pause
    exit /b 1
)

echo       Construiesc varianta PORTABILA ^(un singur .exe^) ...
set CIMITIR_ONEFILE=1
"!PY!" -m PyInstaller --noconfirm --distpath dist_onefile cimitir.spec
set CIMITIR_ONEFILE=0
if errorlevel 1 (
    echo [ATENTIE] Build-ul portabil a esuat - continui doar cu varianta folder.
) else (
    if exist "dist_onefile\Cimitir.exe" move /y "dist_onefile\Cimitir.exe" "dist\Cimitir-portabil.exe" >nul
)
if exist dist_onefile rmdir /s /q dist_onefile

REM ---------- 5b. Semnare digitala ----------
call :find_signtool
call :sign_files "dist\Cimitir\Cimitir.exe" "dist\Cimitir-portabil.exe"

REM ---------- 6. Instalator Inno Setup ----------
echo [6/6] Construiesc instalatorul ...
call :read_version
call :find_iscc
if not defined ISCC (
    echo       [INFO] Inno Setup nu este instalat - sar peste instalator.
    echo              Descarca gratuit de la https://jrsoftware.org/isdl.php
    goto :done
)
"!ISCC!" /DMyAppVersion=!APPVER! "installer\cimitir_setup.iss"
if errorlevel 1 (
    echo [ATENTIE] Compilarea instalatorului a esuat.
) else (
    call :sign_files "dist_installer\CimitirSetup-!APPVER!.exe"
)

:done
echo.
echo ============================================
echo   GATA!
echo ============================================
if exist "dist\Cimitir\Cimitir.exe"  echo   Folder rapid : dist\Cimitir\Cimitir.exe
if exist "dist\Cimitir-portabil.exe" echo   Portabil     : dist\Cimitir-portabil.exe
if exist "dist_installer\CimitirSetup-!APPVER!.exe" echo   Instalator   : dist_installer\CimitirSetup-!APPVER!.exe
echo.
echo   Datele aplicatiei ^(baza de date, documente^) stau in:
echo     %%LOCALAPPDATA%%\Cimitir
echo   Licenta si cheia AI in:
echo     %%APPDATA%%\CimitirDesktop
echo.
pause
exit /b 0


REM ==========================================================================
REM  SUBRUTINE
REM ==========================================================================

:read_version
set APPVER=
for /f "tokens=2 delims==" %%a in ('findstr /b "APP_VERSION" security\config.py') do set APPVER=%%a
set APPVER=!APPVER: =!
set APPVER=!APPVER:"=!
if "!APPVER!"=="" set APPVER=1.0.0
exit /b 0

:find_signtool
set SIGNTOOL=
for /f "delims=" %%f in ('dir /b /s "%ProgramFiles(x86)%\Windows Kits\10\bin\*\x64\signtool.exe" 2^>nul') do set SIGNTOOL=%%f
if defined SIGNTOOL exit /b 0
where signtool >nul 2>nul
if not errorlevel 1 for /f "delims=" %%f in ('where signtool') do set SIGNTOOL=%%f
exit /b 0

:sign_files
if not defined SIGNTOOL goto :sign_no_tool
if defined CIMITIR_CERT goto :sign_pfx
if defined CIMITIR_CERT_SUBJECT goto :sign_store
echo       [INFO] Fara semnare digitala. Windows va afisa "Editor necunoscut"
echo              la prima instalare. Vezi BUILD.md pentru semnare.
exit /b 0

:sign_pfx
echo       Semnez cu certificatul !CIMITIR_CERT! ...
"!SIGNTOOL!" sign /fd SHA256 /f "!CIMITIR_CERT!" /p "!CIMITIR_CERT_PASS!" /tr http://timestamp.digicert.com /td SHA256 %*
if errorlevel 1 echo       [ATENTIE] Semnarea a esuat - fisierele ramin nesemnate.
exit /b 0

:sign_store
echo       Semnez cu certificatul "!CIMITIR_CERT_SUBJECT!" din magazia Windows ...
"!SIGNTOOL!" sign /fd SHA256 /n "!CIMITIR_CERT_SUBJECT!" /tr http://timestamp.digicert.com /td SHA256 %*
if errorlevel 1 echo       [ATENTIE] Semnarea a esuat - fisierele ramin nesemnate.
exit /b 0

:sign_no_tool
echo       [INFO] signtool.exe nu a fost gasit ^(necesita Windows SDK^) - fara semnare.
exit /b 0

:find_iscc
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if defined ISCC exit /b 0
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if defined ISCC exit /b 0
where iscc >nul 2>nul
if not errorlevel 1 for /f "delims=" %%f in ('where iscc') do set ISCC=%%f
exit /b 0
