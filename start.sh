#!/bin/sh
# One-command start for macOS / Linux:  ./start.sh
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — open it, paste your Supabase keys, then run ./start.sh again."
  exit 1
fi
python backend.py
