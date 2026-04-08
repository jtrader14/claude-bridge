@echo off
echo === Claude Bridge ===
echo Starting proxy, bridge and listener...
echo.

start "Proxy" cmd /k "cd %~dp0 && python proxy.py"
timeout /t 2 /nobreak >nul

start "Bridge" cmd /k "cd %~dp0 && python bridge.py monitor"
timeout /t 2 /nobreak >nul

start "Listener" cmd /k "cd %~dp0 && python autolistener.py"

echo.
echo All started. Send "claude: your task" on Telegram.
