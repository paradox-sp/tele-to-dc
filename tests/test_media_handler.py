# tests/test_media_handler.py
from unittest.mock import AsyncMock, patch
from media_handler import handle_media
from config import MediaConfig, CatboxConfig


def make_config(max_mb=25, catbox_enabled=False, userhash=""):
    return MediaConfig(
        max_upload_size_mb=max_mb,
        catbox=CatboxConfig(enabled=catbox_enabled, userhash=userhash),
    )


async def test_small_file_returned_as_attachment():
    data = b"x" * (1024 * 1024 * 5)  # 5 MB
    result = await handle_media(data, "clip.mp4", make_config(max_mb=25))
    assert result.data == data
    assert result.catbox_url is None
    assert result.notice is None


async def test_large_file_catbox_disabled_returns_notice():
    data = b"x" * (1024 * 1024 * 30)  # 30 MB
    result = await handle_media(data, "big.mp4", make_config(max_mb=25))
    assert result.data is None
    assert result.catbox_url is None
    assert "big.mp4" in result.notice
    assert "30.0 MB" in result.notice


async def test_large_file_catbox_enabled_uploads():
    data = b"x" * (1024 * 1024 * 30)  # 30 MB
    config = make_config(max_mb=25, catbox_enabled=True)

    with patch("media_handler._upload_to_catbox", new=AsyncMock(return_value="https://files.catbox.moe/abc.mp4")):
        result = await handle_media(data, "big.mp4", config)

    assert result.data is None
    assert result.catbox_url == "https://files.catbox.moe/abc.mp4"
    assert result.notice is None


async def test_large_file_catbox_upload_fails_returns_notice():
    data = b"x" * (1024 * 1024 * 30)  # 30 MB
    config = make_config(max_mb=25, catbox_enabled=True)

    with patch("media_handler._upload_to_catbox", new=AsyncMock(return_value=None)):
        result = await handle_media(data, "big.mp4", config)

    assert result.data is None
    assert result.notice is not None
    assert "big.mp4" in result.notice


async def test_file_exactly_at_limit_is_uploaded():
    data = b"x" * (1024 * 1024 * 25)  # exactly 25 MB
    result = await handle_media(data, "exact.mp4", make_config(max_mb=25))
    assert result.data == data
