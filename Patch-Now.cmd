@echo off
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0autopatch.py" %*
exit /b %errorlevel%
