import asyncio
import io
import logging
import os
from dataclasses import dataclass
from typing import Optional, Union

import aiohttp

from config import MediaConfig

logger = logging.getLogger(__name__)

CATBOX_URL = "https://catbox.moe/user/api.php"

# catbox silently closes connections from default library User-Agents
# (e.g. "Python/3.x aiohttp/3.x"), so send a descriptive app UA.
USER_AGENT = "tele-to-dc/1.0 (+https://github.com/paradox-sp/tele-to-dc)"

# Limit concurrent catbox uploads to avoid holding many 200MB files in RAM at once.
_UPLOAD_SEMAPHORE = asyncio.Semaphore(3)
_session: "aiohttp.ClientSession | None" = None

# Media can be in-memory bytes or a path to a file on disk (disk mode).
MediaSource = Union[bytes, str]


def _get_session() -> "aiohttp.ClientSession":
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
    return _session


def _source_size(source: MediaSource) -> int:
    if isinstance(source, str):
        return os.path.getsize(source)
    return len(source)


@dataclass
class MediaResult:
    data: Optional[MediaSource]
    filename: str
    catbox_url: Optional[str]
    notice: Optional[str]


async def handle_media(source: MediaSource, filename: str, config: MediaConfig) -> MediaResult:
    size_mb = _source_size(source) / (1024 * 1024)

    # Absolute hard cap: file too large to even attempt upload (memory safety)
    if size_mb > config.max_file_size_mb:
        notice = (
            f"⚠️ File too large (skipped to avoid memory exhaustion): "
            f"{filename} ({size_mb:.1f} MB, hard cap {config.max_file_size_mb} MB)"
        )
        return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)

    # Small enough to re-upload directly to Discord
    if size_mb <= config.max_upload_size_mb:
        return MediaResult(data=source, filename=filename, catbox_url=None, notice=None)

    # Too big for Discord — try catbox if enabled
    if config.catbox.enabled:
        if size_mb > config.catbox_max_upload_size_mb:
            notice = (
                f"⚠️ File too large for catbox ({config.catbox_max_upload_size_mb} MB limit): "
                f"{filename} ({size_mb:.1f} MB)"
            )
            return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)
        url = await _upload_to_catbox(source, filename, config.catbox.userhash)
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


async def _upload_to_catbox(source: MediaSource, filename: str, userhash: str) -> Optional[str]:
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("userhash", userhash)
    file_obj = None
    if isinstance(source, str):
        # Stream from disk instead of loading the whole file into RAM.
        # aiohttp does not close user-provided file objects — we own the handle.
        file_obj = open(source, "rb")
        form.add_field(
            "fileToUpload",
            file_obj,
            filename=filename,
            content_type="application/octet-stream",
        )
    else:
        form.add_field(
            "fileToUpload",
            io.BytesIO(source),
            filename=filename,
            content_type="application/octet-stream",
        )
    try:
        async with _UPLOAD_SEMAPHORE:
            session = _get_session()
            async with session.post(CATBOX_URL, data=form, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    if text.startswith("https://"):
                        return text
                    logger.warning("catbox returned 200 but unexpected body: %.200s", text)
                else:
                    logger.warning(
                        "catbox returned HTTP %d for %s: %.200s",
                        resp.status, filename, await resp.text()
                    )
    except asyncio.TimeoutError:
        logger.warning("catbox upload timed out for %s", filename)
    except aiohttp.ClientError as exc:
        logger.warning("catbox upload failed for %s: %s", filename, exc)
    except Exception:
        logger.exception("catbox upload unexpected error for %s", filename)
    finally:
        if file_obj is not None:
            file_obj.close()
    return None
