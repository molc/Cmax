@echo off
chcp 65001 >/dev/null 2>&1
title CronosPre Tester
echo Starting...
cd /d "%~dp0"
"C:\Usersdmin\AppData\Local\Programs\Python\Python312\python.exe" main.py
pause
