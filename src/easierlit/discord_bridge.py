from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

import discord
from chainlit.data import get_data_layer
from chainlit.user import User

from .discord_outgoing import resolve_discord_channel, send_discord_command
from .models import OutgoingCommand

LOGGER = logging.getLogger(__name__)


class EasierlitDiscordBridge:
    def __init__(self, *, runtime: Any, bot_token: str) -> None:
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ValueError("bot_token must be a non-empty string.")

        self._runtime = runtime
        self._bot_token = bot_token.strip()

        self._client: discord.Client | None = None
        self._client_task: asyncio.Task[None] | None = None
        self._typing_tasks: dict[int, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()

        # Tuned for easy testability without changing public API.
        self._typing_heartbeat_seconds = 7.0
        self._typing_retry_seconds = 1.0

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._client_task is not None and not self._client_task.done():
                return

            self._client = self._create_discord_client()
            self._register_runtime_callbacks()
            self._client_task = asyncio.create_task(self._run_client_forever())

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._register_runtime_callbacks(clear=True)
            await self._cancel_all_typing_tasks()

            client = self._client
            client_task = self._client_task
            self._client = None
            self._client_task = None

            if client is not None and not client.is_closed():
                try:
                    await client.close()
                except Exception:
                    LOGGER.exception("Failed to close Discord client.")

            if client_task is not None:
                client_task.cancel()
                try:
                    await client_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.exception("Discord client task exited with error during shutdown.")

    def _create_discord_client(self) -> discord.Client:
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._register_discord_event_handlers(client)
        return client

    def _register_discord_event_handlers(self, client: discord.Client) -> None:
        add_listener = getattr(client, "add_listener", None)
        if callable(add_listener):
            add_listener(self._on_discord_ready, "on_ready")
            add_listener(self._on_discord_message, "on_message")
            return

        event_decorator = getattr(client, "event", None)
        if callable(event_decorator):

            @event_decorator
            async def on_ready():
                await self._on_discord_ready()

            @event_decorator
            async def on_message(message: discord.Message):
                await self._on_discord_message(message)

            return

        # Last-resort fallback for discord client variants without decorators.
        async def on_ready():
            await self._on_discord_ready()

        async def on_message(message: discord.Message):
            await self._on_discord_message(message)

        setattr(client, "on_ready", on_ready)
        setattr(client, "on_message", on_message)

    async def _run_client_forever(self) -> None:
        client = self._client
        if client is None:
            return

        try:
            await client.start(self._bot_token)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Discord bridge client crashed.")

    def _register_runtime_callbacks(self, *, clear: bool = False) -> None:
        if clear:
            self._runtime.set_discord_sender(None)
            self._runtime.set_discord_typing_state_sender(None)
            return

        self._runtime.set_discord_sender(self._send_discord_command)
        self._runtime.set_discord_typing_state_sender(self._set_discord_typing_state)

    async def _send_discord_command(self, channel_id: int, command: OutgoingCommand) -> bool:
        client = self._client
        if client is None:
            return False
        return await send_discord_command(
            client=client,
            channel_id=channel_id,
            command=command,
            logger=LOGGER,
        )

    async def _set_discord_typing_state(self, channel_id: int, is_running: bool) -> bool:
        if not isinstance(channel_id, int):
            return False

        if is_running:
            running_task = self._typing_tasks.get(channel_id)
            if running_task is not None and not running_task.done():
                return True

            task = asyncio.create_task(self._typing_loop(channel_id))
            self._typing_tasks[channel_id] = task
            task.add_done_callback(
                lambda done_task, target_channel=channel_id: self._remove_typing_task(
                    target_channel, done_task
                )
            )
            return True

        typing_task = self._typing_tasks.pop(channel_id, None)
        if typing_task is None:
            return False
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.exception("Discord typing task failed while closing channel %s.", channel_id)
        return True

    def _remove_typing_task(self, channel_id: int, done_task: asyncio.Task[None]) -> None:
        current = self._typing_tasks.get(channel_id)
        if current is done_task:
            self._typing_tasks.pop(channel_id, None)

    async def _cancel_all_typing_tasks(self) -> None:
        tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _typing_loop(self, channel_id: int) -> None:
        while True:
            client = self._client
            if client is None:
                return

            channel = await resolve_discord_channel(
                client=client,
                channel_id=channel_id,
                logger=LOGGER,
            )
            if channel is None:
                await asyncio.sleep(self._typing_retry_seconds)
                continue

            try:
                async with channel.typing():
                    await asyncio.sleep(self._typing_heartbeat_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Failed to keep Discord typing state for channel %s.", channel_id)
                await asyncio.sleep(self._typing_retry_seconds)

    async def _on_discord_ready(self) -> None:
        client = self._client
        if client is None:
            return
        LOGGER.info("Discord bridge connected as %s", client.user)

    async def _on_discord_message(self, message: discord.Message) -> None:
        author = getattr(message, "author", None)
        if author is None:
            return
        if getattr(author, "bot", False):
            return

        channel = getattr(message, "channel", None)
        channel_id = self._coerce_int(getattr(channel, "id", None))
        if channel_id is None:
            LOGGER.warning("Skipping Discord message %s with invalid channel id.", message.id)
            return

        thread_id = self._thread_id_for_channel(channel_id)
        self._runtime.register_discord_channel(thread_id=thread_id, channel_id=channel_id)

        message_metadata = self._build_discord_message_metadata(message)
        await self._upsert_discord_thread(
            thread_id=thread_id,
            thread_name=self._resolve_thread_name(message),
            metadata=message_metadata,
        )

        app = self._runtime.get_app()
        if app is None:
            LOGGER.warning(
                "Skipping Discord enqueue for message %s because runtime app is unavailable.",
                message.id,
            )
            return

        author_id = self._coerce_text(getattr(author, "id", None)) or "unknown"
        author_name = self._resolve_author_name(author)
        content = message.content or ""
        elements = self._build_discord_attachment_elements(message.attachments or [])
        created_at = self._to_iso_timestamp(getattr(message, "created_at", None))

        try:
            app.enqueue(
                thread_id=thread_id,
                content=content,
                session_id=f"discord:{author_id}",
                message_id=f"discord:{message.id}",
                author=author_name,
                metadata=message_metadata,
                elements=elements,
                created_at=created_at,
            )
        except Exception:
            LOGGER.exception("Failed to enqueue Discord message %s.", message.id)

    async def _upsert_discord_thread(
        self,
        *,
        thread_id: str,
        thread_name: str,
        metadata: dict[str, Any],
    ) -> None:
        data_layer = get_data_layer()
        if data_layer is None:
            return

        update_thread = getattr(data_layer, "update_thread", None)
        if not callable(update_thread):
            return

        owner_user_id = await self._resolve_owner_user_id(data_layer)

        try:
            await update_thread(
                thread_id=thread_id,
                name=thread_name,
                user_id=owner_user_id,
                metadata=metadata,
            )
        except Exception:
            LOGGER.exception("Failed to upsert Discord thread %s.", thread_id)

    async def _resolve_owner_user_id(self, data_layer: Any) -> str | None:
        auth = self._runtime.get_auth()
        if auth is None:
            return None

        identifier = (auth.identifier or auth.username).strip()
        if not identifier:
            return None

        get_user = getattr(data_layer, "get_user", None)
        if callable(get_user):
            try:
                persisted_user = await get_user(identifier)
            except Exception:
                LOGGER.exception("Failed to resolve auth owner user '%s'.", identifier)
                persisted_user = None
            if persisted_user is not None:
                user_id = self._coerce_text(getattr(persisted_user, "id", None))
                if user_id:
                    return user_id

        create_user = getattr(data_layer, "create_user", None)
        if not callable(create_user):
            return None

        try:
            created_user = await create_user(
                User(
                    identifier=identifier,
                    metadata=(auth.metadata or {}),
                )
            )
        except Exception:
            LOGGER.exception("Failed to create auth owner user '%s'.", identifier)
            return None

        return self._coerce_text(getattr(created_user, "id", None))

    def _thread_id_for_channel(self, channel_id: int) -> str:
        return str(uuid5(NAMESPACE_DNS, f"discord-channel:{channel_id}"))

    def _resolve_thread_name(self, message: discord.Message) -> str:
        channel = getattr(message, "channel", None)
        raw_name = self._coerce_text(getattr(channel, "name", None))
        if raw_name:
            return raw_name
        channel_id = self._coerce_text(getattr(channel, "id", None)) or "unknown"
        return f"discord-channel-{channel_id}"

    def _resolve_author_name(self, author: Any) -> str:
        display_name = self._coerce_text(getattr(author, "display_name", None))
        if display_name:
            return display_name
        name = self._coerce_text(getattr(author, "name", None))
        if name:
            return name
        return "Discord User"

    def _build_discord_message_metadata(self, message: discord.Message) -> dict[str, Any]:
        channel = getattr(message, "channel", None)
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        message_id = self._coerce_text(getattr(message, "id", None))
        channel_id = self._coerce_text(getattr(channel, "id", None))
        author_id = self._coerce_text(getattr(author, "id", None))
        guild_id = self._coerce_text(getattr(guild, "id", None))

        metadata = {
            "client_type": "discord",
            "clientType": "discord",
            "easierlit_discord_scope": "channel_id",
            "easierlit_discord_message_id": message_id,
            "easierlit_discord_channel_id": channel_id,
            "easierlit_discord_owner_id": author_id,
            "easierlit_discord_guild_id": guild_id,
        }
        return {key: value for key, value in metadata.items() if value is not None}

    def _build_discord_attachment_elements(self, attachments: list[Any]) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for attachment in attachments:
            attachment_id = self._coerce_text(getattr(attachment, "id", None))
            name = self._coerce_text(getattr(attachment, "filename", None)) or "attachment"
            url = self._coerce_text(getattr(attachment, "url", None))
            mime = self._coerce_text(getattr(attachment, "content_type", None))
            size = self._coerce_int(getattr(attachment, "size", None))
            element_type = self._infer_element_type(mime=mime, name=name)

            element = {
                "id": f"discord-att:{attachment_id}" if attachment_id else None,
                "name": name,
                "url": url,
                "mime": mime,
                "size": str(size) if size is not None else None,
                "type": element_type,
                "display": "inline",
            }
            cleaned = {key: value for key, value in element.items() if value is not None}
            elements.append(cleaned)

        return elements

    def _infer_element_type(self, *, mime: str | None, name: str) -> str:
        normalized = (mime or "").strip().lower()
        if not normalized:
            guessed, _ = mimetypes.guess_type(name)
            normalized = (guessed or "").strip().lower()

        if normalized.startswith("image/"):
            return "image"
        if normalized.startswith("audio/"):
            return "audio"
        if normalized.startswith("video/"):
            return "video"
        if normalized == "application/pdf":
            return "pdf"
        if normalized.startswith("text/"):
            return "text"
        return "file"

    def _coerce_text(self, value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            return None
        rendered = str(value).strip()
        return rendered or None

    def _coerce_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_iso_timestamp(self, value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.isoformat()
        return None
