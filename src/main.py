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
        try:
            await asyncio.gather(
                discord_bot.start(config.discord.token),
                tg_client.run_until_disconnected(),
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as exc:
            logger.exception("Bot crashed due to: %s", exc)
            raise  # propagate to outer loop for restart
        finally:
            if tg_client.is_connected():
                await tg_client.disconnect()
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