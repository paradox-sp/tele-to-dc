# AGENTS.md

Compact guidance for OpenCode sessions in this repo. For full architecture, see `CLAUDE.md`.

## What this is
A single-process async bot that forwards Telegram messages to Discord. Entrypoint is `src/main.py` (`python src/main.py`). It runs a supervisor loop that auto-restarts on crash (5s delay).

## Commands
- Install: `pip install -r requirements.txt -r requirements-dev.txt`
- Run: `python src/main.py` (needs `data/config.yaml`; first run prompts for Telegram phone auth)
- Tests: `pytest` (config in `pytest.ini`: `asyncio_mode = auto`, `pythonpath = src`)
- Single test: `pytest tests/test_config.py`
- Docker first-run auth: `docker compose run -it bot` (interactive login), then `docker compose up -d`

## Non-obvious facts
- `pytest.ini` sets `pythonpath = src`, so tests import modules as top-level (`from config import ...`), not `src.config`. Don't add `src.` prefixes in imports.
- `CONFIG_PATH` env var overrides config location (Docker sets it to `/app/data/config.yaml`). Default is `data/config.yaml`.
- `data/` is gitignored and holds secrets + the Telethon session file. Never commit it.
- Telegram auth uses a **user account** via Telethon, not a bot account. The session is persisted in `data/`.
- Route changes via `/route` slash commands save to config immediately but only take effect for the Telegram listener after a **restart** (the Discord side updates live).
- Media: files ≤ `media.max_upload_size_mb` (default 25) re-upload to Discord; larger files go to catbox.moe if `media.catbox.enabled`, else a "too large" notice.
- Telegram chat IDs are negative for groups/channels (e.g. `-1001234567890`).

## Layout
- `src/`: `main.py` (entry), `config.py` (load/save + dataclasses), `telegram_client.py` (Telethon listener), `discord_client.py` (discord.py bot + slash commands), `message_processor.py` (payload building), `media_handler.py` (upload decisions).
- `tests/`: unit tests for config, media handler, message processor. `conftest.py` is empty.
- `config.example.yaml`: the config schema reference.

## Notes
- No linter/typechecker/formatter configured — only `pytest` is the verification gate.
- Docker image is `python:3.12-alpine`; CI only builds/pushes to GHCR on manual `workflow_dispatch` (no CI test run).
