from __future__ import annotations

import logging
from typing import Any

from .models import OutgoingCommand

_SUPPORTED_COMMANDS = frozenset({"add_message", "add_tool"})


def supports_discord_command(command_name: str) -> bool:
    return command_name in _SUPPORTED_COMMANDS


def render_discord_content(command: OutgoingCommand) -> str:
    content = command.content or ""
    if command.command == "add_tool":
        return f"[{command.author}] {content}"
    return content


async def resolve_discord_channel(
    *,
    client: Any,
    channel_id: int,
    logger: logging.Logger,
):
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel

    try:
        return await client.fetch_channel(channel_id)
    except Exception:
        logger.exception("Failed to fetch Discord channel %s.", channel_id)
        return None


async def send_discord_command(
    *,
    client: Any,
    channel_id: int,
    command: OutgoingCommand,
    logger: logging.Logger,
) -> bool:
    if not supports_discord_command(command.command):
        return False

    channel = await resolve_discord_channel(client=client, channel_id=channel_id, logger=logger)
    if channel is None:
        return False

    try:
        await channel.send(render_discord_content(command))
    except Exception:
        logger.exception("Failed to send Discord message for thread '%s'.", command.thread_id)
        return False

    return True
