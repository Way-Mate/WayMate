@echo off
REM One-command start for Windows: double-click or run start.bat
cd /d "%~dp0"
if not exist venv python -m venv venv
call venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - open it, paste your Supabase keys, then run start.bat again.
  pause
  exit /b 1
)
python backend.py
