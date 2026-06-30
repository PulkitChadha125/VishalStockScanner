@echo off
setlocal EnableExtensions
cd /d "%~dp0"

:: ============================================================
::  Open inbound TCP port on Windows Firewall (VPS / Server)
::  Right-click this file -> Run as administrator
::
::  This trading app listens on port 5000 (see main.py).
::  Change PORT below if you use a different port.
:: ============================================================

set "PORT=5000"
set "RULE_NAME=Trading App TCP %PORT%"

echo ========================================
echo  Open firewall port %PORT%
echo ========================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Administrator rights required.
    echo Right-click open_port.bat and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo Removing old rule if it exists...
netsh advfirewall firewall delete rule name="%RULE_NAME%" >nul 2>&1

echo Adding inbound allow rule for TCP port %PORT%...
netsh advfirewall firewall add rule ^
    name="%RULE_NAME%" ^
    dir=in ^
    action=allow ^
    protocol=TCP ^
    localport=%PORT% ^
    profile=any

if errorlevel 1 (
    echo.
    echo FAILED to add firewall rule.
    pause
    exit /b 1
)

echo.
echo SUCCESS: Inbound TCP port %PORT% is open on this machine.
echo.
echo Local:  http://127.0.0.1:%PORT%
echo Remote: http://YOUR_VPS_PUBLIC_IP:%PORT%
echo.
echo Also ensure your VPS provider's security group / network
echo firewall allows TCP %PORT% if you use cloud hosting.
echo.
pause
exit /b 0
