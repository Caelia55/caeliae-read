@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\invoke-secure-tunnel.ps1" -Mode Run
