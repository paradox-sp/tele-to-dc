import asyncio
import logging
import os
import sys
import threading

from config import load_config
from discord_client import create_discord_client
from telegram_client import create_telegram_client
from discord_user_client import create_discord_user_client, flush_forwarded_ids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("CONFIG_PATH", "data/config.yaml")


async def _wait_for_discord_start(start_future, discord_bot, timeout: float = 30.0) -> None:
    """Wait for the Discord bot to log in, surfacing definite failures.

    start() only completes early on failure, so a done future means the bot
    failed to start — log the exception and re-raise it so the supervisor
    restarts the process. Slowness is not a failure: after the grace period
    we log a warning and continue.
    """
    waited = 0.0
    while waited < timeout:
        if start_future.done():
            exc = start_future.exception()
            if exc is not None:
                logger.error("Discord bot failed to start: %s", exc)
                raise exc
            return
        if discord_bot.is_ready():
            return
        await asyncio.sleep(1)
        waited += 1
    # A failure that completed just after the grace window must still be
    # surfaced, not left sitting unread in the concurrent.futures.Future.
    if start_future.done():
        exc = start_future.exception()
        if exc is not None:
            logger.error("Discord bot failed to start: %s", exc)
            raise exc
    logger.warning(
        "Discord bot not ready after %.0fs — continuing (may be slow to connect)",
        timeout,
    )


async def run_bot() -> None:
    """Run a single instance of the bot, reconnecting on failure."""
    logger.info("Loading config from %s", CONFIG_PATH)
    try:
        config = load_config(CONFIG_PATH)
    except FileNotFoundError:
        logger.error(
            "Config not found at %s — copy config.example.yaml to data/config.yaml and fill in credentials",
            CONFIG_PATH,
        )
        sys.exit(1)
    except Exception as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    if not config.route_map and not config.discord_route_map:
        logger.warning("No routes configured — bot will run but forward nothing.")

    logger.info("Loaded %d Telegram route(s), %d Discord route(s)",
                len(config.routes), len(config.discord_routes))

    discord_bot, send_payload, discord_loop = create_discord_client(config)

    # Start Discord on its own event loop in a separate daemon thread
    # so its gateway heartbeat can never be starved by Telethon.
    discord_thread = threading.Thread(
        target=discord_loop.run_forever, name="discord-loop", daemon=True
    )
    discord_thread.start()
    start_future = asyncio.run_coroutine_threadsafe(
        discord_bot.start(config.discord.token), discord_loop
    )

    tg_client = None
    dc_user_client = None
    try:
        # H2: surface Discord startup failures (e.g. invalid token) instead of
        # silently running with a dead bot. start() only completes early on failure.
        await _wait_for_discord_start(start_future, discord_bot)

        # Wrap send_payload so calls from Telethon's loop hop over to Discord's loop
        async def send_payload_safe(channel_id: int, payload) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                send_payload(channel_id, payload), discord_loop
            )
            return await asyncio.wrap_future(fut)

        tg_client = create_telegram_client(config, send_payload_safe)

        dc_user_client = create_discord_user_client(config)

        logger.info("Connecting to Telegram (first run will prompt for phone number)...")
        await tg_client.start()
        logger.info("Telegram connected.")

        if dc_user_client:
            logger.info("Starting Discord user client...")
            tg_task = asyncio.create_task(tg_client.run_until_disconnected())
            dc_task = asyncio.create_task(dc_user_client.start(config.discord_user.token))
            # Wait for either side to stop; the finally block then closes both
            # clients, releasing the other task so run_bot can return and the
            # supervisor can restart on a clean Telegram disconnect.
            await asyncio.wait({tg_task, dc_task}, return_when=asyncio.FIRST_COMPLETED)
        else:
            logger.info("Starting Discord bot...")
            await tg_client.run_until_disconnected()
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        logger.exception("Bot crashed due to: %s", exc)
        raise  # propagate to outer loop for restart
    finally:
        # Cleanup runs even when startup failed (bad Discord token, Telegram
        # auth error) so the discord-loop thread never leaks across supervisor
        # restarts.
        if tg_client is not None and tg_client.is_connected():
            await tg_client.disconnect()
        if dc_user_client is not None and not dc_user_client.is_closed():
            await dc_user_client.close()
        await flush_forwarded_ids()
        async def _close_and_stop():
            await discord_bot.close()
            discord_loop.stop()
        asyncio.run_coroutine_threadsafe(_close_and_stop(), discord_loop)
        discord_thread.join(timeout=10)
        logger.info("All clients disconnected.")


async def main() -> None:
    """Supervisor that restarts the bot on unexpected errors."""
    while True:
        try:
            await run_bot()
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Shutting down.")
            break
        except Exception:
            # Unexpected error; log and restart after a delay
            logger.exception("Bot crashed unexpectedly; restarting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down.")