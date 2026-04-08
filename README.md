# Claude Bridge

**Control Claude Code from your phone via Telegram. $200/mo flat — no metered API.**

An alternative to connecting OpenClaw (or any Telegram-based AI agent) to Claude Code without using the Claude API. After Anthropic's April 4 billing change, running OpenClaw through the API can cost $1,000–$5,000/month. This bridge routes everything through Claude Code CLI, which is still covered by the $200/mo Max subscription.

## The Problem

On April 4 2026, Anthropic blocked third-party tools like OpenClaw from using Claude subscription limits, forcing users onto pay-as-you-go API billing. People saw costs increase 50x overnight.

## The Solution

Claude Code CLI is still covered by the subscription. This bridge routes tasks from Telegram → through a local proxy → into Claude Code CLI, completely bypassing the metered API.

```
Phone (Telegram)
     |
     v
Your AI bot (OpenClaw, Gemma, any bot) — detects "claude: ..."
     |
     v
bridge.py — intercepts the message, sends to proxy
     |
     v
proxy.py — local HTTP server, holds the task queue
     |
     v
autolistener.py — long-polls proxy, executes via Claude Code CLI
     |
     v
Claude Code (covered by $200/mo subscription, NOT metered API)
     |
     v
Response → Telegram
```

**Total cost:** $200/mo (Claude Max subscription). The bot, Whisper, and all routing run locally for free.

## Why It Works

Claude Code CLI is Anthropic's own product — it draws from your subscription exactly the way it always has. The bridge never touches the Claude API directly. Every task runs through `claude -p`, which is the official CLI covered by the subscription.

## How It Differs from Polling

**Old approach (slow):**
```
bot writes to inbox.json → Claude Code checks every 15s → responds
```
Problem: 15-second delay. Claude wastes cycles reading an empty file.

**This approach (instant):**
```
bot → POST /message to proxy → proxy wakes up listener → Claude executes immediately
```
The listener sleeps on `/wait` until a message arrives. Zero wasted cycles, near-instant response.

---

## Setup

### Step 1: Create a Telegram Bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Choose a name and username
4. Save the **token** (e.g. `1234567890:AABBccDDeeFFggHHiiJJkkLLmmNNooPPqqR`)
5. Get your **chat_id**: send a message to the bot, then open:
   ```
   https://api.telegram.org/bot[TOKEN]/getUpdates
   ```
   Look for `"chat":{"id": XXXXXXX}` — that's your chat_id

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt

# Optional: for voice notes
pip install openai-whisper
```

### Step 3: Configure

```bash
cp .env.example .env
# Edit .env with your bot token, chat_id, and claude path
```

For Windows, find your Claude CLI path:
```bash
where claude
# Result: C:\Users\YOUR_USER\AppData\Roaming\npm\claude.cmd
```
Set `CLAUDE_CMD` in `.env` to the full `.cmd` path.

### Step 4: Start Everything

**Windows:**
```
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

**Or manually (3 terminals):**
```bash
# Terminal 1: Proxy
python proxy.py

# Terminal 2: Bridge (monitors Telegram)
python bridge.py monitor

# Terminal 3: Auto-Listener (executes tasks)
python autolistener.py
```

### Step 5: Test

1. Open Telegram
2. Send to your bot: `claude: what time is it`
3. You should receive:
   - "Task queued for Claude" (from bridge)
   - "Processing..." (from autolistener)
   - Claude Code's response

---

## Architecture

```
                    PHONE
                       |
                  Telegram App
                       |
               @Your_Bot (Telegram API)
                       |
              bridge.py (polls Telegram)
                       |
                 detects "claude: ..."
                       |
              POST /message → proxy.py (:5055)
                       |
                 proxy holds task in queue
                       |
           autolistener.py (long-poll /wait)
                       |
                 receives task instantly
                       |
            subprocess: claude -p "task"
                       |
              Claude Code CLI executes
              (covered by subscription)
                       |
                response flows back
                       |
          Telegram API → sendMessage
                       |
                  PHONE receives reply
```

## Voice Notes (Optional)

The bridge supports voice notes via Whisper:
1. User sends a voice message on Telegram
2. bridge.py downloads the audio
3. Whisper (running on local GPU) transcribes it
4. If the transcription starts with "claude:", it's sent as a task
5. Free, local, ~2 seconds to transcribe

## Files

```
claude_bridge/
  proxy.py           — HTTP task queue server (port 5055)
  bridge.py          — Monitors Telegram, detects "claude:", sends to proxy
  autolistener.py    — Long-polls proxy, executes via Claude CLI, replies on Telegram
  .env.example       — Configuration template
  start.bat          — Start everything (Windows)
  start.sh           — Start everything (Linux/Mac)
  requirements.txt   — Python dependencies
```

## Troubleshooting

### "Execution error: [WinError 2]"
- On Windows, use `claude.cmd` not `claude` in CLAUDE_CMD
- Use the full path: `C:\Users\...\npm\claude.cmd`

### Proxy unavailable
- Check proxy.py is running: `curl http://localhost:5055/health`
- Check port: `netstat -ano | grep 5055`

### Not receiving Telegram messages
- Verify bot token in .env
- Verify chat_id in .env
- Make sure bridge.py is running

### Duplicate messages
- Only ONE autolistener.py should be running
- Check: `tasklist | grep python` (Windows) or `ps aux | grep autolistener` (Linux)

## Cost Comparison

| Setup | Monthly Cost |
|-------|-------------|
| OpenClaw via Claude API (post April 4) | $1,000–$5,000 |
| **Claude Bridge (this repo)** | **$200** |

| Component | Cost |
|-----------|------|
| Claude Code Max subscription | $200/mo |
| Local AI bot (Ollama, etc.) | Free |
| Whisper (voice transcription) | Free |
| Telegram Bot | Free |
| This bridge | Free |
| **Total** | **$200/mo** |

## License

MIT
