@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m caeliae_read.mcp.server
endlocal
