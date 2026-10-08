@echo off
setlocal
call "%~dp0_common.bat" || (pause & exit /b 1)
start "" "%BASE_URL%/admin"
