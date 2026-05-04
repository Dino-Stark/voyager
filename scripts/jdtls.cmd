@echo off
rem Voyager JDT Language Server Launcher
rem Auto-generated launcher - see scripts/setup_jdtls.py for details

set "SCRIPT_DIR=%~dp0"
set "JDTLS_BIN=%SCRIPT_DIR%jdtls\bin"

python "%JDTLS_BIN%\jdtls" %*
