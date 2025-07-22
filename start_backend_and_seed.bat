@echo off
REM Windows batch script to seed dummy data and start the backend
poetry run python backend/scripts/seed_dummy_data.py
poetry run uvicorn backend.main:app --reload
