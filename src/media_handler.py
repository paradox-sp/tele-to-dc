import asyncio
import io
from dataclasses import dataclass
from typing import Optional

import aiohttp

from config import MediaConfig

CATBOX_URL = "https://catbox.moe/user/api.php"

# Limit concurrent catbox uploads to avoid holding many 200MB files in RAM at once.
_UPLOAD_SEMAPHORE = asyncio.Semaphore(3)
_session: "aiohttp.ClientSession | None" = None


def _get_session() -> "aiohttp.ClientSession":
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


@dataclass
class MediaResult:
    data: Optional[bytes]
    filename: str
    catbox_url: Optional[str]
    notice: Optional[str]


async def handle_media(file_bytes: bytes, filename: str, config: MediaConfig) -> MediaResult:
    size_mb = len(file_bytes) / (1024 * 1024)

    # Absolute hard cap: file too large to even attempt upload (memory safety)
    if size_mb > config.max_file_size_mb:
        notice = (
            f"⚠️ File too large (skipped to avoid memory exhaustion): "
            f"{filename} ({size_mb:.1f} MB, hard cap {config.max_file_size_mb} MB)"
        )
        return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)

    # Small enough to re-upload directly to Discord
    if size_mb <= config.max_upload_size_mb:
        return MediaResult(data=file_bytes, filename=filename, catbox_url=None, notice=None)

    # Too big for Discord — try catbox if enabled
    if config.catbox.enabled:
        if size_mb > config.catbox_max_upload_size_mb:
            notice = (
                f"⚠️ File too large for catbox ({config.catbox_max_upload_size_mb} MB limit): "
                f"{filename} ({size_mb:.1f} MB)"
            )
            return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)
        url = await _upload_to_catbox(file_bytes, filename, config.catbox.userhash)
        if url:
            return MediaResult(data=None, filename=filename, catbox_url=url, notice=None)
        notice = f"⚠️ Failed to upload to catbox: {filename} ({size_mb:.1f} MB)"
        return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)

    # catbox disabled and file exceeds Discord limit
    notice = (
        f"⚠️ File too large to forward: {filename} ({size_mb:.1f} MB, Discord limit "
        f"{config.max_upload_size_mb} MB). Enable catbox to forward larger files."
    )
    return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)


async def _upload_to_catbox(file_bytes: bytes, filename: str, userhash: str) -> Optional[str]:
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("userhash", userhash)
    form.add_field(
        "fileToUpload",
        io.BytesIO(file_bytes),
        filename=filename,
        content_type="application/octet-stream",
    )
    try:
        async with _UPLOAD_SEMAPHORE:
            session = _get_session()
            async with session.post(CATBOX_URL, data=form) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    if text.startswith("https://"):
                        return text
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    return None
