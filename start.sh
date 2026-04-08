#!/bin/bash
echo "=== Claude Bridge ==="
echo "Iniciando proxy, bridge y listener..."

cd "$(dirname "$0")"

nohup python proxy.py > proxy.log 2>&1 &
echo "Proxy iniciado (PID: $!)"
sleep 2

nohup python bridge.py monitor > bridge.log 2>&1 &
echo "Bridge iniciado (PID: $!)"
sleep 2

nohup python autolistener.py > autolistener.log 2>&1 &
echo "Listener iniciado (PID: $!)"

echo ""
echo "Todo corriendo. Envia 'claude: tu tarea' en Telegram."
echo "Logs: proxy.log, bridge.log, autolistener.log"
