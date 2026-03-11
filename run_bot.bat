@echo off
cd /d "%~dp0"
REM Запуск на Python 3.11 (3.14 несовместим с httpcore/telegram)
REM Важно: должен быть запущен только ОДИН экземпляр бота (иначе Conflict в Telegram).
echo Запуск бота (только один экземпляр)...
py -3.11 bot.py
pause
