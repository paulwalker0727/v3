@echo off
rem Instaleaza Centrul Cloud intr-o COPIE TEST. Nu modifica instalarea functionala (D:\v3-main).
setlocal
set "SRC=D:\v3-main"
set "DST=D:\v3-main-cloudcenter-test"
if "%CIMITIR_PYTHON%"=="" set "CIMITIR_PYTHON=python"
echo Sursa (doar citire): %SRC%
echo Copia TEST (nou):    %DST%
echo Se poate schimba editand acest fisier. Datele se detecteaza automat; sau adauga --live-data "cale".
pause
"%CIMITIR_PYTHON%" "%~dp0install_test_copy.py" --source "%SRC%" --target "%DST%" %*
pause
endlocal
