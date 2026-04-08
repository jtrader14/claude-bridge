#!/bin/bash
echo "=== Claude Bridge ==="
echo "Starting proxy, bridge and listener..."

cd "$(dirname "$0")"

nohup python proxy.py > proxy.log 2>&1 &
echo "Proxy started (PID: $!)"
sleep 2

nohup python bridge.py monitor > bridge.log 2>&1 &
echo "Bridge started (PID: $!)"
sleep 2

nohup python autolistener.py > autolistener.log 2>&1 &
echo "Listener started (PID: $!)"

echo ""
echo "All running. Send 'claude: your task' on Telegram."
echo "Logs: proxy.log, bridge.log, autolistener.log"
