@echo off
rem Double-click to delete HASC. Windows asks to allow it to make changes: choose Yes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0delete.ps1" %*
