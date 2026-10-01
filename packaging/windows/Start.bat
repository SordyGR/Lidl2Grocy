@echo off
set HERE=%~dp0
"%HERE%python\python.exe" "%HERE%launcher.py" %*
if errorlevel 1 pause
