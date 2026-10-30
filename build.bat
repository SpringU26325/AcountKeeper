@echo off
:: Keep this file ASCII-only. cmd reads batch files in 512-character windows but
:: advances the file pointer by bytes, so non-ASCII text makes the two drift and
:: later lines get cut mid-character. Chinese output lives in release/build_release.py.
:: Usage: run .\build.bat in the PyCharm terminal, or double-click this file.
chcp 65001 >nul

echo ==========================================
echo   Building AccountKeeper
echo ==========================================

:: Version and build arguments are owned by Python; failures pass through unchanged.
"%~dp0.venv\Scripts\python.exe" -B -X utf8 "%~dp0release\build_release.py" %*
if not "%errorlevel%"=="0" exit /b %errorlevel%

echo ==========================================
echo   Done. AccountKeeper.exe and update_helper.exe are in release/_staging/
echo   Upload them to Gitee, copy the URL, then run release\sign_release.py
echo ==========================================
pause
