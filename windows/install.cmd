@echo off
rem Right-click, Run as administrator. Starts install.ps1 from this folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
