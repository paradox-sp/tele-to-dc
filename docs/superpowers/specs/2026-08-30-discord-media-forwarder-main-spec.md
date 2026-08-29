# Discord-to-Discord Media Forwarder — Main Branch Spec

## Overview

Add Discord-to-Discord media forwarding to the existing `telegram-to-discord` bot on the `main` branch. The bot reads image/video messages from specific Discord source channels (via a user account / self-bot) and forwards them to destination channels using Discord's native forward API (`selfcord.Message.forward()`). This runs alongside the existing Telegram → Discord forwarding.

## Constraints

- **Cannot add a bot to the source server** — must use a user account (selfcord.py / discord.py-self) to read source channels
- **Destination uses native forward** — no webhooks, no bot; just `message.forward(destination_channel)`
- **Media only** — forward images and videos; skip text-only messages
- **Config file only** — no slash commands for managing Discord routes; edit `config.yaml` and restart
- **User account must be in both source and destination servers** (for reading and forwarding)

## How It Works

```
Source Discord Server          Your Bot Process            Destination Discord Server
(No bot access)                                          (User account has access)
┌──────────────┐         ┌─────────────────────┐         ┌──────────────┐
│  #source-ch  │──selfcord.py──▶│  discord_user_client  │──message.forward()──▶│  #dest-ch    │
│  (user reads │  websocket     │  (reads + forwards)   │  native API          │  (forwarded  │
│   via user   │  gateway)      │                       │                      │   message)   │
│   account)   │                └─────────────────────┘                └──────────────┘
└──────────────┘
```

## What Gets Forwarded

| Content Type | Forwarded? | Notes |
|-------------|-----------|-------|
| Image attachments (png, jpg, webp) | ✅ Yes | Forwarded via `message.forward()` |
| Video attachments (mp4, webm) | ✅ Yes | Forwarded via `message.forward()` |
| GIF attachments (image/gif) | ❌ No | Skipped by default (not in `media_types`) |
| Text-only messages | ❌ No | Skipped entirely |
| Messages with media + text | ✅ Yes | Entire message forwarded (media + text) |
| Embeds (link previews) | ❌ No | Not forwarded |
| Stickers | ❌ No | Not forwarded |
| Replies | ❌ No | Not forwarded |

## Config Schema

```yaml
# Existing Telegram config (unchanged)
telegram:
  api_id: 12345678
  api_hash: "your_api_hash_here"
  session_name: "tg_session"

# Existing Discord bot config (unchanged)
discord:
  token: "your_discord_bot_token"
  commands_enabled: true

# NEW: Discord user account config (self-bot)
discord_user:
  # Discord user token — NOT a bot token.
  # Get from browser devtools: Network tab → any request → Authorization header.
  # ⚠️ Self-bots violate Discord ToS. Use at your own risk.
  token: "your_user_token_here"

# Existing media config (unchanged, shared by both Telegram and Discord routes)
media:
  max_upload_size_mb: 25
  catbox_max_upload_size_mb: 200
  max_file_size_mb: 200
  catbox:
    enabled: false
    userhash: ""

# Existing Telegram routes (unchanged)
routes:
  - name: "tg-route"
    from: [-1001234567890]
    to: [987654321098765432]

# NEW: Discord source routes
discord_routes:
  - name: "memes-forward"
    from_channel: 1111111111111111111   # source channel ID (right-click → Copy Channel ID)
    to_channel: 2222222222222222222     # destination channel ID
    media_types: ["image", "video"]     # what to forward (skip GIFs by default)
    forward_delay_seconds: 1            # delay between forwards for rate limit safety
    track_message_ids: true             # resume after restart (skip already-forwarded)
```

## New Config Dataclasses

```python
@dataclass
class DiscordUserConfig:
    token: str = ""

@dataclass
class DiscordRoute:
    name: str
    from_channel: int          # source channel ID
    to_channel: int            # destination channel ID
    media_types: list[str] = field(default_factory=lambda: ["image", "video"])
    forward_delay_seconds: float = 1.0
    track_message_ids: bool = True
```

**Note:** No `webhook_url` field. Forwarding uses `selfcord.Message.forward(destination_channel)` — Discord's native forward API.

## New/Modified Files

### New Files
| File | Purpose |
|------|---------|
| `src/discord_user_client.py` | Selfcord.py client — listens on source channels, filters media, forwards to destination |

### Modified Files
| File | Changes |
|------|---------|
| `src/config.py` | Add `DiscordUserConfig`, `DiscordRoute` dataclasses; load/save `discord_routes` |
| `src/main.py` | Initialize Discord user client alongside existing Telegram/Discord bot |
| `config.example.yaml` | Add `discord_user` and `discord_routes` sections |
| `requirements.txt` | Add `selfcord.py` dependency (git install) |
| `tests/test_main.py` | Update mock config to include new fields |

### Unchanged Files
| File | Why unchanged |
|------|--------------|
| `src/discord_client.py` | Existing bot sender — still used for Telegram → Discord |
| `src/telegram_client.py` | Telegram listener — untouched |
| `src/message_processor.py` | `ForwardPayload` reused as-is for Telegram path |
| `src/media_handler.py` | `handle_media()` reused as-is (size checks, catbox) |

## Architecture

### Discord User Client (`discord_user_client.py`)

**Responsibility:** Listen for messages on source channels, filter for media, forward to destination channels using `message.forward()`.

**Key behaviors:**
- Uses `selfcord.py` (discord.py-self) — `discord.Client()` with no intents (selfcord receives all events)
- Listens to `on_message` events
- Filters by channel ID (only processes channels in `discord_routes`)
- Filters attachments by `content_type` (image/*, video/*)
- Skips own messages (`message.author == client.user`)
- Skips text-only messages (no attachments)
- If message has media: forwards entire message to destination via `message.forward(destination_channel)`
- Applies `forward_delay_seconds` delay between forwards for rate limit safety

**No album buffering** — each message is forwarded individually (per user preference).

**No webhook sender needed** — `message.forward()` handles everything natively.

**Resume tracking:**
- Tracks forwarded message IDs in `data/forwarded_ids.json`
- Uses `OrderedDict` per channel for correct truncation ordering
- Debounced disk writes (every 30s, not per-message)
- Flush on shutdown

### Callback Flow

```
discord_user_client.on_message(message)
    ↓
Filter: is this a watched channel? does it have media?
    ↓
message.forward(destination_channel)   ← native selfcord API
    ↓
Mark as forwarded (resume tracking)
```

**Simple and direct** — no intermediate payload building, no webhook sender, no multipart form-data.

### Main Loop Integration (`main.py`)

**Startup sequence:**
1. Load config (existing)
2. Start Discord bot on its own thread (existing)
3. Start Telegram client (existing)
4. Start Discord user client on same event loop as Telegram (new)
5. Run both concurrently via `asyncio.gather`

**Shutdown sequence:**
1. Disconnect Telegram client
2. Close Discord user client
3. Flush forwarded IDs to disk
4. Close Discord bot and stop its loop

**Config-driven feature activation:**
- If `discord_user.token` is empty → Discord user client doesn't start
- If `discord_routes` is empty → Discord user client starts but listens to nothing
- If `routes` is empty → Telegram forwarding doesn't happen
- Both can run simultaneously

## Edge Cases

| Case | Handling |
|------|----------|
| No Discord user token configured | Log warning, skip Discord user client startup |
| No discord_routes configured | Client starts but logs "not listening to any channels" |
| Source channel not accessible | selfcord gateway events simply don't arrive for that channel |
| Destination channel not accessible | `message.forward()` raises HTTPException — log error, skip message |
| File > 25MB | `message.forward()` handles it (Discord's native limit) |
| File > 200MB | Discord may reject — log error, skip message |
| Bot crashes mid-forward | Resume tracking prevents re-forwarding after restart |
| Rate limit hit (429) | selfcord handles retry internally; we add delay between forwards |
| Message has both image and text | Forward entire message (media + text) via `message.forward()` |
| Message has multiple images | Forward as one message (all attachments stay together) |
| User not in destination server | `message.forward()` raises Forbidden — log error, skip |

## Dependencies

```
selfcord.py @ git+https://github.com/dolfies/discord.py-self.git@renamed
```

Already installed in the environment. Uses `selfcord` import name (discord.py-self renamed package).

**Key API used:** `selfcord.Message.forward(destination)` — native Discord forward, available since selfcord 2.1.

## Testing Strategy

- Unit tests for config parsing (`DiscordUserConfig`, `DiscordRoute`)
- Unit tests for media filtering (`_filter_media_attachments`)
- Unit tests for resume tracking (load/save forwarded IDs)
- Integration with existing test suite (90 tests must continue passing)
- Manual test: run bot, send image in source channel, verify it arrives in destination via forward

## Security Notes

- User token stored in `data/config.yaml` (gitignored `data/` directory)
- Self-bot violates Discord ToS — user accepts risk
- No message content logged except media metadata (filename, size)
- User account must be a member of both source and destination servers
