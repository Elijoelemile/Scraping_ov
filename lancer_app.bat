@echo off
rem Lance le comparateur de prix dans le navigateur (fermer cette fenêtre pour l'arrêter)
cd /d "%~dp0"
python -m streamlit run app.py
pause
