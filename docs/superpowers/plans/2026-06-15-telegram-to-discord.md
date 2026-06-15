# Telegram → Discord Bridge: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight async Python bot that forwards Telegram messages (text, media, files, polls, albums) to Discord channels with flexible many-to-many routing, packaged as a minimal Docker image.

**Architecture:** Single Python process, one shared asyncio event loop, Telethon for Telegram (event-driven, MTProto user API) and discord.py for Discord running concurrently via `asyncio.gather`. Messages flow: Telegram event → message processor → media handler → Discord sender. Config-driven routing in `data/config.yaml` with optional admin slash commands.

**Tech Stack:** Python 3.12, Telethon, discord.py 2.x, aiohttp, PyYAML — Docker Alpine image (~120MB), pytest + pytest-asyncio for tests.

---

## File Map

| File | Created/Modified | Responsibility |
|---|---|---|
| `src/config.py` | Create | Dataclasses, load/save config.yaml, route map builder, add/remove route |
| `src/media_handler.py` | Create | Size check, Discord re-upload, Catbox upload, notice fallback |
| `src/message_processor.py` | Create | Convert Telegram message → ForwardPayload (pure logic, no I/O) |
| `src/telegram_client.py` | Create | Telethon event listener, media download, album buffering |
| `src/discord_client.py` | Create | Send ForwardPayload as Discord embed, admin slash commands |
| `src/main.py` | Create | Entry point, wires all modules, runs event loop |
| `tests/conftest.py` | Create | Add src/ to sys.path for imports |
| `tests/test_config.py` | Create | Config loading, route map, add/remove route |
| `tests/test_media_handler.py` | Create | Size check logic, Catbox fallback, notice fallback |
| `tests/test_message_processor.py` | Create | Text, poll, photo, large file payload building |
| `requirements.txt` | Create | Runtime dependencies |
| `requirements-dev.txt` | Create | Test dependencies |
| `pytest.ini` | Create | asyncio_mode = auto |
| `config.example.yaml` | Create | Template users copy to data/config.yaml |
| `Dockerfile` | Create | Alpine Python 3.12, copies src/, runs main.py |
| `docker-compose.yml` | Create | Mounts ./data, restart: unless-stopped |
| `.gitignore` | Create | Ignore data/, sessions, __pycache__ |
| `.dockerignore` | Create | Exclude docs, tests, data from image |
| `.github/workflows/docker-publish.yml` | Create | Manual trigger, amd64+arm64 → ghcr.io |

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `config.example.yaml`
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `tests/conftest.py`
- Create dirs: `src/`, `tests/`, `data/`, `.github/workflows/`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p src tests data .github/workflows
```

- [ ] **Step 2: Write requirements.txt**

```
telethon
discord.py
pyyaml
aiohttp
```

- [ ] **Step 3: Write requirements-dev.txt**

```
pytest
pytest-asyncio
```

- [ ] **Step 4: Write pytest.ini**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: Write tests/conftest.py**

```python
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
```

- [ ] **Step 6: Write config.example.yaml**

```yaml
telegram:
  api_id: 12345678
  api_hash: "your_api_hash_here"
  session_name: "tg_session"

discord:
  token: "your_discord_bot_token"
  commands_enabled: true

media:
  max_upload_size_mb: 25
  catbox:
    enabled: false
    userhash: ""

routes:
  - name: "example-route"
    from:
      - -1001234567890
    to:
      - 987654321098765432
```

- [ ] **Step 7: Write .gitignore**

```
data/
*.session
.env
__pycache__/
*.pyc
*.pyo
```

- [ ] **Step 8: Write .dockerignore**

```
.github/
data/
docs/
tests/
.gitignore
*.md
__pycache__/
*.pyc
requirements-dev.txt
pytest.ini
```

- [ ] **Step 9: Commit**

```bash
git init
git add requirements.txt requirements-dev.txt pytest.ini config.example.yaml .gitignore .dockerignore tests/conftest.py
git commit -m "chore: project scaffold"
```

---

## Task 2: Config Module

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
import yaml
from config import load_config, save_config, add_route, remove_route, Route


@pytest.fixture
def config_file(tmp_path):
    data = {
        "telegram": {"api_id": 123, "api_hash": "abc", "session_name": "test"},
        "discord": {"token": "tok", "commands_enabled": True},
        "media": {"max_upload_size_mb": 25, "catbox": {"enabled": False, "userhash": ""}},
        "routes": [
            {"name": "r1", "from": [-100111], "to": [999]},
            {"name": "r2", "from": [-100111, -100222], "to": [888, 777]},
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data))
    return str(path)


def test_load_telegram_fields(config_file):
    config = load_config(config_file)
    assert config.telegram.api_id == 123
    assert config.telegram.api_hash == "abc"
    assert config.telegram.session_name == "test"


def test_load_discord_fields(config_file):
    config = load_config(config_file)
    assert config.discord.token == "tok"
    assert config.discord.commands_enabled is True


def test_load_media_fields(config_file):
    config = load_config(config_file)
    assert config.media.max_upload_size_mb == 25
    assert config.media.catbox.enabled is False
    assert config.media.catbox.userhash == ""


def test_load_routes(config_file):
    config = load_config(config_file)
    assert len(config.routes) == 2
    assert config.routes[0].name == "r1"
    assert config.routes[0].from_chats == [-100111]
    assert config.routes[0].to_channels == [999]


def test_route_map_merges_overlapping_sources(config_file):
    config = load_config(config_file)
    # -100111 appears in both r1 and r2
    assert set(config.route_map[-100111]) == {999, 888, 777}


def test_route_map_single_source(config_file):
    config = load_config(config_file)
    assert set(config.route_map[-100222]) == {888, 777}


def test_add_route_persists(config_file):
    config = load_config(config_file)
    new_route = Route(name="r3", from_chats=[-100333], to_channels=[666])
    add_route(config, new_route, config_file)
    reloaded = load_config(config_file)
    assert any(r.name == "r3" for r in reloaded.routes)
    assert 666 in reloaded.route_map[-100333]


def test_remove_route_existing(config_file):
    config = load_config(config_file)
    removed = remove_route(config, "r1", config_file)
    assert removed is True
    reloaded = load_config(config_file)
    assert all(r.name != "r1" for r in reloaded.routes)


def test_remove_route_nonexistent(config_file):
    config = load_config(config_file)
    removed = remove_route(config, "ghost", config_file)
    assert removed is False
    assert len(config.routes) == 2
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd D:\Space\telegram-to-discord
python -m pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write src/config.py**

```python
from dataclasses import dataclass, field
from typing import Dict, List
import yaml


@dataclass
class CatboxConfig:
    enabled: bool = False
    userhash: str = ""


@dataclass
class MediaConfig:
    max_upload_size_mb: int = 25
    catbox: CatboxConfig = field(default_factory=CatboxConfig)


@dataclass
class TelegramConfig:
    api_id: int = 0
    api_hash: str = ""
    session_name: str = "tg_session"


@dataclass
class DiscordConfig:
    token: str = ""
    commands_enabled: bool = True


@dataclass
class Route:
    name: str
    from_chats: List[int]
    to_channels: List[int]


@dataclass
class AppConfig:
    telegram: TelegramConfig
    discord: DiscordConfig
    media: MediaConfig
    routes: List[Route]
    route_map: Dict[int, List[int]] = field(default_factory=dict)


def _build_route_map(routes: List[Route]) -> Dict[int, List[int]]:
    route_map: Dict[int, List[int]] = {}
    for route in routes:
        for chat_id in route.from_chats:
            if chat_id not in route_map:
                route_map[chat_id] = []
            for channel_id in route.to_channels:
                if channel_id not in route_map[chat_id]:
                    route_map[chat_id].append(channel_id)
    return route_map


def load_config(path: str = "data/config.yaml") -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    tg = data["telegram"]
    dc = data["discord"]
    media_data = data.get("media", {})
    catbox_data = media_data.get("catbox", {})

    routes = [
        Route(
            name=r["name"],
            from_chats=[int(c) for c in r["from"]],
            to_channels=[int(c) for c in r["to"]],
        )
        for r in data.get("routes", [])
    ]

    return AppConfig(
        telegram=TelegramConfig(
            api_id=int(tg["api_id"]),
            api_hash=str(tg["api_hash"]),
            session_name=tg.get("session_name", "tg_session"),
        ),
        discord=DiscordConfig(
            token=str(dc["token"]),
            commands_enabled=bool(dc.get("commands_enabled", True)),
        ),
        media=MediaConfig(
            max_upload_size_mb=int(media_data.get("max_upload_size_mb", 25)),
            catbox=CatboxConfig(
                enabled=bool(catbox_data.get("enabled", False)),
                userhash=str(catbox_data.get("userhash", "")),
            ),
        ),
        routes=routes,
        route_map=_build_route_map(routes),
    )


def save_config(config: AppConfig, path: str = "data/config.yaml") -> None:
    data = {
        "telegram": {
            "api_id": config.telegram.api_id,
            "api_hash": config.telegram.api_hash,
            "session_name": config.telegram.session_name,
        },
        "discord": {
            "token": config.discord.token,
            "commands_enabled": config.discord.commands_enabled,
        },
        "media": {
            "max_upload_size_mb": config.media.max_upload_size_mb,
            "catbox": {
                "enabled": config.media.catbox.enabled,
                "userhash": config.media.catbox.userhash,
            },
        },
        "routes": [
            {"name": r.name, "from": r.from_chats, "to": r.to_channels}
            for r in config.routes
        ],
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def add_route(config: AppConfig, route: Route, path: str = "data/config.yaml") -> None:
    config.routes.append(route)
    config.route_map = _build_route_map(config.routes)
    save_config(config, path)


def remove_route(config: AppConfig, name: str, path: str = "data/config.yaml") -> bool:
    before = len(config.routes)
    config.routes = [r for r in config.routes if r.name != name]
    if len(config.routes) == before:
        return False
    config.route_map = _build_route_map(config.routes)
    save_config(config, path)
    return True
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: config module with load, save, add/remove route"
```

---

## Task 3: Media Handler

**Files:**
- Create: `src/media_handler.py`
- Create: `tests/test_media_handler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_media_handler.py
import pytest
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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_media_handler.py -v
```

Expected: `ModuleNotFoundError: No module named 'media_handler'`

- [ ] **Step 3: Write src/media_handler.py**

```python
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_media_handler.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/media_handler.py tests/test_media_handler.py
git commit -m "feat: media handler with size check, Catbox upload, notice fallback"
```

---

## Task 4: Message Processor

**Files:**
- Create: `src/message_processor.py`
- Create: `tests/test_message_processor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_message_processor.py
import pytest
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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_message_processor.py -v
```

Expected: `ModuleNotFoundError: No module named 'message_processor'`

- [ ] **Step 3: Write src/message_processor.py**

```python
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Tuple

from config import MediaConfig
from media_handler import handle_media


@dataclass
class ForwardPayload:
    route_name: str
    chat_name: str
    sender_name: str
    text: str
    forward_from: str = ""              # non-empty if message was forwarded
    attachments: List[Tuple[bytes, str]] = field(default_factory=list)
    catbox_urls: List[str] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)


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

    has_media = any([message.photo, message.video, message.audio, message.voice, message.document, message.sticker])
    if has_media and download_fn:
        filename = _get_filename(message)
        file_bytes = await download_fn(message)
        if file_bytes:
            result = await handle_media(file_bytes, filename, media_config)
            if result.data:
                payload.attachments.append((result.data, result.filename))
            elif result.catbox_url:
                payload.catbox_urls.append(result.catbox_url)
            elif result.notice:
                payload.notices.append(result.notice)

    return payload


def _format_poll(poll) -> str:
    options = "\n".join(f"• {opt.text}" for opt in poll.answers)
    return f"📊 **{poll.question}**\n{options}"


def _get_filename(message) -> str:
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
    return f"file_{message.id}"


def _doc_filename(message, default: str) -> str:
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                return attr.file_name
    return default
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_message_processor.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Run all tests to check nothing broke**

```bash
python -m pytest tests/ -v
```

Expected: `21 passed`

- [ ] **Step 6: Commit**

```bash
git add src/message_processor.py tests/test_message_processor.py
git commit -m "feat: message processor converts Telegram messages to ForwardPayload"
```

---

## Task 5: Telegram Client

**Files:**
- Create: `src/telegram_client.py`

No unit tests — this module is pure integration with Telethon. Verified manually at the end.

- [ ] **Step 1: Write src/telegram_client.py**

```python
import asyncio
import logging
from typing import Awaitable, Callable, Dict, List

from telethon import TelegramClient, events

from config import AppConfig
from message_processor import ForwardPayload, process_message

logger = logging.getLogger(__name__)

# Buffers for collecting album (grouped) messages before forwarding
_album_buffer: Dict[int, List] = {}
_album_tasks: Dict[int, asyncio.Task] = {}


def create_telegram_client(
    config: AppConfig,
    on_payload: Callable[[int, ForwardPayload], Awaitable[None]],
) -> TelegramClient:
    session_path = f"data/{config.telegram.session_name}"
    client = TelegramClient(session_path, config.telegram.api_id, config.telegram.api_hash)

    watched = list(config.route_map.keys())
    if not watched:
        logger.warning("No Telegram sources in route_map — not listening to any chats.")
        return client

    @client.on(events.NewMessage(chats=watched))
    async def _handle(event: events.NewMessage.Event):
        chat_id = event.chat_id
        channel_ids = config.route_map.get(chat_id)
        if not channel_ids:
            return

        message = event.message
        chat_name = await _get_chat_name(event, chat_id)
        sender_name = await _get_sender_name(event, chat_name)
        route_name = _find_route_name(config, chat_id)

        if message.grouped_id:
            await _buffer_album(message, route_name, chat_name, sender_name, channel_ids, config, on_payload, client)
            return

        async def download(m):
            return await client.download_media(m, bytes)

        payload = await process_message(message, route_name, chat_name, sender_name, config.media, download_fn=download)
        for channel_id in channel_ids:
            await on_payload(channel_id, payload)

    return client


async def _get_chat_name(event, fallback: int) -> str:
    try:
        chat = await event.get_chat()
        return getattr(chat, "title", None) or getattr(chat, "username", None) or str(fallback)
    except Exception:
        return str(fallback)


async def _get_sender_name(event, fallback: str) -> str:
    try:
        sender = await event.get_sender()
        if not sender:
            return fallback
        username = getattr(sender, "username", None)
        if username:
            return f"@{username}"
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        return (f"{first} {last}").strip() or fallback
    except Exception:
        return fallback


def _find_route_name(config: AppConfig, chat_id: int) -> str:
    for route in config.routes:
        if chat_id in route.from_chats:
            return route.name
    return str(chat_id)


async def _buffer_album(message, route_name, chat_name, sender_name, channel_ids, config, on_payload, client):
    grouped_id = message.grouped_id
    _album_buffer.setdefault(grouped_id, []).append(message)

    if grouped_id in _album_tasks:
        _album_tasks[grouped_id].cancel()

    async def flush():
        await asyncio.sleep(1.0)
        messages = _album_buffer.pop(grouped_id, [])
        _album_tasks.pop(grouped_id, None)

        combined = ForwardPayload(
            route_name=route_name,
            chat_name=chat_name,
            sender_name=sender_name,
            text=next((m.text or m.caption or "" for m in messages if (m.text or m.caption)), ""),
        )

        async def download(m):
            return await client.download_media(m, bytes)

        for msg in messages:
            part = await process_message(msg, route_name, chat_name, sender_name, config.media, download_fn=download)
            combined.attachments.extend(part.attachments)
            combined.catbox_urls.extend(part.catbox_urls)
            combined.notices.extend(part.notices)

        for channel_id in channel_ids:
            await on_payload(channel_id, combined)

    _album_tasks[grouped_id] = asyncio.create_task(flush())
```

- [ ] **Step 2: Commit**

```bash
git add src/telegram_client.py
git commit -m "feat: Telegram client with event listener and album buffering"
```

---

## Task 6: Discord Client

**Files:**
- Create: `src/discord_client.py`

No unit tests — integration with discord.py. Verified manually at the end.

- [ ] **Step 1: Write src/discord_client.py**

```python
import io
import logging
from typing import Callable, Awaitable, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import AppConfig, Route, add_route, remove_route
from message_processor import ForwardPayload

logger = logging.getLogger(__name__)


def create_discord_client(config: AppConfig) -> Tuple[commands.Bot, Callable[[int, ForwardPayload], Awaitable[None]]]:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        if config.discord.commands_enabled:
            await bot.tree.sync()
            logger.info("Slash commands synced")
        logger.info("Discord bot ready: %s", bot.user)

    if config.discord.commands_enabled:
        _register_commands(bot, config)

    async def send_payload(channel_id: int, payload: ForwardPayload) -> None:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning("Channel %d not found or not cached", channel_id)
            return

        embed = discord.Embed(color=0x2CA5E0)
        embed.set_author(name=f"📢 {payload.route_name}")
        embed.add_field(name=payload.chat_name, value=payload.sender_name or "​", inline=True)

        if payload.forward_from:
            embed.add_field(name="​", value=payload.forward_from, inline=False)

        if payload.text:
            embed.description = payload.text[:4096]

        for url in payload.catbox_urls:
            embed.add_field(name="File", value=url, inline=False)

        for notice in payload.notices:
            embed.add_field(name="Notice", value=notice, inline=False)

        files = [
            discord.File(fp=io.BytesIO(data), filename=fname)
            for data, fname in payload.attachments[:10]
        ]

        try:
            await channel.send(embed=embed, files=files or discord.utils.MISSING)
        except discord.HTTPException as e:
            logger.error("Failed to send to channel %d: %s", channel_id, e)

    return bot, send_payload


def _register_commands(bot: commands.Bot, config: AppConfig) -> None:
    @bot.tree.command(name="route", description="Manage forwarding routes")
    @app_commands.describe(
        action="list · add · remove",
        name="Route name",
        telegram_id="Telegram chat ID (negative number)",
        discord_channel="Discord channel ID",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def route_cmd(
        interaction: discord.Interaction,
        action: str,
        name: str = "",
        telegram_id: str = "",
        discord_channel: str = "",
    ):
        if action == "list":
            if not config.routes:
                await interaction.response.send_message("No routes configured.", ephemeral=True)
                return
            lines = [f"**{r.name}**: `{r.from_chats}` → `{r.to_channels}`" for r in config.routes]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        elif action == "add":
            if not name or not telegram_id or not discord_channel:
                await interaction.response.send_message(
                    "Required: name, telegram_id, discord_channel", ephemeral=True
                )
                return
            route = Route(name=name, from_chats=[int(telegram_id)], to_channels=[int(discord_channel)])
            add_route(config, route)
            await interaction.response.send_message(f"Route **{name}** added.", ephemeral=True)

        elif action == "remove":
            if not name:
                await interaction.response.send_message("Provide a route name.", ephemeral=True)
                return
            if remove_route(config, name):
                await interaction.response.send_message(f"Route **{name}** removed.", ephemeral=True)
            else:
                await interaction.response.send_message(f"Route **{name}** not found.", ephemeral=True)

        else:
            await interaction.response.send_message("Unknown action. Use: list / add / remove", ephemeral=True)

    @route_cmd.error
    async def route_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("Administrator permission required.", ephemeral=True)
```

- [ ] **Step 2: Commit**

```bash
git add src/discord_client.py
git commit -m "feat: Discord client with embed sender and admin slash commands"
```

---

## Task 7: Main Entry Point

**Files:**
- Create: `src/main.py`

- [ ] **Step 1: Write src/main.py**

```python
import asyncio
import logging
import os
import sys

from config import load_config
from discord_client import create_discord_client
from telegram_client import create_telegram_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("CONFIG_PATH", "data/config.yaml")


async def main() -> None:
    logger.info("Loading config from %s", CONFIG_PATH)
    try:
        config = load_config(CONFIG_PATH)
    except FileNotFoundError:
        logger.error("Config not found at %s — copy config.example.yaml to data/config.yaml", CONFIG_PATH)
        sys.exit(1)
    except Exception as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    if not config.route_map:
        logger.warning("No routes configured — bot will run but forward nothing.")

    logger.info("Loaded %d route(s)", len(config.routes))

    discord_bot, send_payload = create_discord_client(config)
    tg_client = create_telegram_client(config, send_payload)

    logger.info("Connecting to Telegram (first run will prompt for phone number)...")
    await tg_client.start()
    logger.info("Telegram connected.")

    logger.info("Starting Discord bot...")
    async with discord_bot:
        await asyncio.gather(
            discord_bot.start(config.discord.token),
            tg_client.run_until_disconnected(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down.")
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: main entry point wiring Telegram and Discord clients"
```

---

## Task 8: Docker Setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ .
CMD ["python", "main.py"]
```

- [ ] **Step 2: Write docker-compose.yml**

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    environment:
      - CONFIG_PATH=/app/data/config.yaml
```

- [ ] **Step 3: Verify Docker build succeeds**

```bash
docker build -t tg-discord-test .
```

Expected: build completes, image tagged `tg-discord-test`

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "chore: Dockerfile and docker-compose for containerized deployment"
```

---

## Task 9: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/docker-publish.yml`

- [ ] **Step 1: Write .github/workflows/docker-publish.yml**

```yaml
name: Build and Push Docker Image

on:
  workflow_dispatch:
    inputs:
      tag:
        description: "Image tag (e.g. latest, v1.0.0)"
        required: false
        default: "latest"

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.event.inputs.tag || 'latest' }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "ci: manual GitHub Actions workflow to build and push to ghcr.io"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: `21 passed`, 0 failures

- [ ] **Verify project structure matches spec**

```bash
find . -not -path './.git/*' -not -path './data/*' | sort
```

Expected output includes: `src/`, `tests/`, `.github/workflows/docker-publish.yml`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `config.example.yaml`, `.gitignore`, `.dockerignore`

- [ ] **First-run smoke test (manual)**

```bash
# 1. copy example config
cp config.example.yaml data/config.yaml
# fill in real api_id, api_hash, discord token, and at least one route

# 2. first-run auth (interactive)
docker compose run -it bot

# 3. headless from now on
docker compose up -d

# 4. send a message in the Telegram source — it should appear in Discord within seconds
```
