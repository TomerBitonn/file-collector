@echo off
title File Collector
cd /d "%~dp0"
py collect_files.py
echo.
echo File Collector has closed.
pause
