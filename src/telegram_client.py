import asyncio
import logging
import os
from typing import Awaitable, Callable

from telethon import TelegramClient, events

from config import AppConfig, MediaConfig
from media_handler import _UPLOAD_SEMAPHORE
from message_processor import ForwardPayload, _get_filename, process_message

logger = logging.getLogger(__name__)

# Buffers for collecting album (grouped) messages before forwarding
# Key is (chat_id, grouped_id) to avoid collisions across different chats.
_album_buffer: dict[tuple[int, int], list] = {}
_album_tasks: dict[tuple[int, int], asyncio.Task] = {}

# Discord caps attachments at 10 per message; anything beyond that is discarded.
MAX_ALBUM_ATTACHMENTS = 10


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

    # L5: precompute chat_id -> route so per-message handling needs no linear
    # scan. setdefault keeps first-match semantics when a chat appears in
    # several routes (the old linear scan returned the first match).
    route_by_chat: dict[int, object] = {}
    for route in config.routes:
        for chat_id in route.from_chats:
            route_by_chat.setdefault(chat_id, route)

    @client.on(events.NewMessage(chats=watched))
    async def _handle(event: events.NewMessage.Event):
        try:
            chat_id = event.chat_id
            channel_ids = config.route_map.get(chat_id)
            if not channel_ids:
                return

            message = event.message
            route = route_by_chat.get(chat_id)
            route_name = route.name if route else str(chat_id)
            keep_files = bool(route and route.store)
            chat_name = await _get_chat_name(event, chat_id)
            sender_name = await _get_sender_name(event, chat_name)

            if message.grouped_id:
                await _buffer_album(
                    message, chat_id, route_name, chat_name, sender_name,
                    channel_ids, config, on_payload, client, keep_files,
                )
                return

            async def download(m):
                return await _download_media(client, m, config.media)

            payload = await process_message(
                message, route_name, chat_name, sender_name, config.media, download_fn=download
            )
            for channel_id in channel_ids:
                try:
                    await on_payload(channel_id, payload)
                except Exception as exc:
                    logger.error("Failed to send payload to channel %d: %s", channel_id, exc)
            _cleanup_media(payload, keep=keep_files)
        except Exception as e:
            logger.exception("Error in Telegram message handler: %s", e)

    async def _watch_disconnect():
        # Telethon has no events.Disconnect — the `disconnected` future
        # resolves when the connection ends (intentional disconnect or a
        # failed reconnection). Clear stale album state so it can't survive
        # a reconnect/restart (L7).
        try:
            await client.disconnected
        except OSError:
            pass  # unexpected disconnect — still clear state below
        _clear_album_state()

    asyncio.create_task(_watch_disconnect())

    return client


async def _get_chat_name(event, fallback: int) -> str:
    # L5: event.chat is usually already populated — avoid the API call.
    chat = getattr(event, "chat", None)
    if chat is not None:
        name = getattr(chat, "title", None) or getattr(chat, "username", None)
        if name:
            return name
    try:
        chat = await event.get_chat()
        return getattr(chat, "title", None) or getattr(chat, "username", None) or str(fallback)
    except Exception:
        return str(fallback)


async def _get_sender_name(event, fallback: str) -> str:
    # L5: event.sender is usually already populated — avoid the API call.
    sender = getattr(event, "sender", None)
    if sender is not None:
        username = getattr(sender, "username", None)
        if username:
            return f"@{username}"
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        name = (f"{first} {last}").strip()
        if name:
            return name
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


async def _download_media(client, message, media_config: MediaConfig):
    """Download to disk (returns a path) in disk mode, else to memory (bytes)."""
    if media_config.save_to_disk:
        os.makedirs(media_config.cache_dir, exist_ok=True)
        # message.id is only unique per chat, and document filenames repeat
        # freely — prefix with chat_id + message.id so concurrent downloads
        # (album gather, overlapping messages) never collide on one path.
        base = _get_filename(message) or f"media_{message.id}"
        path = _cache_path(media_config.cache_dir, f"{message.chat_id}_{message.id}_{base}")
        try:
            return await client.download_media(message, file=path)
        except Exception:
            # Don't leave a partial download behind for the next message.
            try:
                os.remove(path)
            except OSError:
                pass
            raise
    return await client.download_media(message, bytes)


def _cache_path(cache_dir: str, filename: str) -> str:
    """Sanitize a Telegram-provided filename and join it to the cache dir."""
    safe = os.path.basename(filename.replace("\\", "/"))
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe)
    return os.path.join(cache_dir, safe)


def _cleanup_media(payload: ForwardPayload, keep: bool) -> None:
    """Delete disk-backed attachments after forwarding, unless the route stores them."""
    if keep:
        return
    for media, _ in payload.attachments:
        if isinstance(media, str):
            try:
                os.remove(media)
            except OSError:
                logger.debug("Could not remove cached media %s", media)


def _clear_album_state() -> None:
    _album_buffer.clear()
    _album_tasks.clear()


def _cancel_cleanup(key) -> None:
    """Ownership-checked cleanup for a cancelled flush task (H1).

    Only the task that currently owns the key may pop the buffer/registry;
    a superseded task must leave the successor's state intact.
    """
    if _album_tasks.get(key) is asyncio.current_task():
        _album_buffer.pop(key, None)
        _album_tasks.pop(key, None)


def _split_album_messages(messages):
    """Split album messages into text-only and media, capping media at 10 (M2)."""
    text_only = [m for m in messages if _get_filename(m) is None]
    media = [m for m in messages if _get_filename(m) is not None]
    return text_only, media[:MAX_ALBUM_ATTACHMENTS], len(media)


async def _buffer_album(
    message, chat_id, route_name, chat_name, sender_name,
    channel_ids, config, on_payload, client, keep_files,
):
    key = (chat_id, message.grouped_id)
    _album_buffer.setdefault(key, []).append(message)

    if key in _album_tasks:
        _album_tasks[key].cancel()

    async def flush():
        try:
            await asyncio.sleep(1.0)
            if _album_tasks.get(key) is not asyncio.current_task():
                return  # superseded by a newer task
            messages = _album_buffer.pop(key, [])
            _album_tasks.pop(key, None)
        except asyncio.CancelledError:
            _cancel_cleanup(key)
            raise

        def _fwd(m):
            if m.fwd_from:
                name = getattr(m.fwd_from, "from_name", None) or ""
                return f"↩️ Forwarded from: {name}" if name else "↩️ Forwarded message"
            return ""

        combined = ForwardPayload(
            route_name=route_name,
            chat_name=chat_name,
            sender_name=sender_name,
            text=next((m.text or m.caption or "" for m in messages if (m.text or m.caption)), ""),
            forward_from=next((_fwd(m) for m in messages if _fwd(m)), ""),
        )

        # M2: don't download/upload media that Discord will discard anyway.
        text_only, media_msgs, total_media = _split_album_messages(messages)
        if total_media > MAX_ALBUM_ATTACHMENTS:
            combined.notices.append(
                f"⚠️ Album has {total_media} files — only first 10 forwarded (Discord limit)."
            )
        selected = text_only + media_msgs

        async def download(m):
            return await _download_media(client, m, config.media)

        async def process_one(m):
            # M4: bound concurrent download+upload units (3) so a 10-file album
            # in memory mode can't spike RAM to ~10x file size before uploads
            # start. The semaphore gates the whole per-item unit, not just the
            # catbox upload phase.
            async with _UPLOAD_SEMAPHORE:
                return await process_message(
                    m, route_name, chat_name, sender_name, config.media, download_fn=download
                )

        try:
            results = await asyncio.gather(
                *(process_one(m) for m in selected),
                return_exceptions=True,
            )
            for part in results:
                if isinstance(part, BaseException):
                    logger.error("Failed to process album message: %s", part)
                    continue
                combined.attachments.extend(part.attachments)
                combined.catbox_urls.extend(part.catbox_urls)
                combined.notices.extend(part.notices)

            for channel_id in channel_ids:
                try:
                    await on_payload(channel_id, combined)
                except Exception as exc:
                    logger.error("Failed to send payload to channel %d: %s", channel_id, exc)
        finally:
            # Even on cancellation (process shutdown) or a send error, don't
            # leak disk files — unless the route asked to store them.
            _cleanup_media(combined, keep=keep_files)

    _album_tasks[key] = asyncio.create_task(flush())