from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from config import MediaConfig
from media_handler import handle_media


@dataclass
class ForwardPayload:
    route_name: str
    chat_name: str
    sender_name: str
    text: str
    forward_from: str = ""
    attachments: list[tuple[bytes, str]] = field(default_factory=list)
    catbox_urls: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


async def process_message(
    message,
    route_name: str,
    chat_name: str,
    sender_name: str,
    media_config: MediaConfig,
    download_fn: Optional[Callable[..., Awaitable[Optional[bytes]]]] = None,
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
        try:
            file_bytes = await download_fn(message)
        except Exception:
            payload.notices.append(f"⚠️ Failed to download media: {filename}")
            return payload
        if file_bytes:
            result = await handle_media(file_bytes, filename, media_config)
            if result.data:
                payload.attachments.append((result.data, result.filename))
            elif result.catbox_url:
                payload.catbox_urls.append(result.catbox_url)
            elif result.notice:
                payload.notices.append(result.notice)
        else:
            payload.notices.append(f"⚠️ Failed to download media: {filename}")

    return payload


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
