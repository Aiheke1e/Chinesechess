@echo off
cd /d "%~dp0"
py -3 control_panel.py
if errorlevel 1 pause
