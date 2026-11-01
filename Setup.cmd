@echo off
rem Double-click to set up mcp-hub step by step (scripts\setup.ps1). Safe to run again.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
echo.
pause
