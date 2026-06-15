import io
from dataclasses import dataclass
from typing import Optional

import aiohttp

from config import MediaConfig

CATBOX_URL = "https://catbox.moe/user/api.php"


@dataclass
class MediaResult:
    data: Optional[bytes]
    filename: str
    catbox_url: Optional[str]
    notice: Optional[str]


async def handle_media(file_bytes: bytes, filename: str, config: MediaConfig) -> MediaResult:
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb <= config.max_upload_size_mb:
        return MediaResult(data=file_bytes, filename=filename, catbox_url=None, notice=None)

    if config.catbox.enabled:
        url = await _upload_to_catbox(file_bytes, filename, config.catbox.userhash)
        if url:
            return MediaResult(data=None, filename=filename, catbox_url=url, notice=None)

    notice = f"⚠️ File too large to forward: {filename} ({size_mb:.1f} MB)"
    return MediaResult(data=None, filename=filename, catbox_url=None, notice=notice)


async def _upload_to_catbox(file_bytes: bytes, filename: str, userhash: str = "") -> Optional[str]:
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
        async with aiohttp.ClientSession() as session:
            async with session.post(CATBOX_URL, data=form) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    if text.startswith("https://"):
                        return text
    except aiohttp.ClientError:
        pass
    return None
