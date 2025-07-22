#!/bin/bash
# Windows users: run this script with Git Bash or use the .bat version
poetry run python backend/scripts/seed_dummy_data.py
poetry run uvicorn backend.main:app --reload
