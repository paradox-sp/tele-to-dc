from __future__ import annotations

import asyncio
import collections
import json
import logging
import os

import selfcord as discord

from config import AppConfig, DiscordRoute

logger = logging.getLogger(__name__)

# Message ID tracking for resume capability
_FORWARDED_IDS_FILE = "data/forwarded_ids.json"
_forwarded_ids: dict[int, collections.OrderedDict] = {}

# Debounced disk save
_save_pending = False
_SAVE_INTERVAL = 30.0
_save_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Forwarded-ID persistence
# ---------------------------------------------------------------------------

def _load_forwarded_ids() -> None:
    global _forwarded_ids
    try:
        with open(_FORWARDED_IDS_FILE) as f:
            data = json.load(f)
            _forwarded_ids = {int(k): collections.OrderedDict.fromkeys(v) for k, v in data.items()}
        logger.info("Loaded %d tracked channels with forwarded message IDs", len(_forwarded_ids))
    except FileNotFoundError:
        _forwarded_ids = {}
    except Exception as e:
        logger.warning("Failed to load forwarded IDs: %s", e)
        _forwarded_ids = {}


def _save_forwarded_ids() -> None:
    try:
        os.makedirs(os.path.dirname(_FORWARDED_IDS_FILE), exist_ok=True)
        data = {str(k): list(v.keys()) for k, v in _forwarded_ids.items()}
        tmp_path = _FORWARDED_IDS_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, _FORWARDED_IDS_FILE)
    except Exception as e:
        logger.warning("Failed to save forwarded IDs: %s", e)


def _is_message_forwarded(channel_id: int, message_id: int) -> bool:
    return channel_id in _forwarded_ids and message_id in _forwarded_ids[channel_id]


def _mark_message_forwarded(channel_id: int, message_id: int) -> None:
    global _save_pending
    if channel_id not in _forwarded_ids:
        _forwarded_ids[channel_id] = collections.OrderedDict()
    _forwarded_ids[channel_id][message_id] = None
    if len(_forwarded_ids[channel_id]) > 10000:
        for _ in range(5000):
            _forwarded_ids[channel_id].popitem(last=False)
    _save_pending = True
    _schedule_save_if_needed()


# ---------------------------------------------------------------------------
# Debounced save helpers
# ---------------------------------------------------------------------------

def _schedule_save_if_needed() -> None:
    global _save_task
    if _save_task is not None and not _save_task.done():
        return
    _save_task = asyncio.ensure_future(_periodic_save())


async def _periodic_save() -> None:
    global _save_pending
    while True:
        await asyncio.sleep(_SAVE_INTERVAL)
        if _save_pending:
            _save_pending = False
            _save_forwarded_ids()


async def flush_forwarded_ids() -> None:
    """Force an immediate save (called on shutdown)."""
    global _save_pending
    if _save_pending:
        _save_pending = False
        _save_forwarded_ids()
    if _save_task is not None and not _save_task.done():
        _save_task.cancel()


# ---------------------------------------------------------------------------
# Media filtering
# ---------------------------------------------------------------------------

def _has_media(message: discord.Message, media_types: list[str]) -> bool:
    """Check if a message has attachments matching the allowed media types."""
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        if "image" in media_types and content_type.startswith("image/"):
            if content_type == "image/gif" and "gif" not in media_types:
                continue
            return True
        if "video" in media_types and content_type.startswith("video/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def create_discord_user_client(
    config: AppConfig,
) -> "discord.Client | None":
    """Create and configure the Discord user client (self-bot).

    Returns None if no token is configured.
    """
    if not config.discord_user.token:
        logger.warning("No Discord user token configured — Discord user client will not start")
        return None

    client = discord.Client()

    watched_channels = config.discord_route_map
    if not watched_channels:
        logger.warning("No Discord routes configured — not listening to any channels.")
        return client

    _load_forwarded_ids()

    @client.event
    async def on_ready():
        logger.info("Discord user client ready: %s (ID: %s)", client.user, client.user.id)
        logger.info("Monitoring %d channel(s)", len(watched_channels))

    @client.event
    async def on_message(message: discord.Message):
        try:
            if message.author == client.user:
                return

            channel_id = message.channel.id
            routes = watched_channels.get(channel_id)
            if not routes:
                return

            for route in routes:
                # Skip if already forwarded
                if route.track_message_ids and _is_message_forwarded(channel_id, message.id):
                    continue

                # Skip messages without matching media
                if not _has_media(message, route.media_types):
                    continue

                # Forward the message
                try:
                    dest = client.get_channel(route.to_channel)
                    if dest is None:
                        dest = await client.fetch_channel(route.to_channel)

                    await asyncio.sleep(route.forward_delay_seconds)
                    await message.forward(dest)
                    logger.info("Forwarded message %s from #%s to %s",
                                message.id, getattr(message.channel, 'name', channel_id), route.to_channel)

                    if route.track_message_ids:
                        _mark_message_forwarded(channel_id, message.id)

                except discord.NotFound:
                    logger.error("Destination channel %d not found for route %s", route.to_channel, route.name)
                except discord.Forbidden:
                    logger.error("No access to destination channel %d for route %s", route.to_channel, route.name)
                except Exception as exc:
                    logger.error("Failed to forward message %s for route %s: %s", message.id, route.name, exc)

        except Exception as e:
            logger.exception("Error in Discord user message handler: %s", e)

    return client
