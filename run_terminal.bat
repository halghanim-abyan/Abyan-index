@echo off
title Terminal v1.0
cd /d "%~dp0"
start "" http://localhost:8504
python -m streamlit run main_app.py --server.port 8504
