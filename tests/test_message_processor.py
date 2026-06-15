# tests/test_message_processor.py
from unittest.mock import MagicMock, AsyncMock, patch
from message_processor import process_message, ForwardPayload
from config import MediaConfig, CatboxConfig


def make_config(max_mb=25):
    return MediaConfig(max_upload_size_mb=max_mb, catbox=CatboxConfig())


def make_msg(text="", caption=None):
    msg = MagicMock()
    msg.id = 1
    msg.text = text
    msg.caption = caption
    msg.poll = None
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.voice = None
    msg.document = None
    msg.sticker = None
    msg.grouped_id = None
    msg.fwd_from = None
    return msg


async def test_plain_text_message():
    msg = make_msg(text="Hello world")
    payload = await process_message(msg, "route", "Chat", "@user", make_config())
    assert payload.text == "Hello world"
    assert payload.route_name == "route"
    assert payload.chat_name == "Chat"
    assert payload.sender_name == "@user"
    assert payload.attachments == []
    assert payload.catbox_urls == []
    assert payload.notices == []


async def test_uses_caption_when_no_text():
    msg = make_msg(text="", caption="A caption")
    payload = await process_message(msg, "r", "Chat", "user", make_config())
    assert payload.text == "A caption"


async def test_empty_message_has_empty_text():
    msg = make_msg(text="", caption=None)
    payload = await process_message(msg, "r", "Chat", "user", make_config())
    assert payload.text == ""


async def test_poll_formatted_correctly():
    msg = make_msg()
    poll = MagicMock()
    poll.poll.question = "Best language?"
    poll.poll.answers = [MagicMock(text="Python"), MagicMock(text="Go")]
    msg.poll = poll
    payload = await process_message(msg, "r", "Chat", "user", make_config())
    assert "Best language?" in payload.text
    assert "Python" in payload.text
    assert "Go" in payload.text
    assert "📊" in payload.text
    assert payload.attachments == []


async def test_photo_under_limit_attached():
    msg = make_msg(caption="nice photo")
    msg.photo = MagicMock()
    small = b"x" * (1024 * 1024 * 5)

    async def dl(m):
        return small

    payload = await process_message(msg, "r", "Chat", "user", make_config(), download_fn=dl)
    assert len(payload.attachments) == 1
    assert payload.attachments[0][0] == small
    assert payload.catbox_urls == []
    assert payload.notices == []


async def test_photo_over_limit_no_catbox_gives_notice():
    msg = make_msg()
    msg.photo = MagicMock()
    big = b"x" * (1024 * 1024 * 30)

    async def dl(m):
        return big

    payload = await process_message(msg, "r", "Chat", "user", make_config(max_mb=25), download_fn=dl)
    assert payload.attachments == []
    assert len(payload.notices) == 1
    assert "too large" in payload.notices[0]


async def test_no_download_fn_skips_media():
    msg = make_msg()
    msg.photo = MagicMock()
    payload = await process_message(msg, "r", "Chat", "user", make_config(), download_fn=None)
    assert payload.attachments == []
    assert payload.notices == []


async def test_forwarded_message_sets_forward_from():
    msg = make_msg(text="forwarded content")
    msg.fwd_from = MagicMock()
    msg.fwd_from.from_name = "Original Sender"
    payload = await process_message(msg, "r", "Chat", "user", make_config())
    assert "Original Sender" in payload.forward_from
    assert "↩️" in payload.forward_from


async def test_forwarded_message_no_name_shows_generic():
    msg = make_msg(text="forwarded")
    msg.fwd_from = MagicMock()
    msg.fwd_from.from_name = None
    payload = await process_message(msg, "r", "Chat", "user", make_config())
    assert payload.forward_from != ""
    assert "↩️" in payload.forward_from
