@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  set PY=python
)

%PY% -m pip install --upgrade pip pyinstaller
if errorlevel 1 goto :error

%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name UniversalMugenBot ^
  universal_mugen_bot.py
if errorlevel 1 goto :error

echo.
echo Pronto: dist\UniversalMugenBot.exe
pause
exit /b 0

:error
echo.
echo Falha ao gerar o executavel.
pause
exit /b 1
