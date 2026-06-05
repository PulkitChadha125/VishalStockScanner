@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  Vishal Trading Strategy - Launcher
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed or not in PATH.
    echo Install Python 3.10+ and try again.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing / updating dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Ensuring NumPy 1.x (compatible with older CPUs / Server 2012)...
pip install "numpy>=1.23.5,<2.0" "pandas>=1.5.0,<2.2" --force-reinstall
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo Starting application at http://127.0.0.1:5000
echo Opening browser...
start "" "http://127.0.0.1:5000"

python main.py

echo.
echo Application stopped.
pause
