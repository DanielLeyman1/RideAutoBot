@echo off
cd /d "%~dp0"
REM Запуск на Python 3.11 (3.14 несовместим с httpcore/telegram)
py -3.11 bot.py
pause
