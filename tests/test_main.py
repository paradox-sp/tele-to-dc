# tests/test_main.py
"""Tests for main.py's Discord startup wait (H2).

main.py imports discord_client and telegram_client, neither of which can be
imported without their third-party packages installed, so fake `discord` and
`telethon` package trees are installed into sys.modules first.
"""
import asyncio
import concurrent.futures
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _install_fakes():
    discord = types.ModuleType("discord")
    discord.File = MagicMock(name="File")
    discord.Embed = MagicMock(name="Embed")
    discord.NotFound = type("NotFound", (Exception,), {})
    discord.Forbidden = type("Forbidden", (Exception,), {})
    discord.HTTPException = type("HTTPException", (Exception,), {})
    discord.Intents = MagicMock(name="Intents")
    discord.Interaction = MagicMock(name="Interaction")
    discord.utils = MagicMock(name="utils")
    discord.utils.MISSING = object()

    app_commands = types.ModuleType("discord.app_commands")
    app_commands.describe = lambda **kw: (lambda f: f)
    app_commands.checks = MagicMock(name="checks")
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

    telethon = types.ModuleType("telethon")
    telethon.TelegramClient = MagicMock(name="TelegramClient")
    telethon.events = MagicMock(name="events")
    sys.modules["telethon"] = telethon
    sys.modules["telethon.events"] = telethon.events


_install_fakes()

from main import _wait_for_discord_start


async def test_wait_raises_on_failed_start():
    fut = concurrent.futures.Future()
    fut.set_exception(RuntimeError("invalid token"))
    bot = MagicMock()

    with pytest.raises(RuntimeError, match="invalid token"):
        await _wait_for_discord_start(fut, bot, timeout=1)


async def test_wait_returns_immediately_when_ready():
    fut = concurrent.futures.Future()
    bot = MagicMock()
    bot.is_ready.return_value = True

    await _wait_for_discord_start(fut, bot, timeout=1)

    assert not fut.done()  # never waited on the future


async def test_wait_returns_when_future_done_without_error():
    fut = concurrent.futures.Future()
    fut.set_result(None)
    bot = MagicMock()

    await _wait_for_discord_start(fut, bot, timeout=1)


async def test_wait_warns_after_timeout_and_continues():
    fut = concurrent.futures.Future()  # never completes
    bot = MagicMock()
    bot.is_ready.return_value = False

    with patch("main.logger") as mock_logger:
        await _wait_for_discord_start(fut, bot, timeout=0.1)

    mock_logger.warning.assert_called_once()


async def test_wait_raises_on_failure_completing_after_grace():
    # MINOR-9: a failure that lands just after the grace window must still be
    # surfaced, not left sitting unread in the concurrent.futures.Future.
    class LateFuture(concurrent.futures.Future):
        def __init__(self):
            super().__init__()
            self._done_calls = 0

        def done(self):
            self._done_calls += 1
            if self._done_calls == 2:
                self.set_exception(RuntimeError("late failure"))
            return super().done()

    fut = LateFuture()
    bot = MagicMock()
    bot.is_ready.return_value = False

    with patch("main.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="late failure"):
            await _wait_for_discord_start(fut, bot, timeout=0.1)


async def test_run_bot_cleans_up_when_discord_start_fails():
    # MAJOR-3: a Discord startup failure (invalid token) must still run the
    # finally cleanup — close bot, stop loop, join thread — so the supervisor
    # restart doesn't leak a discord-loop thread per cycle.
    import main as main_mod
    from types import SimpleNamespace

    loop = asyncio.new_event_loop()
    bot = MagicMock()
    bot.start = AsyncMock()
    send_payload = AsyncMock()

    config = SimpleNamespace(
        route_map={-1001: [123]},
        routes=[],
        telegram=SimpleNamespace(session_name="s", api_id=1, api_hash="h"),
        discord=SimpleNamespace(token="tok", commands_enabled=False),
    )

    def _consume(coro, loop):
        # The test's loop never runs, so coroutines scheduled onto it
        # (bot.start, _close_and_stop) would otherwise warn as never-awaited.
        # Close them and return a done future, like a real running loop would.
        coro.close()
        fut = concurrent.futures.Future()
        fut.set_result(None)
        return fut

    with patch("main.load_config", return_value=config), \
         patch("main.create_discord_client", return_value=(bot, send_payload, loop)), \
         patch("main._wait_for_discord_start", side_effect=RuntimeError("invalid token")), \
         patch("main.asyncio.run_coroutine_threadsafe", side_effect=_consume), \
         patch("main.threading.Thread") as mock_thread:
        with pytest.raises(RuntimeError, match="invalid token"):
            await main_mod.run_bot()

    mock_thread.return_value.start.assert_called_once()
    mock_thread.return_value.join.assert_called_once()
    loop.close()