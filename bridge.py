"""
Claude Bridge — Monitorea Telegram para tareas de Claude
==========================================================
Detecta mensajes con prefijo "claude:" en Telegram y los envia al proxy.
Soporta notas de voz via Whisper (opcional).

Uso:
  python bridge.py          # Verificacion unica
  python bridge.py monitor  # Monitoreo continuo
"""

import json
import os
import sys
import time
import re
import tempfile
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# === CONFIGURACION (desde .env) ===
TELEGRAM_BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY_URL = f"http://localhost:{os.getenv('PROXY_PORT', '5055')}"
POLL_INTERVAL = 5
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
OFFSET_FILE = os.path.join(BRIDGE_DIR, "bridge_offset.json")
WHISPER_MODEL = "small"  # small = rapido + preciso en GPU

# Verificar configuracion
if not TELEGRAM_BOT or not TELEGRAM_CHAT:
    print("ERROR: Configurar TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
    print("Ver .env.example para referencia")
    sys.exit(1)

# Frases que activan Claude
CLAUDE_TRIGGERS = [
    r'^claude[\s:]+(.+)',
    r'^hey claude[\s:]+(.+)',
    r'^ask claude[\s:]+(.+)',
    r'^tell claude[\s:]+(.+)',
    r'^claude task[\s:]+(.+)',
    r'^/claude\s+(.+)',
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(text):
    """Enviar mensaje a Telegram."""
    try:
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": "HTML"},
                timeout=10
            )
    except Exception as e:
        log(f"Telegram error: {e}")


def load_offset():
    try:
        with open(OFFSET_FILE, 'r') as f:
            return json.load(f).get("offset", 0)
    except:
        return 0


def save_offset(offset):
    with open(OFFSET_FILE, 'w') as f:
        json.dump({"offset": offset}, f)


def extract_claude_task(text):
    """Verificar si el mensaje es una tarea para Claude. Retorna el prompt o None."""
    for pattern in CLAUDE_TRIGGERS:
        match = re.match(pattern, text.strip(), re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


# Lazy-load Whisper (se carga solo con la primera nota de voz)
_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        log(f"Cargando modelo Whisper '{WHISPER_MODEL}'...")
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        log("Whisper cargado.")
    return _whisper_model


def transcribe_voice(file_id):
    """Descargar nota de voz de Telegram y transcribir con Whisper."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT}/getFile",
            params={"file_id": file_id}, timeout=10
        )
        file_path = resp.json()["result"]["file_path"]

        audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT}/{file_path}"
        audio_data = requests.get(audio_url, timeout=30).content

        ext = os.path.splitext(file_path)[1] or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        model = get_whisper()
        result = model.transcribe(tmp_path)
        text = result["text"].strip()
        os.unlink(tmp_path)

        log(f"Whisper transcribio: {text[:80]}")
        return text
    except Exception as e:
        log(f"Whisper error: {e}")
        return None


def add_task(prompt):
    """Enviar tarea al proxy."""
    try:
        resp = requests.post(
            f"{PROXY_URL}/message",
            json={"prompt": prompt, "source": "telegram"},
            timeout=5
        )
        if resp.ok:
            task_id = resp.json().get("id", "?")
            log(f"Tarea enviada al proxy: {prompt[:80]}")
            return task_id
    except:
        log("Proxy no disponible")
    return None


def poll_telegram():
    """Revisar Telegram por nuevos mensajes con triggers de Claude."""
    offset = load_offset()
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT}/getUpdates",
            params={"offset": offset + 1, "timeout": 0, "limit": 10},
            timeout=10
        )
        updates = resp.json().get("result", [])
    except:
        return

    for update in updates:
        save_offset(update["update_id"])
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if chat_id != TELEGRAM_CHAT:
            continue

        # Notas de voz — transcribir con Whisper
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            file_id = voice["file_id"]
            send_telegram("<i>Transcribiendo nota de voz...</i>")
            transcript = transcribe_voice(file_id)
            if transcript:
                prompt = extract_claude_task(transcript)
                if prompt:
                    add_task(prompt)
                    send_telegram(f"<b>Tarea de voz enviada a Claude</b>\n\n<i>{prompt[:200]}</i>")
            continue

        text = msg.get("text", "")
        if not text:
            continue

        # Solo interceptar mensajes con "claude:" — el resto va a Gemma normal
        prompt = extract_claude_task(text)
        if prompt:
            add_task(prompt)
            send_telegram(f"<b>Tarea enviada a Claude</b>\n\n<i>{prompt[:200]}</i>")


def monitor():
    """Monitoreo continuo del bridge."""
    log("=== Claude Bridge ACTIVO ===")
    log(f"Proxy: {PROXY_URL}")
    log(f"Polling Telegram cada {POLL_INTERVAL}s")
    log("Triggers: 'claude: ...', 'ask claude ...', '/claude ...'")

    send_telegram(
        "<b>Claude Bridge ONLINE</b>\n\n"
        "Envia un mensaje con <code>claude: tu tarea</code> "
        "y Claude Code lo ejecutara automaticamente.\n\n"
        "Ejemplos:\n"
        "<code>claude: fix the bug in server.py</code>\n"
        "<code>ask claude what's running on port 8080</code>\n"
        "<code>/claude check git status</code>"
    )

    while True:
        try:
            poll_telegram()
        except Exception as e:
            log(f"Error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "monitor":
        monitor()
    else:
        poll_telegram()
