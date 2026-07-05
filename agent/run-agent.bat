@echo off
setlocal
cd /d "%~dp0"
python -m agent.main --server http://127.0.0.1:8000 --company-id 1 --name %COMPUTERNAME%
