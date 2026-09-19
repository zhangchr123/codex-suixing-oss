@echo off
cd /d "%~dp0"
py -3 desktop.py
if errorlevel 1 pause
