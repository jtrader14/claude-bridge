"""
Claude Bridge Proxy
====================
HTTP server that receives tasks and serves them to Claude Code via long-polling.

Endpoints:
  POST /message        — Submit a task
  GET  /wait           — Claude Code waits here (long-poll)
  POST /done/<id>      — Mark task as completed
  GET  /messages       — List pending tasks
  GET  /health         — Health check

Usage: python proxy.py
Port: 5055
"""

import json
import os
import time
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

PROXY_PORT = int(os.getenv("PROXY_PORT", "5055"))

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
    """Receive a task from bridge/Telegram."""
    data = request.get_json()
    prompt = data.get('prompt', data.get('text', ''))
    source = data.get('source', 'telegram')

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
    print(f"[{msg['created']}] New task from {source}: {prompt[:80]}")
    return jsonify({'ok': True, 'id': msg['id']})


@app.route('/wait')
def wait_for_message():
    """Long-poll: blocks until a message arrives or timeout."""
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
    """Mark a task as completed with result."""
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
                print(f"[{m['completed']}] Completed: {m['prompt'][:60]}")
                return jsonify({'ok': True})

    return jsonify({'error': 'not found'}), 404


@app.route('/messages')
def list_messages():
    """List pending tasks."""
    with message_lock:
        pending = [m for m in messages if m['status'] in ('pending', 'processing')]
    return jsonify(pending)


@app.route('/history')
def list_history():
    """List completed tasks (last 20)."""
    return jsonify(history[-20:])


if __name__ == '__main__':
    print(f"Claude Bridge Proxy running at http://localhost:{PROXY_PORT}")
    print("Submit task:   POST /message")
    print("Claude waits:  GET  /wait")
    app.run(host='0.0.0.0', port=PROXY_PORT, debug=False, threaded=True)
