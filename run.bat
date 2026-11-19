@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m uvicorn server.main:app --port 8000 --reload
