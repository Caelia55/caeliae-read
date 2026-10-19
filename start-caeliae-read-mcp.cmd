@echo off
setlocal
cd /d "%~dp0"
set "CAELIAE_READ_DATA_ROOT=%~dp0data"
"%~dp0.venv\Scripts\python.exe" -m caeliae_read.mcp.server
endlocal
