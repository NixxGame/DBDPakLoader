@echo off
title DBDpakLoader
cd /d "%~dp0"

echo    Starting pak loader...
echo.

:: ── Python check ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.10+ from https://python.org and try again.
    pause
    exit /b
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set pyver=%%v
for /f "tokens=1 delims=." %%a in ("%pyver%") do set pymajor=%%a

if %pymajor% LSS 3 (
    echo [ERROR] Python version %pyver% detected. Python 3.0+ is required.
    pause
    exit /b
)

echo [OK] Python %pyver% detected.
echo.

:: ── curl check ───────────────────────────────────────────────────────────────
curl --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] curl not found. Windows 10 or newer is required.
    pause
    exit /b
)

:: ── Ensure src\ folder exists ────────────────────────────────────────────────
if not exist "%~dp0src" mkdir "%~dp0src"

:: ── Ensure C:\pakConfig folders exist ────────────────────────────────────────
if not exist "C:\pakConfig"      mkdir "C:\pakConfig"
if not exist "C:\pakConfig\mods" mkdir "C:\pakConfig\mods"

:: ── Migration: move old flat files into src\ if they exist at root ───────────
if exist "%~dp0loader.py" (
    echo [MIGRATE] Old layout detected. Moving files into src\...
    if exist "%~dp0loader.py"        move /y "%~dp0loader.py"        "%~dp0src\loader.py"        >nul
    if exist "%~dp0version.txt"      move /y "%~dp0version.txt"      "%~dp0src\version.txt"      >nul
    if exist "%~dp0requirements.txt" move /y "%~dp0requirements.txt" "%~dp0src\requirements.txt" >nul
    if exist "%~dp0loader_config.json" del /f /q "%~dp0loader_config.json"
    if exist "%~dp0hwid.txt"           del /f /q "%~dp0hwid.txt"
    echo [OK] Migration complete.
    echo.
)

:: ── Download any missing src\ files from GitHub ───────────────────────────────
set "RAW=https://raw.githubusercontent.com/NixxGame/DBDPakLoader/main/src"
set "MISSING=0"

if not exist "%~dp0src\loader.py"        set MISSING=1
if not exist "%~dp0src\version.txt"      set MISSING=1
if not exist "%~dp0src\requirements.txt" set MISSING=1

if %MISSING%==1 (
    echo [SETUP] Downloading missing files from GitHub...
    echo.

    if not exist "%~dp0src\loader.py" (
        echo   Downloading loader.py...
        curl -fsSL "%RAW%/loader.py" -o "%~dp0src\loader.py"
        if errorlevel 1 ( echo [ERROR] Failed to download loader.py. & pause & exit /b )
    )

    if not exist "%~dp0src\version.txt" (
        echo   Downloading version.txt...
        curl -fsSL "%RAW%/version.txt" -o "%~dp0src\version.txt"
        if errorlevel 1 ( echo [ERROR] Failed to download version.txt. & pause & exit /b )
    )

    if not exist "%~dp0src\requirements.txt" (
        echo   Downloading requirements.txt...
        curl -fsSL "%RAW%/requirements.txt" -o "%~dp0src\requirements.txt"
        if errorlevel 1 ( echo [ERROR] Failed to download requirements.txt. & pause & exit /b )
    )

    echo.
    echo [OK] All files ready.
    echo.
)

:: ── Install / update Python requirements ─────────────────────────────────────
echo Installing/updating requirements...
python -m pip install --upgrade pip --quiet --disable-pip-version-check
python -m pip install -r "%~dp0src\requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install requirements.
    pause
    exit /b
)
echo [OK] Requirements ready.
echo.

:: ── Launch ───────────────────────────────────────────────────────────────────
echo Launching loader...
echo.

start "" /b pythonw "%~dp0src\loader.py"
exit
