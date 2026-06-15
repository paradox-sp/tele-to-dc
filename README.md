# tele-to-dc

A lightweight, self-hosted bot that automatically forwards messages from Telegram groups and channels to Discord. Runs as a single async process in Docker — no polling, event-driven, minimal footprint (~50–80 MB RAM). Works on Raspberry Pi.

## Features

- Forwards text, photos, videos, documents, audio, voice messages, stickers, polls, and albums
- Flexible many-to-many routing (many Telegram sources → many Discord channels)
- Smart media handling: re-uploads files under the size limit, uploads large files to [catbox.moe](https://catbox.moe) (optional), or shows a "too large" notice
- Forwarded message attribution shown in embeds
- Optional admin-only `/route` slash commands for live route management
- Docker image built for `linux/amd64` and `linux/arm64`

## Quick Start

### 1. Get credentials

- **Telegram API:** Go to [my.telegram.org/apps](https://my.telegram.org/apps) and create an app to get `api_id` and `api_hash`
- **Discord bot token:** Create a bot at [discord.com/developers/applications](https://discord.com/developers/applications). Invite it to your server with `Send Messages`, `Embed Links`, `Attach Files` permissions (and `Use Application Commands` if using slash commands)

### 2. Configure

```bash
mkdir data
cp config.example.yaml data/config.yaml
```

Edit `data/config.yaml` with your credentials and routes:

```yaml
telegram:
  api_id: 12345678
  api_hash: "your_api_hash"

discord:
  token: "your_bot_token"
  commands_enabled: true   # set false to disable /route commands

routes:
  - name: "my-route"
    from:
      - -1001234567890     # Telegram channel/group ID
    to:
      - 987654321098765432  # Discord channel ID
```

To find Telegram chat IDs, forward a message to `@userinfobot`. For Discord channel IDs, enable Developer Mode and right-click the channel.

### 3. First run (Telegram auth)

Telethon needs to authenticate with your Telegram account once:

```bash
docker compose run -it bot
```

Enter your phone number and the SMS code when prompted. The session is saved to `./data/` and reused on every future start.

### 4. Run

```bash
docker compose up -d
```

Messages will forward automatically. No commands needed.

## Configuration Reference

```yaml
telegram:
  api_id: 12345678
  api_hash: "abc123"
  session_name: "tg_session"      # session file name in data/

discord:
  token: "your_token"
  commands_enabled: true           # false = pure forwarding, no slash commands

media:
  max_upload_size_mb: 25          # files under this are re-uploaded to Discord
  catbox:
    enabled: false                 # upload large files to catbox.moe instead of showing a notice
    userhash: ""                   # optional catbox.moe account hash (empty = anonymous)

routes:
  - name: "route-name"
    from:
      - -1001234567890            # one or more Telegram chat IDs
    to:
      - 987654321098765432        # one or more Discord channel IDs
```

## Slash Commands

When `commands_enabled: true`, server administrators can manage routes without restarting:

| Command | Description |
|---|---|
| `/route list` | Show all active routes |
| `/route add` | Add a new route |
| `/route remove` | Remove a route by name |

Route changes are saved immediately but require a bot restart to take effect on the Telegram listener.

## Build and Push (GitHub Actions)

A manual workflow is included to build and push the Docker image to GitHub Container Registry:

1. Go to **Actions** → **Build and Push Docker Image** → **Run workflow**
2. Enter an optional tag (default: `latest`)

The image is built for both `linux/amd64` and `linux/arm64`.

## Requirements

- Docker and Docker Compose
- A Telegram account (the bot reads as your account, not a bot account)
- A Discord bot token
