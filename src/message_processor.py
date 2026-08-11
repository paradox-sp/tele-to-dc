import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Union

from config import MediaConfig
from media_handler import handle_media

logger = logging.getLogger(__name__)


@dataclass
class ForwardPayload:
    route_name: str
    chat_name: str
    sender_name: str
    text: str
    forward_from: str = ""
    # Media is either in-memory bytes or a path to a file on disk (disk mode).
    attachments: list[tuple[Union[bytes, str], str]] = field(default_factory=list)
    catbox_urls: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


async def process_message(
    message,
    route_name: str,
    chat_name: str,
    sender_name: str,
    media_config: MediaConfig,
    download_fn: Optional[Callable[..., Awaitable[Optional[Union[bytes, str]]]]] = None,
) -> ForwardPayload:
    text = message.text or message.caption or ""

    forward_from = ""
    if message.fwd_from:
        name = getattr(message.fwd_from, "from_name", None) or ""
        forward_from = f"↩️ Forwarded from: {name}" if name else "↩️ Forwarded message"

    payload = ForwardPayload(
        route_name=route_name,
        chat_name=chat_name,
        sender_name=sender_name,
        text=text,
        forward_from=forward_from,
    )

    if message.poll:
        payload.text = _format_poll(message.poll.poll)
        return payload

    filename = _get_filename(message)
    if filename and download_fn:
        # H3: gate on the entity's declared size BEFORE downloading, so an
        # oversized file never enters RAM at all.
        entity_size = _get_media_size(message)
        if entity_size is not None:
            size_mb = entity_size / (1024 * 1024)
            if size_mb > media_config.max_file_size_mb:
                payload.notices.append(
                    f"⚠️ File too large (skipped to avoid memory exhaustion): "
                    f"{filename} ({size_mb:.1f} MB, hard cap {media_config.max_file_size_mb} MB)"
                )
                return payload
        try:
            media = await download_fn(message)
        except Exception:
            payload.notices.append(f"⚠️ Failed to download media: {filename}")
            return payload
        if media:
            try:
                result = await handle_media(media, filename, media_config)
            except Exception as exc:
                # e.g. a disk-mode file vanished between download and upload —
                # degrade to a notice instead of dropping the whole payload.
                logger.warning("Media handling failed for %s: %s", filename, exc)
                payload.notices.append(f"⚠️ Failed to process media: {filename}")
                return payload
            if result.data:
                payload.attachments.append((result.data, result.filename))
            elif result.catbox_url:
                payload.catbox_urls.append(result.catbox_url)
            elif result.notice:
                payload.notices.append(result.notice)
        else:
            payload.notices.append(f"⚠️ Failed to download media: {filename}")

    return payload


def _get_media_size(message) -> Optional[int]:
    """Declared size in bytes from the Telegram entity, if available."""
    for attr in ("document", "video", "audio", "voice", "video_note"):
        entity = getattr(message, attr, None)
        if entity is not None:
            size = getattr(entity, "size", None)
            if isinstance(size, int) and size > 0:
                return size
    photo = getattr(message, "photo", None)
    if photo is not None:
        sizes = getattr(photo, "sizes", None)
        if sizes:
            largest = sizes[-1]
            size = getattr(largest, "size", None)
            if isinstance(size, int) and size > 0:
                return size
    return None


def _format_poll(poll) -> str:
    options = "\n".join(f"• {opt.text}" for opt in poll.answers)
    return f"📊 **{poll.question}**\n{options}"


def _get_filename(message) -> Optional[str]:
    if message.sticker:
        return f"sticker_{message.id}.webp"
    if message.photo:
        return f"photo_{message.id}.jpg"
    if message.video:
        return _doc_filename(message, f"video_{message.id}.mp4")
    if message.video_note:
        return f"video_note_{message.id}.mp4"
    if message.audio:
        return _doc_filename(message, f"audio_{message.id}.mp3")
    if message.voice:
        return f"voice_{message.id}.ogg"
    if message.document:
        return _doc_filename(message, f"file_{message.id}")
    return None


def _doc_filename(message, default: str) -> str:
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                return attr.file_name
    return default
