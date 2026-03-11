@echo off
cd /d "%~dp0"
REM Останавливаем предыдущий экземпляр бота (избегаем Conflict в Telegram)
if exist bot.pid (
  for /f "usebackq delims=" %%a in ("bot.pid") do taskkill /F /PID %%a 2>nul
  del bot.pid 2>nul
)
REM Запуск на Python 3.11 (3.14 несовместим с httpcore/telegram)
echo Запуск бота...
py -3.11 bot.py
pause
