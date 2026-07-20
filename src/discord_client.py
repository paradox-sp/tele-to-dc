import asyncio
import io
import logging
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from config import AppConfig, Route, add_route, remove_route
from message_processor import ForwardPayload

logger = logging.getLogger(__name__)


def create_discord_client(
    config: AppConfig,
) -> tuple[commands.Bot, Callable[[int, ForwardPayload], Awaitable[None]], asyncio.AbstractEventLoop]:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    _first_ready = True

    @bot.event
    async def on_ready():
        nonlocal _first_ready
        if config.discord.commands_enabled and _first_ready:
            await bot.tree.sync()
            logger.info("Slash commands synced")
            _first_ready = False
        logger.info("Discord bot ready: %s", bot.user)

    if config.discord.commands_enabled:
        _register_commands(bot, config)

    async def send_payload(channel_id: int, payload: ForwardPayload) -> None:
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except discord.NotFound:
                logger.error("Channel %d not found", channel_id)
                return
            except discord.Forbidden:
                logger.error("No access to channel %d", channel_id)
                return

        embed = discord.Embed(color=0x2CA5E0)
        embed.set_author(name=f"📢 {payload.route_name}"[:256])
        embed.add_field(name=payload.chat_name[:256], value=(payload.sender_name or "\u200b")[:1024], inline=True)

        if payload.forward_from:
            embed.add_field(name="\u200b", value=payload.forward_from[:1024], inline=False)

        if payload.text:
            embed.description = payload.text[:4096]

        MAX_FIELDS = 25
        urls_shown = 0
        for url in payload.catbox_urls:
            if len(embed.fields) >= MAX_FIELDS:
                logger.warning("Embed field limit (%d) reached; %d catbox URL(s) not shown", MAX_FIELDS, len(payload.catbox_urls) - urls_shown)
                break
            embed.add_field(name="File", value=url[:1024], inline=False)
            urls_shown += 1
        notices_shown = 0
        for notice in payload.notices:
            if len(embed.fields) >= MAX_FIELDS:
                logger.warning("Embed field limit (%d) reached; %d notice(s) not shown", MAX_FIELDS, len(payload.notices) - notices_shown)
                break
            embed.add_field(name="Notice", value=notice[:1024], inline=False)
            notices_shown += 1

        if len(payload.attachments) > 10:
            embed.add_field(
                name="Notice",
                value=f"⚠️ Album has {len(payload.attachments)} files — only first 10 forwarded (Discord limit).",
                inline=False,
            )
        files = [
            discord.File(fp=io.BytesIO(data), filename=fname)
            for data, fname in payload.attachments[:10]
        ]

        try:
            await channel.send(embed=embed, files=files or discord.utils.MISSING)
        except discord.HTTPException as exc:
            logger.error("Failed to send to channel %d: %s", channel_id, exc)
        finally:
            for f in files:
                f.fp.close()

    discord_loop = asyncio.new_event_loop()
    return bot, send_payload, discord_loop


def _register_commands(bot: commands.Bot, config: AppConfig) -> None:
    @bot.tree.command(name="route", description="Manage forwarding routes")
    @app_commands.describe(
        action="list · add · remove",
        name="Route name",
        telegram_id="Telegram chat ID (negative number for groups/channels)",
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
            try:
                route = Route(name=name, from_chats=[int(telegram_id)], to_channels=[int(discord_channel)])
                add_route(config, route)
                await interaction.response.send_message(f"Route **{name}** added. Restart the bot to apply changes to the Telegram listener.", ephemeral=True)
            except ValueError as exc:
                await interaction.response.send_message(f"Error: {exc}", ephemeral=True)

        elif action == "remove":
            if not name:
                await interaction.response.send_message("Provide a route name.", ephemeral=True)
                return
            if remove_route(config, name):
                await interaction.response.send_message(f"Route **{name}** removed. Restart the bot to apply changes to the Telegram listener.", ephemeral=True)
            else:
                await interaction.response.send_message(f"Route **{name}** not found.", ephemeral=True)

        else:
            await interaction.response.send_message(
                "Unknown action. Use: list / add / remove", ephemeral=True
            )

    @route_cmd.error
    async def route_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
