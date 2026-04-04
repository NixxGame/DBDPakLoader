@echo off
title DBDpakLoader
cd /d "%~dp0"

echo    Starting pak loader...
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.0+ and try again.
    pause
    exit /b
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set pyver=%%v
for /f "tokens=1 delims=." %%a in ("%pyver%") do set pymajor=%%a

if %pymajor% LSS 3 (
    echo [ERROR] Python version %pyver% detected.
    echo Python 3.0+ is required.
    pause
    exit /b
)

echo [OK] Python %pyver% detected.
echo.

if exist requirements.txt (
    echo Installing/updating requirements...
    python -m pip install --upgrade pip --quiet --disable-pip-version-check
    python -m pip install -r requirements.txt --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install requirements.
        pause
        exit /b
    )
    echo [OK] Requirements ready.
) else (
    echo [WARNING] requirements.txt not found, skipping install.
)

echo.
echo Launching loader...
echo.

start "" /b pythonw loader.py
exit