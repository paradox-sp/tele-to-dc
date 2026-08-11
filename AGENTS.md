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
- **Disk mode** (env vars, read in `config.py`): `SAVE_MEDIA_TO_DISK=true` makes `telegram_client` download media straight to disk (`MEDIA_CACHE_DIR`, default `data/media_cache`) instead of RAM; `media_handler`/`discord_client` then upload from the file path (`discord.File(fp=path)`, catbox streams from disk). Temp files are deleted after forwarding unless the route has `store: true` (cleanup in `telegram_client._cleanup_media`). Off by default — everything stays in memory and route `store` is ignored.
- `data/` is gitignored and holds secrets + the Telethon session file. Never commit it.
- Telegram auth uses a **user account** via Telethon, not a bot account. The session is persisted in `data/`.
- **Two event loops**: Discord runs on its own loop in a separate daemon thread (`discord-loop` in `main.py`) so its gateway heartbeat is never starved by Telethon. Cross-loop calls must hop via `asyncio.run_coroutine_threadsafe` (see `send_payload_safe`) — don't call Discord coroutines directly from Telethon's loop.
- Route changes via `/route` slash commands save to config immediately but only take effect for the Telegram listener after a **restart** (the Discord side updates live). This is by design, not a bug: the watched-chats list is captured at client creation, and `config.route_map` is a derived dict built in `config.py`'s `__post_init__`.
- Media size logic is three-tier: ≤ `media.max_upload_size_mb` (default 25) re-uploads to Discord; larger files go to catbox.moe if `media.catbox.enabled` (up to `catbox_max_upload_size_mb`, default 200); files above `max_file_size_mb` (default 200) are skipped entirely with a notice for memory safety. The hard cap is enforced **before** download via entity `size` attrs (`message_processor._get_media_size`).
- Telegram albums (grouped messages) are buffered ~1s in `telegram_client.py` and flushed as one combined payload; Discord embeds cap attachments at 10 per message, so album processing caps media at 10 (`MAX_ALBUM_ATTACHMENTS`) and processes messages concurrently (`asyncio.gather`). Album flush tasks use ownership-checked cleanup (`_cancel_cleanup`) so a cancelled task never destroys a successor's buffer.
- Telegram chat IDs are negative for groups/channels (e.g. `-1001234567890`).

## Layout
- `src/`: `main.py` (entry), `config.py` (load/save + dataclasses), `telegram_client.py` (Telethon listener), `discord_client.py` (discord.py bot + slash commands), `message_processor.py` (payload building), `media_handler.py` (upload decisions).
- `tests/`: unit tests for config, media handler, message processor. `conftest.py` is empty.
- `config.example.yaml`: the config schema reference.

## Notes
- No linter/typechecker/formatter configured — only `pytest` is the verification gate.
- Docker image is `python:3.12-alpine`; CI only builds/pushes to GHCR on manual `workflow_dispatch` (no CI test run).
