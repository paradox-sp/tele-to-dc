# Disk-Saving Media Mode + Optimization Fixes — Design

Date: 2026-08-11
Status: Approved by user (both parts)

## Part 2 — Disk-saving media mode

### Motivation
Today the media pipeline is fully in-memory: `download_media(m, bytes)` → bytes in RAM →
`io.BytesIO` copies for Discord attachments and catbox uploads. With 3 concurrent catbox
uploads of up to 200 MB, peak RAM can reach ~600 MB. The user wants an option to write
downloads to disk and upload from disk instead.

### Env vars
- `SAVE_MEDIA_TO_DISK` (bool, default off) — master switch. Off = current in-memory behavior.
- `MEDIA_CACHE_DIR` (path, default `data/media_cache`) — directory for downloaded files.

### Per-route option
- New `Route.store: bool = False`. Only meaningful when the global toggle is on
  ("global gates everything"):
  - `store: true` → send **and keep** the file in the cache dir
  - unset/false → send, then **delete** the file after upload

### Data flow when disk mode is on
1. `telegram_client`: `download_media(m, file=<cache_dir>/<sanitized name>)` — writes straight
   to disk, no bytes in RAM. Cache dir created on demand.
2. `process_message` → `handle_media` accepts `bytes | str(path)`; size check via
   `os.path.getsize` for paths (this also fixes H3 for disk mode).
3. `ForwardPayload.attachments` becomes `list[tuple[bytes | str, str]]` (bytes or file path).
4. `discord_client`: `discord.File(fp=path)` when path (no `BytesIO` copy); `io.BytesIO`
   when bytes.
5. catbox upload streams from the file instead of `BytesIO`.
6. After the payload is sent to **all** channels, temp files are deleted unless the
   route has `store: true`. Cleanup lives in `telegram_client` after the channel loop.

### Memory mode (unchanged)
`SAVE_MEDIA_TO_DISK` off → exactly today's behavior; route `store` ignored.

### Config
- `MediaConfig.save_to_disk: bool`, `MediaConfig.cache_dir: str` — populated from env vars
  in `load_config` (same pattern as `CONFIG_PATH` in main.py).
- `Route.store` parsed in `load_config`, written in `save_config`, preserved by
  `add_route`/`remove_route`.

### Testing
- `handle_media` with a path: size gate, small→Discord, large→catbox, hard cap, cleanup.
- `process_message` with a path-based download.
- `_get_filename` unchanged.
- Config: route `store` parsing + save round-trip; env var → MediaConfig mapping.
- Cleanup: file deleted after send; kept when `store: true`.

## Part 1 — Optimization fixes (from oracle review)

### High
- **H1** Album messages silently dropped by task-cancellation race
  (`telegram_client.py:96-110`): the cancelled flush task's `CancelledError` handler
  unconditionally pops the buffer + registry entry belonging to the successor task.
  Fix: ownership-checked cleanup — only pop when
  `_album_tasks.get(key) is asyncio.current_task()`.
- **H2** Discord startup failure invisible (`main.py:48-50`): `discord_bot.start()` future
  never awaited. Fix: await the future (via `asyncio.wrap_future`) with a timeout and
  surface `LoginFailure`/errors so the supervisor restarts.
- **H3** Size caps checked *after* full download into RAM (`message_processor.py:48-55`,
  `media_handler.py:43`). Fix: pre-download size gate in `process_message` using entity
  `size` attributes (document/video/audio/voice/video_note/photo) — skip with the same
  notice before calling `download_fn`.

### Medium
- **M1** Per-message `fetch_channel` REST call when channel misses cache
  (`discord_client.py:37-46`). Fix: cache resolved channels in a dict; invalidate on
  `NotFound`.
- **M2** Albums download/upload all files then discard past 10 attachments / 25 fields
  (`telegram_client.py:129-136` + `discord_client.py:74-83`). Fix: cap album processing
  at 10 media items (attachments) + remaining field budget for catbox URLs, with a notice.
- **M3** Slash command can't accept negative Telegram chat IDs (`discord_client.py:110,127`).
  Fix: accept the absolute value and negate in code (documented in the command description).
- **M4** Album catbox uploads serialized despite semaphore(3) (`telegram_client.py:131`).
  Fix: `asyncio.gather` for album message processing under the existing semaphore.

### Low (cheap, included)
- **L2** Non-atomic config write (`config.py:129-130`): write to `path + ".tmp"` then
  `os.replace`.
- **L5** Per-message entity lookups + linear `_find_route_name` scan
  (`telegram_client.py:39-40,85-89`): prefer `event.chat`/`event.sender`; precompute a
  chat→route-name dict at client creation.
- **L7** Module-level album buffers survive restarts (`telegram_client.py:14-15`): clear
  on disconnect.

### Deferred (not in this pass)
L1 (restart backoff), L3 (drop prefix-command machinery), L4 (shutdown futures),
L6 (loop-bound module state), L8 (minor duplication).

## Verification
- `pytest` full suite green.
- New tests cover: disk-mode handle_media/process_message, config store round-trip,
  cleanup semantics, H1 race, H3 size gate, M1 cache, M2 cap, M3 negation, L2 atomic write.