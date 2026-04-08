# Claude Bridge: Controlar Claude Code desde el Telefono

## Que es

Un sistema que permite enviar tareas a Claude Code desde Telegram y recibir respuestas automaticamente. Sin tocar el teclado del PC.

```
Telefono (Telegram)
     |
     v
Gemma (bot local, Ollama) — detecta "claude: ..."
     |
     v
bridge.py — intercepta el mensaje, lo envia al proxy
     |
     v
proxy.py — servidor HTTP local, mantiene la cola de tareas
     |
     v
autolistener.py — long-poll al proxy, ejecuta via Claude Code CLI
     |
     v
Claude Code (suscripcion $200/mes, NO API metered)
     |
     v
Respuesta → Telegram (@tu_bot)
```

**Costo total:** $200/mes (suscripcion Claude Max). Gemma, Whisper y todo el routing es local y gratis.

**Por que funciona:** Claude Code CLI esta cubierto por la suscripcion. No pasa por la API metered. Todo corre en tu PC.

---

## Requisitos

| Item | Detalle |
|------|---------|
| PC | Windows/Linux/Mac con Claude Code CLI instalado |
| Claude Code | Suscripcion Max ($200/mes) |
| Ollama | Corriendo Gemma 4 (o cualquier modelo local) |
| Telegram Bot | Un bot creado con @BotFather |
| Python | 3.12+ con flask, requests, whisper (opcional) |

---

## Paso 1: Crear el Bot de Telegram

1. Abrir Telegram → buscar **@BotFather**
2. Enviar `/newbot`
3. Elegir nombre y username
4. Guardar el **token** (ej: `1234567890:AABBccDDeeFFggHHiiJJkkLLmmNNooPPqqR`)
5. Obtener tu **chat_id**: enviar un mensaje al bot, luego abrir:
   ```
   https://api.telegram.org/bot[TOKEN]/getUpdates
   ```
   Buscar `"chat":{"id": XXXXXXX}` — ese es tu chat_id

---

## Paso 2: Instalar Dependencias

```bash
pip install flask requests

# Opcional: para notas de voz
pip install openai-whisper
```

---

## Paso 3: Crear el Proxy (proxy.py)

El proxy es un servidor HTTP que recibe tareas de Gemma y las sirve a Claude Code via long-polling.

Crear archivo `proxy.py`:

```python
"""
Claude Bridge Proxy
Puerto: 5055

Endpoints:
  POST /message   — Gemma envia tarea aqui
  GET  /wait      — Claude Code espera aqui (long-poll)
  POST /done/<id> — Marcar tarea como completada
  GET  /health    — Health check
"""

import json
import time
import threading
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
messages = []
message_lock = threading.Lock()
new_message_event = threading.Event()
history = []


@app.route('/health')
def health():
    with message_lock:
        pending = len([m for m in messages if m['status'] == 'pending'])
    return jsonify({'ok': True, 'pending': pending, 'history': len(history)})


@app.route('/message', methods=['POST'])
def receive_message():
    """Gemma envia una tarea aqui."""
    data = request.get_json()
    prompt = data.get('prompt', data.get('text', ''))
    source = data.get('source', 'gemma')

    if not prompt.strip():
        return jsonify({'error': 'empty prompt'}), 400

    msg = {
        'id': f'msg_{int(time.time()*1000)}',
        'prompt': prompt.strip(),
        'source': source,
        'status': 'pending',
        'result': None,
        'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    with message_lock:
        messages.append(msg)

    new_message_event.set()
    print(f"[{msg['created']}] Nueva tarea de {source}: {prompt[:80]}")
    return jsonify({'ok': True, 'id': msg['id']})


@app.route('/wait')
def wait_for_message():
    """Long-poll: bloquea hasta que llega un mensaje o timeout."""
    timeout = int(request.args.get('timeout', 90))

    with message_lock:
        pending = [m for m in messages if m['status'] == 'pending']
        if pending:
            msg = pending[0]
            msg['status'] = 'processing'
            return jsonify(msg)

    new_message_event.clear()
    arrived = new_message_event.wait(timeout=timeout)

    if arrived:
        with message_lock:
            pending = [m for m in messages if m['status'] == 'pending']
            if pending:
                msg = pending[0]
                msg['status'] = 'processing'
                return jsonify(msg)

    return jsonify({'timeout': True}), 204


@app.route('/done/<msg_id>', methods=['POST'])
def mark_done(msg_id):
    """Marcar tarea como completada."""
    data = request.get_json() or {}
    result = data.get('result', 'done')

    with message_lock:
        for i, m in enumerate(messages):
            if m['id'] == msg_id:
                m['status'] = 'done'
                m['result'] = result
                m['completed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                history.append(m)
                messages.pop(i)
                return jsonify({'ok': True})

    return jsonify({'error': 'not found'}), 404


@app.route('/messages')
def list_messages():
    with message_lock:
        pending = [m for m in messages if m['status'] in ('pending', 'processing')]
    return jsonify(pending)


if __name__ == '__main__':
    print("Claude Bridge Proxy en http://localhost:5055")
    app.run(host='0.0.0.0', port=5055, debug=False, threaded=True)
```

---

## Paso 4: Crear el Bridge (bridge.py)

El bridge monitorea Telegram y envia tareas al proxy cuando detecta el prefijo `claude:`.

Crear archivo `bridge.py`:

```python
"""
Claude Bridge — Monitorea Telegram para tareas de Claude
Uso: python bridge.py monitor
"""

import json
import os
import sys
import time
import re
import requests
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# === CONFIGURAR ESTOS VALORES ===
TELEGRAM_BOT = "TU_BOT_TOKEN_AQUI"
TELEGRAM_CHAT = "TU_CHAT_ID_AQUI"
PROXY_URL = "http://localhost:5055"
POLL_INTERVAL = 5
OFFSET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_offset.json")

# Frases que activan Claude
CLAUDE_TRIGGERS = [
    r'^claude[\s:]+(.+)',
    r'^hey claude[\s:]+(.+)',
    r'^ask claude[\s:]+(.+)',
    r'^tell claude[\s:]+(.+)',
    r'^/claude\s+(.+)',
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(text):
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
    for pattern in CLAUDE_TRIGGERS:
        match = re.match(pattern, text.strip(), re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


def add_task(prompt):
    try:
        resp = requests.post(
            f"{PROXY_URL}/message",
            json={"prompt": prompt, "source": "gemma"},
            timeout=5
        )
        if resp.ok:
            log(f"Tarea enviada al proxy: {prompt[:80]}")
            return resp.json().get("id")
    except:
        log("Proxy no disponible")
    return None


def poll_telegram():
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

        text = msg.get("text", "")
        if not text:
            continue

        prompt = extract_claude_task(text)
        if prompt:
            add_task(prompt)
            send_telegram(f"<b>Tarea enviada a Claude</b>\n\n<i>{prompt[:200]}</i>")


def monitor():
    log("=== Claude Bridge ACTIVO ===")
    send_telegram(
        "<b>Claude Bridge ONLINE</b>\n\n"
        "Envia un mensaje con <code>claude: tu tarea</code> "
        "y Claude Code lo ejecutara automaticamente."
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
```

---

## Paso 5: Crear el Auto-Listener (autolistener.py)

Este es el componente clave — long-polls el proxy y ejecuta tareas via Claude Code CLI automaticamente.

Crear archivo `autolistener.py`:

```python
"""
Claude Bridge Auto-Listener
Escucha el proxy y ejecuta tareas via Claude Code CLI.
Uso: python autolistener.py
"""

import json
import os
import sys
import time
import subprocess
import requests
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# === CONFIGURAR ESTOS VALORES ===
PROXY_URL = "http://localhost:5055"
POLL_TIMEOUT = 90

# Bot de Telegram para responder
TELEGRAM_BOT = "TU_BOT_TOKEN_AQUI"
TELEGRAM_CHAT = "TU_CHAT_ID_AQUI"

# Claude Code CLI — en Windows usar .cmd
# Windows: r"C:\Users\TU_USUARIO\AppData\Roaming\npm\claude.cmd"
# Linux/Mac: "claude"
CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 300

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autolistener.log")


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
    try:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": parse_mode},
                timeout=10,
            )
            if not resp.ok:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT, "text": chunk},
                    timeout=10,
                )
    except Exception as e:
        log(f"Telegram error: {e}")


def mark_done(msg_id, result):
    try:
        requests.post(f"{PROXY_URL}/done/{msg_id}", json={"result": result[:500]}, timeout=10)
    except:
        pass


def execute_with_claude(prompt):
    """Ejecutar prompt via Claude Code CLI."""
    log(f"Ejecutando: {prompt[:100]}")
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
        return output if output else "Tarea completada."

    except subprocess.TimeoutExpired:
        return "Timeout: la tarea tardo mas de 5 minutos."
    except Exception as e:
        return f"Error de ejecucion: {str(e)}"


def poll_and_execute():
    try:
        resp = requests.get(
            f"{PROXY_URL}/wait",
            params={"timeout": POLL_TIMEOUT},
            timeout=POLL_TIMEOUT + 10,
        )

        if resp.status_code == 204:
            return False

        if resp.status_code != 200:
            return False

        msg = resp.json()
        if msg.get("timeout"):
            return False

        msg_id = msg.get("id", "unknown")
        prompt = msg.get("prompt", "")

        if not prompt:
            mark_done(msg_id, "vacio")
            return True

        log(f">>> Tarea: {prompt[:120]}")

        # Notificar que estamos trabajando
        send_telegram(f"<b>Procesando...</b>\n\n<i>{prompt[:200]}</i>")

        # Ejecutar via Claude Code
        response = execute_with_claude(prompt)

        # Enviar resultado a Telegram
        send_telegram(f"<b>Claude</b>\n\n{response}")

        # Marcar como completado
        summary = response[:200] if len(response) <= 200 else response[:197] + "..."
        mark_done(msg_id, summary)
        log(f"<<< Listo: {summary[:100]}")
        return True

    except requests.exceptions.ConnectionError:
        log("Proxy no disponible — esta corriendo proxy.py?")
        time.sleep(5)
        return False
    except Exception as e:
        log(f"Error: {e}")
        time.sleep(2)
        return False


def run_listener():
    log("=" * 50)
    log("Claude Bridge Auto-Listener INICIADO")
    log(f"Proxy: {PROXY_URL}")
    log(f"Claude timeout: {CLAUDE_TIMEOUT}s")
    log("=" * 50)

    send_telegram(
        "<b>Auto-Listener ONLINE</b>\n\n"
        "Envia <code>claude: tu tarea</code> y recibiras "
        "la respuesta automaticamente."
    )

    consecutive_errors = 0
    while True:
        try:
            got_message = poll_and_execute()
            if got_message:
                consecutive_errors = 0
        except KeyboardInterrupt:
            send_telegram("<b>Auto-Listener OFFLINE</b>")
            break
        except Exception as e:
            consecutive_errors += 1
            log(f"Error: {e}")
            if consecutive_errors > 10:
                time.sleep(30)
                consecutive_errors = 0
            else:
                time.sleep(2)


if __name__ == "__main__":
    run_listener()
```

---

## Paso 6: Iniciar Todo

Abrir 3 terminales (o usar nohup/screen):

```bash
# Terminal 1: Proxy
cd ~/Documents/claude_bridge
python proxy.py

# Terminal 2: Bridge (monitorea Telegram)
cd ~/Documents/claude_bridge
python bridge.py monitor

# Terminal 3: Auto-Listener (ejecuta tareas)
cd ~/Documents/claude_bridge
python autolistener.py
```

O en background (Linux/Git Bash):
```bash
cd ~/Documents/claude_bridge
nohup python proxy.py > proxy.log 2>&1 &
nohup python bridge.py monitor > bridge.log 2>&1 &
nohup python autolistener.py > autolistener.log 2>&1 &
```

---

## Paso 7: Probar

1. Abrir Telegram
2. Enviar al bot: `claude: what time is it`
3. Deberias recibir:
   - "Tarea enviada a Claude" (del bridge)
   - "Procesando..." (del autolistener)
   - La respuesta de Claude Code

---

## Como Funciona Internamente

### El Problema Original
Claude Code es un CLI interactivo. No tiene API. No se puede llamar desde otro programa facilmente. Y la suscripcion de $200/mes solo cubre el CLI oficial, no llamadas API directas.

### La Solucion: Proxy + Long-Polling

**Polling (version vieja, lenta):**
```
bridge.py escribe inbox.json → Claude Code lee cada 15s → responde
```
Problema: 15 segundos de delay, Claude quema ciclos leyendo un archivo vacio.

**Proxy + Long-Poll (version actual, instantanea):**
```
bridge.py → POST /message al proxy → proxy despierta al listener → Claude ejecuta
```
El listener esta dormido en `/wait` hasta que llega un mensaje. Cero ciclos desperdiciados, respuesta instantanea.

### Por Que No Se Usa la API

La API de Claude cobra por token. Un dia de uso intensivo puede costar $100+. La suscripcion Max ($200/mes) cubre uso ilimitado de Claude Code CLI. El truco es que TODA la ejecucion pasa por `claude -p`, que es el CLI oficial cubierto por la suscripcion.

### Notas de Voz (Opcional)

El bridge.py incluye soporte para notas de voz via Whisper:
1. El usuario envia audio en Telegram
2. bridge.py descarga el audio
3. Whisper (corriendo en GPU local) transcribe el audio
4. Si la transcripcion empieza con "claude:", se envia como tarea
5. Gratis, local, ~2 segundos de transcripcion

---

## Arquitectura Completa

```
                    TELEFONO
                       |
                  Telegram App
                       |
               @Tu_Bot (Telegram API)
                       |
              bridge.py (polling Telegram)
                       |
                 detecta "claude: ..."
                       |
              POST /message → proxy.py (:5055)
                       |
                 proxy guarda en cola
                       |
           autolistener.py (long-poll /wait)
                       |
                 recibe tarea
                       |
            subprocess: claude -p "tarea"
                       |
              Claude Code CLI ejecuta
              (cubierto por suscripcion)
                       |
                respuesta de vuelta
                       |
          Telegram API → send_message
                       |
                  TELEFONO recibe
```

---

## Configuracion en Windows

### Ubicar Claude CLI
```bash
where claude
# Resultado: C:\Users\TU_USUARIO\AppData\Roaming\npm\claude.cmd
```

En `autolistener.py`, usar la ruta completa con `.cmd`:
```python
CLAUDE_CMD = r"C:\Users\TU_USUARIO\AppData\Roaming\npm\claude.cmd"
```

### Crear Accesos Directos
Crear archivos .bat en el escritorio:

**start_bridge.bat:**
```bat
@echo off
cd C:\Users\TU_USUARIO\Documents\claude_bridge
start "Proxy" python proxy.py
timeout /t 2
start "Bridge" python bridge.py monitor
timeout /t 2
start "Listener" python autolistener.py
```

---

## Troubleshooting

### "Execution error: [WinError 2]"
- En Windows, usar `claude.cmd` no `claude` en CLAUDE_CMD
- Usar la ruta completa: `r"C:\Users\...\npm\claude.cmd"`

### Proxy no disponible
- Verificar que proxy.py esta corriendo: `curl http://localhost:5055/health`
- Verificar puerto: `netstat -ano | grep 5055`

### No recibe mensajes de Telegram
- Verificar token del bot
- Verificar chat_id
- Verificar que bridge.py esta corriendo

### Respuesta no llega a Telegram
- Verificar token del bot de respuesta
- Verificar chat_id de respuesta
- Revisar autolistener.log

### Mensajes duplicados
- Solo debe haber UN autolistener.py corriendo
- Verificar: `tasklist | grep python`
- Matar duplicados: `taskkill /PID XXXX /F`

---

## Archivos del Proyecto

```
claude_bridge/
  proxy.py           — Servidor HTTP, cola de tareas (puerto 5055)
  bridge.py          — Monitorea Telegram, detecta "claude:", envia al proxy
  autolistener.py    — Long-poll proxy, ejecuta via Claude CLI, responde por Telegram
  bridge_offset.json — Ultimo update_id de Telegram (para no repetir mensajes)
  proxy.log          — Log del proxy
  bridge.log         — Log del bridge
  autolistener.log   — Log del listener
```

---

## Costos

| Componente | Costo |
|-----------|-------|
| Claude Code Max | $200/mes |
| Ollama + Gemma | Gratis (local) |
| Whisper | Gratis (local) |
| Telegram Bot | Gratis |
| Proxy/Bridge/Listener | Gratis (tu PC) |
| **TOTAL** | **$200/mes** |

Sin el bridge, usar Claude via API para el mismo volumen de trabajo costaria $1,000-$5,000/mes.

---

*Documento creado con Claude Code*
*github.com/tu-usuario/claude-bridge*
