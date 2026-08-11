# tests/test_discord_client.py
"""Tests for discord_client.py.

discord.py is not installed in the test environment, so a fake `discord`
package tree is installed into sys.modules before importing the module under
test. No live Discord connection is ever made.
"""
import io
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _install_fake_discord():
    """Install a fake `discord` package tree into sys.modules."""
    discord = types.ModuleType("discord")
    discord.File = MagicMock(name="File")
    discord.Embed = MagicMock(name="Embed")
    # Match real discord.py: NotFound/Forbidden are subclasses of HTTPException.
    discord.HTTPException = type("HTTPException", (Exception,), {})
    discord.NotFound = type("NotFound", (discord.HTTPException,), {})
    discord.Forbidden = type("Forbidden", (discord.HTTPException,), {})
    discord.Intents = MagicMock(name="Intents")
    discord.Interaction = MagicMock(name="Interaction")
    discord.utils = MagicMock(name="utils")
    discord.utils.MISSING = object()

    app_commands = types.ModuleType("discord.app_commands")
    app_commands.describe = MagicMock(side_effect=lambda **kw: (lambda f: f))
    app_commands.checks = MagicMock(name="checks")
    app_commands.checks.has_permissions = lambda **kw: (lambda f: f)
    app_commands.AppCommandError = type("AppCommandError", (Exception,), {})
    app_commands.MissingPermissions = type(
        "MissingPermissions", (app_commands.AppCommandError,), {}
    )

    ext = types.ModuleType("discord.ext")
    commands = types.ModuleType("discord.ext.commands")
    commands.Bot = MagicMock(name="Bot")

    sys.modules["discord"] = discord
    sys.modules["discord.app_commands"] = app_commands
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands
    return discord, app_commands, commands


discord, app_commands, commands = _install_fake_discord()

from discord_client import create_discord_client, _channel_cache
from config import AppConfig, DiscordConfig, MediaConfig, TelegramConfig
from message_processor import ForwardPayload


class FakeCommand:
    """Stand-in for a discord.py Command: callable + .error decorator."""

    def __init__(self, fn):
        self.fn = fn
        self.error_handler = None

    def error(self, handler):
        self.error_handler = handler
        return handler

    async def __call__(self, *args, **kwargs):
        return await self.fn(*args, **kwargs)


class FakeTree:
    """Stand-in for bot.tree that actually registers command functions."""

    def __init__(self):
        self.registered = {}

    def command(self, **kwargs):
        def deco(fn):
            cmd = FakeCommand(fn)
            self.registered[fn.__name__] = cmd
            return cmd

        return deco


def make_config(commands_enabled=False):
    return AppConfig(
        telegram=TelegramConfig(api_id=1, api_hash="hash"),
        discord=DiscordConfig(token="token", commands_enabled=commands_enabled),
        media=MediaConfig(),
        routes=[],
    )


def make_payload(attachments=None, catbox_urls=None, notices=None):
    return ForwardPayload(
        route_name="route",
        chat_name="Chat",
        sender_name="@user",
        text="hello",
        attachments=attachments or [],
        catbox_urls=catbox_urls or [],
        notices=notices or [],
    )


@pytest.fixture(autouse=True)
def _reset_shared_mocks():
    commands.Bot.reset_mock(return_value=True)
    discord.File.reset_mock()
    discord.Embed.reset_mock()
    discord.Embed.return_value.fields = []
    app_commands.describe.reset_mock()
    _channel_cache.clear()
    yield


@pytest.fixture
def make_client():
    loops = []

    def _make(commands_enabled=False):
        bot, send_payload, loop = create_discord_client(
            make_config(commands_enabled=commands_enabled)
        )
        loops.append(loop)
        return bot, send_payload

    yield _make
    for loop in loops:
        loop.close()


# --- Part 2: disk-mode attachments -----------------------------------------


async def test_send_payload_bytes_attachment_uses_bytesio(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.fetch_channel = AsyncMock(return_value=channel)

    await send_payload(1001, make_payload(attachments=[(b"file-bytes", "photo.jpg")]))

    call = discord.File.call_args
    assert isinstance(call.kwargs["fp"], io.BytesIO)
    assert call.kwargs["filename"] == "photo.jpg"
    channel.send.assert_awaited_once()


async def test_send_payload_path_attachment_uses_path_directly(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.fetch_channel = AsyncMock(return_value=channel)

    await send_payload(1002, make_payload(attachments=[("/tmp/cache/photo.jpg", "photo.jpg")]))

    call = discord.File.call_args
    assert call.kwargs["fp"] == "/tmp/cache/photo.jpg"
    assert not isinstance(call.kwargs["fp"], io.BytesIO)
    assert call.kwargs["filename"] == "photo.jpg"


async def test_send_payload_mixed_attachments_handled_per_type(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.fetch_channel = AsyncMock(return_value=channel)

    await send_payload(
        1003,
        make_payload(attachments=[(b"bytes", "a.jpg"), ("/tmp/cache/b.jpg", "b.jpg")]),
    )

    calls = discord.File.call_args_list
    assert isinstance(calls[0].kwargs["fp"], io.BytesIO)
    assert calls[1].kwargs["fp"] == "/tmp/cache/b.jpg"


# --- M1: channel cache ------------------------------------------------------


async def test_channel_cache_avoids_repeated_fetch(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.fetch_channel = AsyncMock(return_value=channel)

    payload = make_payload()
    await send_payload(2001, payload)
    await send_payload(2001, payload)

    assert bot.fetch_channel.await_count == 1


async def test_get_channel_hit_skips_fetch(make_client):
    bot, send_payload = make_client()
    cached = MagicMock()
    cached.send = AsyncMock()
    bot.get_channel.return_value = cached
    bot.fetch_channel = AsyncMock()

    await send_payload(2002, make_payload())

    bot.fetch_channel.assert_not_awaited()
    cached.send.assert_awaited_once()


async def test_channel_not_found_logs_and_does_not_send(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=discord.NotFound("no such channel"))

    await send_payload(2003, make_payload())

    assert bot.fetch_channel.await_count == 1
    assert 2003 not in _channel_cache


async def test_channel_forbidden_logs_and_does_not_send(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=discord.Forbidden("no access"))

    await send_payload(2004, make_payload())

    assert bot.fetch_channel.await_count == 1
    assert 2004 not in _channel_cache


async def test_send_http_error_is_caught(make_client):
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))
    bot.fetch_channel = AsyncMock(return_value=channel)

    await send_payload(2005, make_payload())  # must not raise


async def test_send_notfound_invalidates_cache(make_client):
    # MINOR-5: a cached channel that 404s on send must be dropped so the next
    # message re-fetches instead of failing forever against the stale entry.
    bot, send_payload = make_client()
    bot.get_channel.return_value = None
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=discord.NotFound("deleted"))
    bot.fetch_channel = AsyncMock(return_value=channel)

    await send_payload(3001, make_payload())
    await send_payload(3001, make_payload())

    assert 3001 not in _channel_cache
    assert bot.fetch_channel.await_count == 2  # re-fetched after invalidation


# --- M3: negative Telegram chat IDs -----------------------------------------


async def test_route_add_negates_telegram_id(make_client):
    tree = FakeTree()
    commands.Bot.return_value.tree = tree
    make_client(commands_enabled=True)

    cmd = tree.registered["route_cmd"]
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    with patch("discord_client.add_route") as mock_add_route:
        await cmd(
            interaction,
            action="add",
            name="news",
            telegram_id="123456789",
            discord_channel="987654321",
        )

    mock_add_route.assert_called_once()
    route = mock_add_route.call_args.args[1]
    assert route.from_chats == [-123456789]
    assert route.to_channels == [987654321]


async def test_route_add_invalid_telegram_id_returns_error(make_client):
    tree = FakeTree()
    commands.Bot.return_value.tree = tree
    make_client(commands_enabled=True)

    cmd = tree.registered["route_cmd"]
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    with patch("discord_client.add_route") as mock_add_route:
        await cmd(
            interaction,
            action="add",
            name="news",
            telegram_id="not-a-number",
            discord_channel="987654321",
        )

    mock_add_route.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    assert "Error" in interaction.response.send_message.await_args.args[0]


async def test_route_add_user_chat_keeps_positive_id(make_client):
    # MAJOR-4: negation is only for groups/channels — user chats keep their
    # positive ID when user_chat is set.
    tree = FakeTree()
    commands.Bot.return_value.tree = tree
    make_client(commands_enabled=True)

    cmd = tree.registered["route_cmd"]
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    with patch("discord_client.add_route") as mock_add_route:
        await cmd(
            interaction,
            action="add",
            name="dm",
            telegram_id="123456789",
            discord_channel="987654321",
            user_chat=True,
        )

    route = mock_add_route.call_args.args[1]
    assert route.from_chats == [123456789]


def test_route_describe_documents_negation(make_client):
    app_commands.describe.reset_mock()
    make_client(commands_enabled=True)

    kwargs = app_commands.describe.call_args.kwargs
    assert "absolute value" in kwargs["telegram_id"]