# tests/test_media_handler.py
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from media_handler import handle_media, _upload_to_catbox
from config import MediaConfig, CatboxConfig


def make_config(max_mb=25, catbox_enabled=False, userhash="", max_file_size_mb=200):
    return MediaConfig(
        max_upload_size_mb=max_mb,
        max_file_size_mb=max_file_size_mb,
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
    assert "Enable catbox" in result.notice


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
    assert "Failed to upload to catbox" in result.notice


async def test_file_exactly_at_limit_is_uploaded():
    data = b"x" * (1024 * 1024 * 25)  # exactly 25 MB
    result = await handle_media(data, "exact.mp4", make_config(max_mb=25))
    assert result.data == data


async def test_upload_to_catbox_returns_url_on_success():
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="https://files.catbox.moe/abc123.mp4\n")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("media_handler._get_session", return_value=mock_session):
        result = await _upload_to_catbox(b"data", "test.mp4", "")

    assert result == "https://files.catbox.moe/abc123.mp4"


async def test_upload_to_catbox_returns_none_on_non_200():
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("media_handler._get_session", return_value=mock_session):
        result = await _upload_to_catbox(b"data", "test.mp4", "")

    assert result is None


async def test_upload_to_catbox_returns_none_on_client_error():
    mock_session = MagicMock()
    mock_session.post = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("media_handler._get_session", return_value=mock_session):
        result = await _upload_to_catbox(b"data", "test.mp4", "")

    assert result is None


async def test_file_over_catbox_limit_returns_notice():
    data = b"x" * (1024 * 1024 * 250)  # 250 MB
    config = make_config(max_mb=25, catbox_enabled=True, max_file_size_mb=300)

    result = await handle_media(data, "big.mp4", config)

    assert result.data is None
    assert result.catbox_url is None
    assert result.notice is not None
    assert "too large for catbox" in result.notice
    assert "big.mp4" in result.notice


async def test_file_between_discord_and_catbox_limit_uses_catbox():
    data = b"x" * (1024 * 1024 * 100)  # 100 MB
    config = make_config(max_mb=25, catbox_enabled=True)

    with patch("media_handler._upload_to_catbox", new=AsyncMock(return_value="https://files.catbox.moe/abc.mp4")):
        result = await handle_media(data, "big.mp4", config)

    assert result.data is None
    assert result.catbox_url == "https://files.catbox.moe/abc.mp4"
    assert result.notice is None


async def test_file_over_hard_cap_skipped():
    data = b"x" * (1024 * 1024 * 250)  # 250 MB
    config = make_config(max_mb=25, catbox_enabled=True, max_file_size_mb=100)

    result = await handle_media(data, "big.mp4", config)

    assert result.data is None
    assert result.catbox_url is None
    assert result.notice is not None
    assert "memory exhaustion" in result.notice
    assert "big.mp4" in result.notice
