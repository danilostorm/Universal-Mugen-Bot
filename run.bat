@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 universal_mugen_bot.py
) else (
  python universal_mugen_bot.py
)
