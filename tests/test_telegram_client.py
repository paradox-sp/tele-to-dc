# tests/test_telegram_client.py
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# telethon isn't installed in the test env — fake the import surface so
# telegram_client can be imported (same pattern as test_main.py).
_telethon = types.ModuleType("telethon")
_telethon.TelegramClient = MagicMock(name="TelegramClient")
_telethon.events = MagicMock(name="events")
sys.modules["telethon"] = _telethon
sys.modules["telethon.events"] = _telethon.events

from config import CatboxConfig, MediaConfig
from message_processor import ForwardPayload
from telegram_client import (
    _album_buffer,
    _album_tasks,
    _buffer_album,
    _cache_path,
    _cancel_cleanup,
    _cleanup_media,
    _clear_album_state,
    _download_media,
    _split_album_messages,
)


def make_media_config(save_to_disk=False, cache_dir="data/media_cache"):
    return MediaConfig(
        max_upload_size_mb=25,
        catbox=CatboxConfig(),
        save_to_disk=save_to_disk,
        cache_dir=cache_dir,
    )


def make_msg(media="photo"):
    msg = MagicMock()
    msg.id = 1
    msg.chat_id = -1001
    msg.text = ""
    msg.caption = None
    msg.poll = None
    msg.photo = MagicMock() if media == "photo" else None
    msg.video = None
    msg.video_note = None
    msg.audio = None
    msg.voice = None
    msg.document = None
    msg.sticker = None
    msg.grouped_id = None
    msg.fwd_from = None
    return msg


def make_payload(attachments=None):
    return ForwardPayload(
        route_name="r",
        chat_name="Chat",
        sender_name="user",
        text="",
        attachments=attachments or [],
    )


def test_cache_path_strips_directories():
    p = _cache_path("cache", "sub/dir/file.mp4")
    assert p == os.path.join("cache", "file.mp4")


def test_cache_path_sanitizes_illegal_chars():
    p = _cache_path("cache", 'a<b>c:d"e|f?g*.mp4')
    assert p.startswith("cache" + os.sep)
    assert p.endswith(".mp4")
    for ch in '<>:"|?*':
        assert ch not in os.path.basename(p)


async def test_clear_album_state_empties_buffers():
    _album_buffer[(1, 2)] = [MagicMock()]
    _album_tasks[(1, 2)] = MagicMock()
    _clear_album_state()
    assert _album_buffer == {}
    assert _album_tasks == {}


async def test_cancel_cleanup_removes_own_entry():
    key = (1, 2)
    _album_buffer[key] = [MagicMock()]
    _album_tasks[key] = asyncio.current_task()
    _cancel_cleanup(key)
    assert key not in _album_buffer
    assert key not in _album_tasks


async def test_cancel_cleanup_preserves_successor_entry():
    # H1: a cancelled flush task must NOT destroy the successor's buffer/registry.
    key = (1, 2)
    _album_buffer[key] = [MagicMock()]
    _album_tasks[key] = MagicMock()  # successor owns the key
    _cancel_cleanup(key)
    assert key in _album_buffer
    assert key in _album_tasks


async def test_download_media_memory_mode_returns_bytes():
    client = AsyncMock()
    client.download_media = AsyncMock(return_value=b"data")
    result = await _download_media(client, make_msg(), make_media_config(save_to_disk=False))
    assert result == b"data"
    assert client.download_media.call_args.args[1] is bytes


async def test_download_media_disk_mode_writes_to_cache(tmp_path):
    client = AsyncMock()
    # MAJOR-2: cache names embed chat_id + message.id so concurrent downloads
    # from different chats (or same-name documents) never collide on one path.
    target = str(tmp_path / "-1001_1_photo_1.jpg")
    client.download_media = AsyncMock(return_value=target)
    result = await _download_media(
        client, make_msg(), make_media_config(save_to_disk=True, cache_dir=str(tmp_path))
    )
    assert result == target
    kwargs = client.download_media.call_args.kwargs
    assert "file" in kwargs
    assert kwargs["file"] == target


async def test_download_media_disk_mode_removes_partial_file_on_error(tmp_path):
    client = AsyncMock()

    async def fail_after_write(m, **kw):
        (tmp_path / "-1001_1_photo_1.jpg").write_bytes(b"partial")
        raise RuntimeError("boom")

    client.download_media = AsyncMock(side_effect=fail_after_write)
    with pytest.raises(RuntimeError):
        await _download_media(
            client, make_msg(), make_media_config(save_to_disk=True, cache_dir=str(tmp_path))
        )
    assert list(tmp_path.iterdir()) == []  # partial download must not be left behind


def test_cleanup_media_deletes_paths_unless_keep(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    _cleanup_media(make_payload([(str(p), "a.jpg")]), keep=False)
    assert not p.exists()


def test_cleanup_media_keeps_when_keep(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    _cleanup_media(make_payload([(str(p), "a.jpg")]), keep=True)
    assert p.exists()


def test_cleanup_media_ignores_bytes():
    _cleanup_media(make_payload([(b"data", "a.jpg")]), keep=False)  # must not raise


def test_split_album_messages_caps_media_at_10():
    msgs = [make_msg(media="photo" if i % 2 == 0 else None) for i in range(25)]
    text_only, media, total = _split_album_messages(msgs)
    assert len(media) == 10
    assert total == 13
    assert len(text_only) == 12


def test_split_album_messages_no_truncation():
    msgs = [make_msg(media="photo") for _ in range(3)]
    text_only, media, total = _split_album_messages(msgs)
    assert len(media) == 3
    assert total == 3
    assert text_only == []


async def test_album_downloads_bounded_by_semaphore():
    # MAJOR-1: album processing must bound concurrent download+upload units
    # (3), so a 10-file album in memory mode can't spike RAM to ~10x file size.
    import telegram_client
    from types import SimpleNamespace

    class TrackingSemaphore:
        def __init__(self, n):
            self._sem = asyncio.Semaphore(n)
            self.active = 0
            self.max_active = 0

        async def __aenter__(self):
            await self._sem.acquire()
            self.active += 1
            self.max_active = max(self.max_active, self.active)

        async def __aexit__(self, *exc):
            self.active -= 1
            self._sem.release()

    tracker = TrackingSemaphore(3)
    original = telegram_client._UPLOAD_SEMAPHORE
    telegram_client._UPLOAD_SEMAPHORE = tracker

    client = AsyncMock()

    async def slow_download(m, *args, **kw):
        await asyncio.sleep(0.02)
        return b"data"

    client.download_media = AsyncMock(side_effect=slow_download)

    sent = []

    async def on_payload(channel_id, payload):
        sent.append(payload)

    msgs = [make_msg(media="photo") for _ in range(10)]
    for m in msgs:
        m.grouped_id = 7
        m.chat_id = -1001

    try:
        for m in msgs:
            await _buffer_album(
                m, -1001, "r", "Chat", "user",
                [123], SimpleNamespace(media=make_media_config()), on_payload, client, False,
            )
        await asyncio.sleep(1.5)  # let the final flush task run
    finally:
        telegram_client._UPLOAD_SEMAPHORE = original

    assert tracker.max_active <= 3
    assert len(sent) == 1
    assert len(sent[0].attachments) == 10