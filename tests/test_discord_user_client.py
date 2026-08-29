# tests/test_discord_user_client.py
"""Tests for discord_user_client.py: media filtering, forwarded-ID tracking, and persistence."""
import json
import os
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers: reset module-level state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset discord_user_client module globals before each test.

    Also patches _schedule_save_if_needed so _mark_message_forwarded works
    without a running event loop (the periodic-save task is irrelevant in tests).
    """
    import discord_user_client as mod
    mod._forwarded_ids.clear()
    mod._save_pending = False
    if mod._save_task is not None and not mod._save_task.done():
        mod._save_task.cancel()
    mod._save_task = None
    monkeypatch.setattr(mod, "_schedule_save_if_needed", lambda: None)
    yield
    # cleanup after
    if mod._save_task is not None and not mod._save_task.done():
        mod._save_task.cancel()
    mod._save_task = None


def _make_attachment(content_type: str) -> MagicMock:
    att = MagicMock()
    att.content_type = content_type
    return att


def _make_message(attachments: list[MagicMock]) -> MagicMock:
    msg = MagicMock()
    msg.attachments = attachments
    return msg


# ---------------------------------------------------------------------------
# _has_media tests
# ---------------------------------------------------------------------------

class TestHasMedia:
    def test_image_attachment_matches_image_type(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("image/png")])
        assert _has_media(msg, ["image"]) is True

    def test_video_attachment_matches_video_type(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("video/mp4")])
        assert _has_media(msg, ["video"]) is True

    def test_image_and_video_types_match_both(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("image/jpeg")])
        assert _has_media(msg, ["image", "video"]) is True

    def test_no_attachments_returns_false(self):
        from discord_user_client import _has_media
        msg = _make_message([])
        assert _has_media(msg, ["image"]) is False

    def test_wrong_media_type_returns_false(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("application/pdf")])
        assert _has_media(msg, ["image"]) is False

    def test_video_not_in_types_returns_false(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("video/mp4")])
        assert _has_media(msg, ["image"]) is False

    def test_none_content_type_treated_as_empty(self):
        from discord_user_client import _has_media
        att = _make_attachment(None)
        att.content_type = None
        msg = _make_message([att])
        assert _has_media(msg, ["image"]) is False

    def test_gif_excluded_by_default(self):
        """GIFs are excluded unless 'gif' is explicitly in media_types."""
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("image/gif")])
        assert _has_media(msg, ["image"]) is False

    def test_gif_included_when_explicit(self):
        from discord_user_client import _has_media
        msg = _make_message([_make_attachment("image/gif")])
        assert _has_media(msg, ["image", "gif"]) is True

    def test_first_matching_attachment_wins(self):
        from discord_user_client import _has_media
        atts = [_make_attachment("application/octet-stream"), _make_attachment("image/webp")]
        msg = _make_message(atts)
        assert _has_media(msg, ["image"]) is True


# ---------------------------------------------------------------------------
# Forwarded-ID tracking tests
# ---------------------------------------------------------------------------

class TestForwardedIdTracking:
    def test_is_message_forwarded_returns_false_for_unknown(self):
        from discord_user_client import _is_message_forwarded
        assert _is_message_forwarded(100, 200) is False

    def test_mark_and_check(self):
        from discord_user_client import _mark_message_forwarded, _is_message_forwarded
        _mark_message_forwarded(100, 200)
        assert _is_message_forwarded(100, 200) is True

    def test_different_channel_not_affected(self):
        from discord_user_client import _mark_message_forwarded, _is_message_forwarded
        _mark_message_forwarded(100, 200)
        assert _is_message_forwarded(999, 200) is False

    def test_different_message_not_affected(self):
        from discord_user_client import _mark_message_forwarded, _is_message_forwarded
        _mark_message_forwarded(100, 200)
        assert _is_message_forwarded(100, 300) is False

    def test_multiple_messages_in_channel(self):
        from discord_user_client import _mark_message_forwarded, _is_message_forwarded
        _mark_message_forwarded(100, 1)
        _mark_message_forwarded(100, 2)
        _mark_message_forwarded(100, 3)
        assert _is_message_forwarded(100, 1) is True
        assert _is_message_forwarded(100, 2) is True
        assert _is_message_forwarded(100, 3) is True

    def test_trims_old_entries_at_10k(self):
        from discord_user_client import _mark_message_forwarded, _is_message_forwarded, _forwarded_ids
        # Add 10001 entries to channel 1
        for i in range(10001):
            _mark_message_forwarded(1, i)
        # Oldest 5000 should be trimmed, keeping ~5001
        assert len(_forwarded_ids[1]) == 5001
        # Oldest entries gone
        assert _is_message_forwarded(1, 0) is False
        assert _is_message_forwarded(1, 1) is False
        # Recent entries kept
        assert _is_message_forwarded(1, 10000) is True
        assert _is_message_forwarded(1, 5000) is True


# ---------------------------------------------------------------------------
# Persistence tests (load / save round-trip)
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        from discord_user_client import _save_forwarded_ids, _load_forwarded_ids, _mark_message_forwarded
        import discord_user_client as mod
        orig = mod._FORWARDED_IDS_FILE
        mod._FORWARDED_IDS_FILE = str(tmp_path / "forwarded_ids.json")
        try:
            _mark_message_forwarded(100, 1)
            _mark_message_forwarded(100, 2)
            _mark_message_forwarded(200, 99)
            _save_forwarded_ids()

            # Clear and reload
            mod._forwarded_ids.clear()
            _load_forwarded_ids()

            from discord_user_client import _is_message_forwarded
            assert _is_message_forwarded(100, 1) is True
            assert _is_message_forwarded(100, 2) is True
            assert _is_message_forwarded(200, 99) is True
            assert _is_message_forwarded(100, 3) is False
        finally:
            mod._FORWARDED_IDS_FILE = orig

    def test_load_missing_file_is_noop(self, tmp_path):
        from discord_user_client import _load_forwarded_ids, _forwarded_ids
        import discord_user_client as mod
        orig = mod._FORWARDED_IDS_FILE
        mod._FORWARDED_IDS_FILE = str(tmp_path / "nonexistent.json")
        try:
            _forwarded_ids.clear()
            _load_forwarded_ids()
            assert len(_forwarded_ids) == 0
        finally:
            mod._FORWARDED_IDS_FILE = orig

    def test_load_corrupt_file_is_noop(self, tmp_path):
        from discord_user_client import _load_forwarded_ids, _forwarded_ids
        import discord_user_client as mod
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json {{{")
        orig = mod._FORWARDED_IDS_FILE
        mod._FORWARDED_IDS_FILE = str(path)
        try:
            _forwarded_ids.clear()
            _load_forwarded_ids()
            assert len(_forwarded_ids) == 0
        finally:
            mod._FORWARDED_IDS_FILE = orig

    def test_atomic_write_no_tmp_left_behind(self, tmp_path):
        from discord_user_client import _save_forwarded_ids, _mark_message_forwarded, _forwarded_ids
        import discord_user_client as mod
        path = str(tmp_path / "forwarded_ids.json")
        orig = mod._FORWARDED_IDS_FILE
        mod._FORWARDED_IDS_FILE = path
        try:
            _forwarded_ids.clear()
            _mark_message_forwarded(1, 1)
            _save_forwarded_ids()
            assert os.path.exists(path)
            assert not os.path.exists(path + ".tmp")
            # Verify content is valid JSON
            with open(path) as f:
                data = json.load(f)
            assert "1" in data
            assert 1 in data["1"]
        finally:
            mod._FORWARDED_IDS_FILE = orig

    def test_empty_state_writes_valid_json(self, tmp_path):
        from discord_user_client import _save_forwarded_ids, _forwarded_ids
        import discord_user_client as mod
        path = str(tmp_path / "forwarded_ids.json")
        orig = mod._FORWARDED_IDS_FILE
        mod._FORWARDED_IDS_FILE = path
        try:
            _forwarded_ids.clear()
            _save_forwarded_ids()
            # Empty state writes {} to the file
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data == {}
        finally:
            mod._FORWARDED_IDS_FILE = orig
