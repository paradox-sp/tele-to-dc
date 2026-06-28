import asyncio
import logging
from typing import Awaitable, Callable

from telethon import TelegramClient, events

from config import AppConfig
from message_processor import ForwardPayload, process_message

logger = logging.getLogger(__name__)

# Buffers for collecting album (grouped) messages before forwarding
# Key is (chat_id, grouped_id) to avoid collisions across different chats.
_album_buffer: dict[tuple[int, int], list] = {}
_album_tasks: dict[tuple[int, int], asyncio.Task] = {}


def create_telegram_client(
    config: AppConfig,
    on_payload: Callable[[int, ForwardPayload], Awaitable[None]],
) -> TelegramClient:
    session_path = f"data/{config.telegram.session_name}"
    client = TelegramClient(session_path, config.telegram.api_id, config.telegram.api_hash)

    watched = list(config.route_map.keys())
    if not watched:
        logger.warning("No Telegram sources in route_map — not listening to any chats.")
        return client

    @client.on(events.NewMessage(chats=watched))
    async def _handle(event: events.NewMessage.Event):
        try:
            chat_id = event.chat_id
            channel_ids = config.route_map.get(chat_id)
            if not channel_ids:
                return

            message = event.message
            chat_name = await _get_chat_name(event, chat_id)
            sender_name = await _get_sender_name(event, chat_name)
            route_name = _find_route_name(config, chat_id)

            if message.grouped_id:
                await _buffer_album(message, chat_id, route_name, chat_name, sender_name, channel_ids, config, on_payload, client)
                return

            async def download(m):
                return await client.download_media(m, bytes)

            payload = await process_message(message, route_name, chat_name, sender_name, config.media, download_fn=download)
            for channel_id in channel_ids:
                try:
                    await on_payload(channel_id, payload)
                except Exception as exc:
                    logger.error("Failed to send payload to channel %d: %s", channel_id, exc)
        except Exception as e:
            logger.exception("Error in Telegram message handler: %s", e)

    return client


async def _get_chat_name(event, fallback: int) -> str:
    try:
        chat = await event.get_chat()
        return getattr(chat, "title", None) or getattr(chat, "username", None) or str(fallback)
    except Exception:
        return str(fallback)


async def _get_sender_name(event, fallback: str) -> str:
    try:
        sender = await event.get_sender()
        if not sender:
            return fallback
        username = getattr(sender, "username", None)
        if username:
            return f"@{username}"
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        return (f"{first} {last}").strip() or fallback
    except Exception:
        return fallback


def _find_route_name(config: AppConfig, chat_id: int) -> str:
    for route in config.routes:
        if chat_id in route.from_chats:
            return route.name
    return str(chat_id)


async def _buffer_album(message, chat_id, route_name, chat_name, sender_name, channel_ids, config, on_payload, client):
    key = (chat_id, message.grouped_id)
    _album_buffer.setdefault(key, []).append(message)

    if key in _album_tasks:
        _album_tasks[key].cancel()

    async def flush():
        await asyncio.sleep(1.0)
        if _album_tasks.get(key) is not asyncio.current_task():
            return  # superseded by a newer task

        messages = _album_buffer.pop(key, [])
        _album_tasks.pop(key, None)

        combined = ForwardPayload(
            route_name=route_name,
            chat_name=chat_name,
            sender_name=sender_name,
            text=next((m.text or m.caption or "" for m in messages if (m.text or m.caption)), ""),
        )

        async def download(m):
            return await client.download_media(m, bytes)

        for msg in messages:
            try:
                part = await process_message(msg, route_name, chat_name, sender_name, config.media, download_fn=download)
                combined.attachments.extend(part.attachments)
                combined.catbox_urls.extend(part.catbox_urls)
                combined.notices.extend(part.notices)
            except Exception as exc:
                logger.error("Failed to process album message %d: %s", msg.id, exc)

        for channel_id in channel_ids:
            try:
                await on_payload(channel_id, combined)
            except Exception as exc:
                logger.error("Failed to send payload to channel %d: %s", channel_id, exc)

    _album_tasks[key] = asyncio.create_task(flush())
