"""
Claude Bridge — Telegram Monitor
==================================
Monitors Telegram for messages with "claude:" prefix and forwards them to the proxy.
Supports voice notes via Whisper (optional).

Usage:
  python bridge.py          # Single check
  python bridge.py monitor  # Continuous monitoring
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

# === CONFIG (from .env) ===
TELEGRAM_BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY_URL = f"http://localhost:{os.getenv('PROXY_PORT', '5055')}"
POLL_INTERVAL = 5
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
OFFSET_FILE = os.path.join(BRIDGE_DIR, "bridge_offset.json")
WHISPER_MODEL = "small"  # small = fast + accurate on GPU

# Validate config
if not TELEGRAM_BOT or not TELEGRAM_CHAT:
    print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    print("See .env.example for reference")
    sys.exit(1)

# Phrases that trigger Claude
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
    """Send a message to Telegram."""
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
    """Check if message is a Claude task. Returns the prompt or None."""
    for pattern in CLAUDE_TRIGGERS:
        match = re.match(pattern, text.strip(), re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


# Lazy-load Whisper (loads once on first voice note)
_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        log(f"Loading Whisper '{WHISPER_MODEL}' model...")
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        log("Whisper model loaded.")
    return _whisper_model


def transcribe_voice(file_id):
    """Download voice note from Telegram and transcribe with Whisper."""
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

        log(f"Whisper transcribed: {text[:80]}")
        return text
    except Exception as e:
        log(f"Whisper error: {e}")
        return None


def add_task(prompt):
    """Send task to the proxy."""
    try:
        resp = requests.post(
            f"{PROXY_URL}/message",
            json={"prompt": prompt, "source": "telegram"},
            timeout=5
        )
        if resp.ok:
            task_id = resp.json().get("id", "?")
            log(f"Task sent to proxy: {prompt[:80]}")
            return task_id
    except:
        log("Proxy unavailable")
    return None


def poll_telegram():
    """Check Telegram for new messages with Claude triggers."""
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

        # Voice notes — transcribe with Whisper
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            file_id = voice["file_id"]
            send_telegram("<i>Transcribing voice note...</i>")
            transcript = transcribe_voice(file_id)
            if transcript:
                prompt = extract_claude_task(transcript)
                if prompt:
                    add_task(prompt)
                    send_telegram(f"<b>Voice task queued for Claude</b>\n\n<i>{prompt[:200]}</i>")
            continue

        text = msg.get("text", "")
        if not text:
            continue

        # Only intercept "claude:" messages — everything else is ignored
        prompt = extract_claude_task(text)
        if prompt:
            add_task(prompt)
            send_telegram(f"<b>Task queued for Claude</b>\n\n<i>{prompt[:200]}</i>")


def monitor():
    """Continuous bridge monitoring."""
    log("=== Claude Bridge ACTIVE ===")
    log(f"Proxy: {PROXY_URL}")
    log(f"Polling Telegram every {POLL_INTERVAL}s")
    log("Triggers: 'claude: ...', 'ask claude ...', '/claude ...'")

    send_telegram(
        "<b>Claude Bridge ONLINE</b>\n\n"
        "Send a message with <code>claude: your task</code> "
        "and Claude Code will execute it automatically.\n\n"
        "Examples:\n"
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
