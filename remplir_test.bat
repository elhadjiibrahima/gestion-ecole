@echo off
REM Cree une ecole de TEST complete dans la base de donnees (seul le compte directeur est demande).
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m app.cli remplir-test
pause
