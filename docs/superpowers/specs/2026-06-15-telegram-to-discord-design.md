# Telegram → Discord Bridge: Design Spec

**Date:** 2026-06-15
**Status:** Approved

---

## Overview

A lightweight, containerized Python bot that automatically forwards messages from Telegram groups/channels to Discord channels. Runs as a single async process, event-driven (no polling), with zero manual interaction required after setup. Designed to run on low-power hardware including Raspberry Pi.

---

## Goals

- Forward any Telegram message type (text, media, files, stickers, polls, albums) to Discord automatically
- Support flexible many-to-many routing (1→1, many→1, 1→many)
- Stay minimal: ~50–80MB RAM, ~120MB Docker image, 4 Python dependencies
- Run indefinitely via Docker with auto-restart

---

## Non-Goals

- Web dashboard or UI
- Message editing/deletion sync (forward only, no updates)
- Telegram bot integration (reads as a user account via MTProto)

---

## Tech Stack

| Component | Library | Reason |
|---|---|---|
| Telegram client | Telethon | Most mature MTProto library, pure Python, ARM-compatible |
| Discord bot | discord.py 2.x | Async-native, slash command support |
| HTTP client | aiohttp | Async file uploads to Catbox.moe |
| Config | PyYAML | Human-readable config file |
| Runtime | Python 3.12 on Alpine | Minimal Docker image |

**requirements.txt:**
```
telethon
discord.py
pyyaml
aiohttp
```

---

## Architecture

Single Python process with one shared `asyncio` event loop running both clients simultaneously. No threads, no subprocesses.

```
config.yaml
    │
    ▼
┌─────────────────────────────────────────┐
│           Single Python Process          │
│                                         │
│  ┌─────────────┐    ┌────────────────┐  │
│  │  Telethon   │───▶│    Message     │  │
│  │  Listener   │    │   Processor    │  │
│  └─────────────┘    └───────┬────────┘  │
│                             │           │
│                    ┌────────▼────────┐  │
│                    │  Media Handler  │  │
│                    └────────┬────────┘  │
│                             │           │
│  ┌─────────────┐    ┌───────▼────────┐  │
│  │  Discord    │◀───│   Forwarder    │  │
│  │  Bot        │    └────────────────┘  │
│  └─────────────┘                        │
└─────────────────────────────────────────┘
```

### Modules

| File | Responsibility |
|---|---|
| `src/main.py` | Entry point — starts both clients on shared event loop |
| `src/config.py` | Load and validate `config.yaml`, write route changes back to disk |
| `src/telegram_client.py` | Telethon event listener, fires on new messages in watched chats |
| `src/message_processor.py` | Converts Telegram message object → Discord-ready payload |
| `src/media_handler.py` | Smart download/re-upload/Catbox logic |
| `src/discord_client.py` | Sends payloads to Discord, registers admin slash commands |

---

## Config File

Located at `data/config.yaml`, mounted as a Docker volume so it persists across container restarts.

```yaml
telegram:
  api_id: 12345678
  api_hash: "your_api_hash_here"
  session_name: "tg_session"          # session file stored in /app/data/

discord:
  token: "your_discord_bot_token"
  commands_enabled: true              # false = disable all slash commands entirely

media:
  max_upload_size_mb: 25             # re-upload to Discord if under this limit
  catbox:
    enabled: false
    userhash: ""                      # optional, empty = anonymous upload

routes:
  - name: "crypto-news"
    from:
      - -1001234567890               # telegram chat ID
    to:
      - 987654321098765432            # discord channel ID

  - name: "multi-source-example"
    from:
      - -1001234567890
      - -1009876543210               # many telegram → one discord
    to:
      - 123456789012345678

  - name: "broadcast-example"
    from:
      - -1001234567890               # one telegram → many discord
    to:
      - 111111111111111111
      - 222222222222222222
```

`from` and `to` are always lists — handles all routing combinations with the same structure.

---

## Routing

On startup, `config.py` builds a lookup table: `telegram_chat_id → [discord_channel_ids]`. When a Telegram message arrives, the listener checks if the source chat ID is in the table and dispatches to all mapped Discord channels.

Multiple routes can reference the same Telegram source or Discord destination without conflict.

---

## Message Processing

Each message is converted to a Discord embed payload before sending.

**Embed format:**
```
┌─────────────────────────────────┐
│ 📢 Route Name                   │  ← from routes[].name
│ Channel/Group Name              │  ← telegram chat title
│ @username                       │  ← sender (if available)
│                                 │
│ Message text here...            │  ← message content
└─────────────────────────────────┘
```

**Message types handled:**

| Type | Handling |
|---|---|
| Text | Embed with content |
| Photo | Embed + re-uploaded attachment |
| Video | Embed + re-uploaded attachment |
| Document/File | Embed + re-uploaded attachment |
| Audio/Voice | Embed + re-uploaded attachment |
| Sticker | Re-uploaded as image |
| Poll | Formatted as text in embed |
| Album (media group) | Multiple attachments in one message |
| Forwarded message | Shows original source attribution in embed |

---

## Media Handling

Smart three-tier fallback:

| Scenario | Action |
|---|---|
| File ≤ `max_upload_size_mb` | Download from Telegram, upload to Discord as attachment |
| File > limit, Catbox **enabled** | Download from Telegram, POST to catbox.moe, send public URL |
| File > limit, Catbox **disabled** | Send notice: `⚠️ File too large to forward: filename.mp4 (87 MB)` |

**Catbox API:**
- Endpoint: `POST https://catbox.moe/user/api.php`
- Fields: `reqtype=fileupload`, `fileToUpload=<bytes>`, `userhash=<hash or empty>`
- Response: plain text public URL e.g. `https://files.catbox.moe/abc123.mp4`
- Anonymous uploads supported (empty `userhash`)
- Implemented with `aiohttp` for non-blocking upload

---

## Discord Slash Commands

Only active when `commands_enabled: true` in config. All commands restricted to **server administrators only**.

| Command | Description |
|---|---|
| `/route list` | Show all currently active routes |
| `/route add <name> <telegram_id> <discord_channel>` | Add a new route, saves to config.yaml |
| `/route remove <name>` | Remove a route by name, saves to config.yaml |

Route changes via commands are immediately active and written back to `config.yaml` so they survive restarts.

---

## Error Handling

- **Telegram flood wait:** Telethon handles automatically — waits and retries
- **Discord rate limits:** discord.py handles automatically
- **Media download failure:** Log error, send text-only embed with note
- **Catbox upload failure:** Fall back to the "too large" notice message
- **Invalid route in config:** Log warning on startup, skip invalid route, continue

---

## Project Structure

```
telegram-to-discord/
├── .github/
│   └── workflows/
│       └── docker-publish.yml   # manual trigger → ghcr.io
├── src/
│   ├── main.py
│   ├── config.py
│   ├── telegram_client.py
│   ├── discord_client.py
│   ├── message_processor.py
│   └── media_handler.py
├── data/                        # gitignored, Docker volume mount
│   ├── tg_session.session       # Telethon session (persists login)
│   └── config.yaml
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── .gitignore
└── config.example.yaml          # template for data/config.yaml
```

---

## Docker

**Dockerfile:**
```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ .
CMD ["python", "main.py"]
```

**docker-compose.yml:**
```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./data:/app/data
```

**Expected image size:** ~120MB
**Expected RAM usage:** ~50–80MB idle

---

## GitHub Actions

**Trigger:** Manual only (`workflow_dispatch`) with optional tag input (default: `latest`).
**Registry:** GitHub Container Registry (`ghcr.io`)
**Platforms:** `linux/amd64` + `linux/arm64` (Raspberry Pi compatible)

Workflow steps:
1. Checkout code
2. Set up QEMU + Docker Buildx for multi-platform builds
3. Login to ghcr.io using `GITHUB_TOKEN` (no extra secrets needed)
4. Build and push multi-platform image

---

## .dockerignore

```
.github/
data/
.gitignore
.env*
*.md
docs/
__pycache__/
*.pyc
```

---

## .gitignore

```
data/
*.session
.env
__pycache__/
*.pyc
*.pyo
```

---

## First-Run Flow

1. Copy `config.example.yaml` to `data/config.yaml` and fill in credentials
2. Run `docker compose run -it bot` on first launch — Telethon prompts for phone number to authenticate
3. Session saved to `data/tg_session.session` — never prompted again
4. From then on use `docker compose up -d` for normal headless operation
4. Messages begin forwarding automatically

---

## Constraints & Assumptions

- Discord file upload limit: 25MB (configurable via `max_upload_size_mb`)
- Telegram session must be authenticated interactively on first run
- Bot must be a member of all Telegram sources (as the authenticated user account)
- Discord bot must have `Send Messages`, `Attach Files`, `Embed Links` permissions in target channels
- Slash commands require `applications.commands` scope when inviting the Discord bot
