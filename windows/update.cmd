@echo off
rem Double-click to update HASC. Windows asks to allow it to make changes: choose Yes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
