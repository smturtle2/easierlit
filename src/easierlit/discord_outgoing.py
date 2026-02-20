from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import discord
from .models import OutgoingCommand

_SUPPORTED_COMMANDS = frozenset({"add_message", "add_tool"})
_MAX_DISCORD_ATTACHMENTS = 10


def supports_discord_command(command_name: str) -> bool:
    return command_name in _SUPPORTED_COMMANDS


def render_discord_content(command: OutgoingCommand) -> str:
    content = command.content or ""
    if command.command == "add_tool":
        return f"[{command.author}] {content}"
    return content


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, dict, list, tuple, set)):
        return None
    rendered = str(value).strip()
    return rendered or None


def _coerce_element_dict(element: Any) -> dict[str, Any]:
    if isinstance(element, dict):
        return dict(element)

    result: dict[str, Any] = {}
    to_dict = getattr(element, "to_dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            result.update(dumped)

    attr_map = {
        "name": "name",
        "path": "path",
        "url": "url",
        "content": "content",
    }
    for key, attr_name in attr_map.items():
        if key in result:
            continue
        if hasattr(element, key):
            result[key] = getattr(element, key)
            continue
        if hasattr(element, attr_name):
            result[key] = getattr(element, attr_name)

    return result


async def _download_url_bytes(url: str, logger: logging.Logger) -> bytes | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None

    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(url) as response:
                if response.status != 200:
                    return None
                return await response.read()
    except Exception:
        logger.exception("Failed to download element URL for Discord attachment: %s", url)
        return None


async def _read_element_bytes(element_dict: dict[str, Any], logger: logging.Logger) -> bytes | None:
    path_text = _coerce_text(element_dict.get("path"))
    if path_text:
        file_path = Path(path_text).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.is_file():
            return None
        try:
            return await asyncio.to_thread(file_path.read_bytes)
        except Exception:
            return None

    content = element_dict.get("content")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if isinstance(content, str):
        return content.encode("utf-8")

    url = _coerce_text(element_dict.get("url"))
    if url:
        return await _download_url_bytes(url, logger)

    return None


def _resolve_element_file_name(element_dict: dict[str, Any], index: int) -> str:
    name = _coerce_text(element_dict.get("name"))
    if name:
        return Path(name).name or f"element-{index + 1}.bin"

    path_text = _coerce_text(element_dict.get("path"))
    if path_text:
        path_name = Path(path_text).name
        if path_name:
            return path_name

    url = _coerce_text(element_dict.get("url"))
    if url:
        url_name = Path(urlparse(url).path).name
        if url_name:
            return url_name

    return f"element-{index + 1}.bin"


async def build_discord_files(
    *,
    elements: list[Any],
    logger: logging.Logger,
) -> list[discord.File]:
    files: list[discord.File] = []
    for index, element in enumerate(elements):
        if len(files) >= _MAX_DISCORD_ATTACHMENTS:
            break

        element_dict = _coerce_element_dict(element)
        payload = await _read_element_bytes(element_dict, logger)
        if payload is None:
            continue

        file_name = _resolve_element_file_name(element_dict, index)
        files.append(
            discord.File(
                fp=io.BytesIO(payload),
                filename=file_name,
            )
        )

    return files


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
        raw_elements = getattr(command, "elements", None)
        files = await build_discord_files(elements=raw_elements or [], logger=logger)
        content = render_discord_content(command)
        if not content.strip() and not files:
            return False
        if files:
            await channel.send(content, files=files)
        else:
            await channel.send(content)
    except Exception:
        logger.exception("Failed to send Discord message for thread '%s'.", command.thread_id)
        return False

    return True
