@echo off
rem Double-click to install HASC. Windows asks to allow it to make changes: choose Yes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
