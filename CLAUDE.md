# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Bot Locally
1. Copy the example config and fill in your credentials:
   ```bash
   mkdir -p data
   cp config.example.yaml data/config.yaml
   # Edit data/config.yaml with your telegram api_id, api_hash, discord bot token, and routes
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   python src/main.py
   ```
   The bot will prompt for Telegram phone authentication on first run.

### Running Tests
```bash
pytest
```
Tests are located in the `tests/` directory. The test configuration in `pytest.ini` sets `asyncio_mode = auto` and adds `src/` to the Python path.

### Docker Usage
- Build and run with Docker Compose:
  ```bash
  docker compose up -d
  ```
- The bot stores its data (config and Telegram session) in the `./data` directory, which is mounted as a volume.
- To rebuild after code changes:
  ```bash
  docker compose build
  docker compose up -d
  ```

### Building and Pushing Docker Image (GitHub Actions)
A manual workflow exists in `.github/workflows/` to build and push the Docker image to GitHub Container Registry for both `linux/amd64` and `linux/arm64` platforms.

## Architecture Overview

### Core Components
- **`src/main.py`**: Entry point. Loads configuration, initializes Telegram and Discord clients, and runs the event loop.
- **`src/config.py`**: Handles loading/saving configuration from `data/config.yaml`. Defines dataclasses for Telegram, Discord, media, and route settings.
- **`src/telegram_client.py`**: Uses Telethon to listen for new messages from configured Telegram chats. Handles media downloads and album grouping.
- **`src/discord_client.py`**: Uses discord.py to create a bot that sends formatted messages to Discord channels. Optionally provides slash commands (`/route list/add/remove`) for administrators to manage routes without restarting.
- **`src/message_processor.py`**: Extracts relevant information from Telegram messages (text, media, polls, forwards) and prepares a payload for Discord.
- **`src/media_handler.py`**: Decides whether to upload media directly to Discord (if under size limit) or to catbox.moe (if enabled and over limit).

### Data Flow
1. Telegram client receives a new message from a monitored chat.
2. Message is processed to extract text, media information, and forwarded details.
3. Media is downloaded and handled according to size limits and catbox settings.
4. A formatted payload is sent to all mapped Discord channels via the Discord client.
5. Discord client sends an embed with the message content, attachments, and notices.

### Configuration
- Stored in `data/config.yaml` (see `config.example.yaml` for reference).
- Defines Telegram API credentials, Discord bot token, media settings, and many-to-many routes.
- Routes are mapped internally from Telegram chat IDs to lists of Discord channel IDs for efficient lookup.

### Extensibility
- Adding new media types: Modify `media_handler.py` and update `message_processor.py` to handle the new media type.
- Changing routing logic: Modify the route mapping in `telegram_client.py` or the route processing in `discord_client.py`.
- Adding new Discord features: Extend the slash commands in `discord_client.py` or modify the embed construction.

## Important Notes
- The bot uses a single Telegram session (stored in `data/`). Do not share or commit this session file.
- The bot requires a Telegram user account (not a bot) to function, as it uses Telethon to log in as a user.
- Discord bot requires the `Send Messages`, `Embed Links`, and `Attach Files` permissions. If using slash commands, also need `Use Application Commands`.
- Media larger than the Discord limit (default 25 MB) can be optionally uploaded to catbox.moe (requires enabling in config).
- Route changes via slash commands require a bot restart to take effect for the Telegram listener (the Discord commands update the config immediately).