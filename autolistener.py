"""
Claude Bridge Auto-Listener
=============================
Continuously long-polls the proxy for messages from Telegram.
When a task arrives, executes it via Claude Code CLI and sends
the response back to Telegram.

Usage:
  python autolistener.py          # Start listener
  python autolistener.py --test   # Test with a sample message
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

# === CONFIG (from .env) ===
PROXY_URL = f"http://localhost:{os.getenv('PROXY_PORT', '5055')}"
POLL_TIMEOUT = 90

TELEGRAM_BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Claude Code CLI
# Windows: find with "where claude" — use the .cmd path
# Linux/Mac: usually just "claude"
CLAUDE_CMD = os.getenv("CLAUDE_CMD", "claude")
CLAUDE_TIMEOUT = 300  # 5 min max per task

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autolistener.log")

# Validate config
if not TELEGRAM_BOT or not TELEGRAM_CHAT:
    print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    print("See .env.example for reference")
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
    """Send response back to Telegram."""
    try:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": parse_mode},
                timeout=10,
            )
            if not resp.ok:
                # Retry without parse_mode if HTML fails
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT, "text": chunk},
                    timeout=10,
                )
    except Exception as e:
        log(f"Telegram error: {e}")


def mark_done(msg_id, result):
    """Mark task as done on the proxy."""
    try:
        requests.post(f"{PROXY_URL}/done/{msg_id}", json={"result": result[:500]}, timeout=10)
    except Exception as e:
        log(f"Mark done error: {e}")


def execute_with_claude(prompt):
    """Run a prompt through Claude Code CLI and return the response."""
    log(f"Executing via Claude CLI: {prompt[:100]}")

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
            return f"No output. Stderr: {error[:500]}"

        return output if output else "Task completed (no output)."

    except subprocess.TimeoutExpired:
        return "Task timed out after 5 minutes."
    except Exception as e:
        return f"Execution error: {str(e)}"


def poll_and_execute():
    """Single poll cycle: wait for message, execute, respond."""
    try:
        resp = requests.get(
            f"{PROXY_URL}/wait",
            params={"timeout": POLL_TIMEOUT},
            timeout=POLL_TIMEOUT + 10,
        )

        if resp.status_code == 204:
            return False

        if resp.status_code != 200:
            log(f"Proxy returned {resp.status_code}")
            return False

        msg = resp.json()
        if msg.get("timeout"):
            return False

        msg_id = msg.get("id", "unknown")
        prompt = msg.get("prompt", "")
        source = msg.get("source", "unknown")

        if not prompt:
            log("Empty prompt, skipping")
            mark_done(msg_id, "skipped - empty prompt")
            return True

        log(f">>> Task from {source}: {prompt[:120]}")

        # Notify user we're working on it
        send_telegram(f"<b>Processing...</b>\n\n<i>{prompt[:200]}</i>")

        # Execute via Claude Code
        response = execute_with_claude(prompt)

        # Send result back to Telegram
        send_telegram(f"<b>Claude</b>\n\n{response}")

        # Mark as completed on the proxy
        summary = response[:200] if len(response) <= 200 else response[:197] + "..."
        mark_done(msg_id, summary)

        log(f"<<< Done: {summary[:100]}")
        return True

    except requests.exceptions.Timeout:
        return False
    except requests.exceptions.ConnectionError:
        log("Proxy unavailable — is proxy.py running?")
        time.sleep(5)
        return False
    except Exception as e:
        log(f"Poll error: {e}")
        time.sleep(2)
        return False


def run_listener():
    """Main loop — continuously listen for messages."""
    log("=" * 50)
    log("Claude Bridge Auto-Listener STARTED")
    log(f"Proxy: {PROXY_URL}")
    log(f"Poll timeout: {POLL_TIMEOUT}s")
    log(f"Claude timeout: {CLAUDE_TIMEOUT}s")
    log(f"Claude CMD: {CLAUDE_CMD}")
    log("=" * 50)

    send_telegram(
        "<b>Auto-Listener ONLINE</b>\n\n"
        "Send <code>claude: your task</code> and you'll get "
        "the response automatically.\n\n"
        "No manual /listen needed."
    )

    consecutive_errors = 0

    while True:
        try:
            got_message = poll_and_execute()
            if got_message:
                consecutive_errors = 0

        except KeyboardInterrupt:
            log("Shutting down...")
            send_telegram("<b>Auto-Listener OFFLINE</b>")
            break
        except Exception as e:
            consecutive_errors += 1
            log(f"Main loop error: {e}")
            if consecutive_errors > 10:
                log("Too many consecutive errors, backing off 30s")
                time.sleep(30)
                consecutive_errors = 0
            else:
                time.sleep(2)


if __name__ == "__main__":
    if "--test" in sys.argv:
        log("Sending test message to proxy...")
        try:
            resp = requests.post(
                f"{PROXY_URL}/message",
                json={"prompt": "say hello and tell me today's date", "source": "test"},
                timeout=5,
            )
            log(f"Test message sent: {resp.json()}")
            log("Listening for response...")
            poll_and_execute()
        except Exception as e:
            log(f"Test failed: {e}")
    else:
        run_listener()
