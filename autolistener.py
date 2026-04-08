"""
Claude Bridge Auto-Listener
=============================
Long-polls el proxy continuamente. Cuando llega una tarea,
la ejecuta via Claude Code CLI y envia la respuesta a Telegram.

Uso:
  python autolistener.py          # Iniciar listener
  python autolistener.py --test   # Probar con mensaje de prueba
"""

import json
import os
import sys
import time
import subprocess
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# === CONFIGURACION (desde .env) ===
PROXY_URL = f"http://localhost:{os.getenv('PROXY_PORT', '5055')}"
POLL_TIMEOUT = 90

TELEGRAM_BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Claude Code CLI
# Windows: buscar con "where claude" → usar la ruta .cmd
# Linux/Mac: normalmente solo "claude"
CLAUDE_CMD = os.getenv("CLAUDE_CMD", "claude")
CLAUDE_TIMEOUT = 300  # 5 min max por tarea

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autolistener.log")

# Verificar configuracion
if not TELEGRAM_BOT or not TELEGRAM_CHAT:
    print("ERROR: Configurar TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
    print("Ver .env.example para referencia")
    sys.exit(1)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except:
        pass


def send_telegram(text, parse_mode="HTML"):
    """Enviar respuesta a Telegram."""
    try:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": parse_mode},
                timeout=10,
            )
            if not resp.ok:
                # Reintentar sin parse_mode si falla el HTML
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT, "text": chunk},
                    timeout=10,
                )
    except Exception as e:
        log(f"Telegram error: {e}")


def mark_done(msg_id, result):
    """Marcar tarea como completada en el proxy."""
    try:
        requests.post(f"{PROXY_URL}/done/{msg_id}", json={"result": result[:500]}, timeout=10)
    except Exception as e:
        log(f"Error marcando done: {e}")


def execute_with_claude(prompt):
    """Ejecutar prompt via Claude Code CLI y retornar la respuesta."""
    log(f"Ejecutando via Claude CLI: {prompt[:100]}")

    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", "--output-format", "text", prompt],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=os.path.expanduser("~"),
            encoding='utf-8',
            errors='replace',
        )

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode != 0 and not output:
            return f"Error (exit {result.returncode}): {error[:500]}"

        if not output and error:
            return f"Sin output. Stderr: {error[:500]}"

        return output if output else "Tarea completada (sin output)."

    except subprocess.TimeoutExpired:
        return "Timeout: la tarea tardo mas de 5 minutos."
    except Exception as e:
        return f"Error de ejecucion: {str(e)}"


def poll_and_execute():
    """Un ciclo: esperar mensaje, ejecutar, responder."""
    try:
        resp = requests.get(
            f"{PROXY_URL}/wait",
            params={"timeout": POLL_TIMEOUT},
            timeout=POLL_TIMEOUT + 10,
        )

        if resp.status_code == 204:
            return False

        if resp.status_code != 200:
            log(f"Proxy retorno {resp.status_code}")
            return False

        msg = resp.json()
        if msg.get("timeout"):
            return False

        msg_id = msg.get("id", "unknown")
        prompt = msg.get("prompt", "")
        source = msg.get("source", "unknown")

        if not prompt:
            log("Prompt vacio, saltando")
            mark_done(msg_id, "saltado - prompt vacio")
            return True

        log(f">>> Tarea de {source}: {prompt[:120]}")

        # Notificar que estamos trabajando
        send_telegram(f"<b>Procesando...</b>\n\n<i>{prompt[:200]}</i>")

        # Ejecutar via Claude Code
        response = execute_with_claude(prompt)

        # Enviar resultado a Telegram
        send_telegram(f"<b>Claude</b>\n\n{response}")

        # Marcar como completado en el proxy
        summary = response[:200] if len(response) <= 200 else response[:197] + "..."
        mark_done(msg_id, summary)

        log(f"<<< Listo: {summary[:100]}")
        return True

    except requests.exceptions.Timeout:
        return False
    except requests.exceptions.ConnectionError:
        log("Proxy no disponible — esta corriendo proxy.py?")
        time.sleep(5)
        return False
    except Exception as e:
        log(f"Error en poll: {e}")
        time.sleep(2)
        return False


def run_listener():
    """Loop principal — escuchar mensajes continuamente."""
    log("=" * 50)
    log("Claude Bridge Auto-Listener INICIADO")
    log(f"Proxy: {PROXY_URL}")
    log(f"Poll timeout: {POLL_TIMEOUT}s")
    log(f"Claude timeout: {CLAUDE_TIMEOUT}s")
    log(f"Claude CMD: {CLAUDE_CMD}")
    log("=" * 50)

    send_telegram(
        "<b>Auto-Listener ONLINE</b>\n\n"
        "Envia <code>claude: tu tarea</code> y recibiras "
        "la respuesta automaticamente.\n\n"
        "No necesitas /listen manual."
    )

    consecutive_errors = 0

    while True:
        try:
            got_message = poll_and_execute()
            if got_message:
                consecutive_errors = 0

        except KeyboardInterrupt:
            log("Cerrando...")
            send_telegram("<b>Auto-Listener OFFLINE</b>")
            break
        except Exception as e:
            consecutive_errors += 1
            log(f"Error en loop: {e}")
            if consecutive_errors > 10:
                log("Demasiados errores consecutivos, esperando 30s")
                time.sleep(30)
                consecutive_errors = 0
            else:
                time.sleep(2)


if __name__ == "__main__":
    if "--test" in sys.argv:
        log("Enviando mensaje de prueba al proxy...")
        try:
            resp = requests.post(
                f"{PROXY_URL}/message",
                json={"prompt": "say hello and tell me today's date", "source": "test"},
                timeout=5,
            )
            log(f"Mensaje enviado: {resp.json()}")
            log("Escuchando respuesta...")
            poll_and_execute()
        except Exception as e:
            log(f"Test fallo: {e}")
    else:
        run_listener()
