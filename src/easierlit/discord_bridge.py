from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord
from chainlit.config import config
from chainlit.context import ChainlitContext, HTTPSession, context_var
from chainlit.data import get_data_layer
from chainlit.discord.app import DiscordEmitter, download_discord_files
from chainlit.message import Message
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_session

from .discord_outgoing import send_discord_command
from .models import OutgoingCommand
from .runtime import RuntimeRegistry

if TYPE_CHECKING:
    from discord.abc import MessageableChannel

LOGGER = logging.getLogger(__name__)
_USER_PREFIX = "discord_"


class EasierlitDiscordBridge:
    def __init__(
        self,
        *,
        runtime: RuntimeRegistry,
        bot_token: str,
        data_layer_getter: Callable[[], Any | None] = get_data_layer,
        client: discord.Client | None = None,
    ) -> None:
        self._runtime = runtime
        self._bot_token = bot_token
        self._data_layer_getter = data_layer_getter

        if client is None:
            intents = discord.Intents.default()
            intents.message_content = True
            client = discord.Client(intents=intents)

        self._client = client
        self._client_task: asyncio.Task[None] | None = None
        self._started = False

        self._users_by_discord_id: dict[int, User | PersistedUser] = {}

        self._client.event(self._on_ready)
        self._client.event(self._on_message)

    async def start(self) -> None:
        if self._started:
            return

        self._runtime.set_discord_sender(self.send_outgoing_command)
        self._started = True
        self._client_task = asyncio.create_task(self._client.start(self._bot_token))
        self._client_task.add_done_callback(self._on_client_task_done)

    async def stop(self) -> None:
        self._runtime.set_discord_sender(None)

        if not self._started:
            return

        self._started = False
        task = self._client_task
        self._client_task = None

        try:
            await self._client.close()
        except Exception:
            LOGGER.exception("Failed to close Easierlit Discord bridge client cleanly.")

        if task is None:
            return

        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("Easierlit Discord bridge task exited with an error.")
            return

        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Easierlit Discord bridge task exited with an error.")

    def _on_client_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return

        try:
            task.result()
        except Exception:
            LOGGER.exception("Easierlit Discord bridge crashed.")

    async def _on_ready(self) -> None:
        LOGGER.info("Easierlit Discord bridge logged in as %s", self._client.user)

    async def _on_message(self, message: discord.Message) -> None:
        if not self._client.user or message.author == self._client.user:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        if not is_dm and not self._client.user.mentioned_in(message):
            return

        resolved = await self._resolve_thread_target(message)
        if resolved is None:
            return

        thread_id, thread_name, channel, bind_thread_to_user = resolved
        await self._process_discord_message(
            message=message,
            thread_id=thread_id,
            thread_name=thread_name,
            channel=channel,
            bind_thread_to_user=bind_thread_to_user,
        )

    async def _resolve_thread_target(
        self,
        message: discord.Message,
    ) -> tuple[str, str, MessageableChannel, bool] | None:
        channel = message.channel
        bind_thread_to_user = False

        if isinstance(channel, discord.Thread):
            thread_name = f"{channel.name}"
            thread_id = self._build_channel_thread_id(channel.id)
            return thread_id, thread_name, channel, bind_thread_to_user

        if hasattr(discord, "ForumChannel") and isinstance(channel, discord.ForumChannel):
            thread_name = f"{channel.name}"
            thread_id = self._build_channel_thread_id(channel.id)
            return thread_id, thread_name, channel, bind_thread_to_user

        if isinstance(channel, discord.DMChannel):
            thread_id = self._build_channel_thread_id(channel.id)
            thread_name = f"{self._resolve_author_name(message.author)} Discord DM"
            bind_thread_to_user = True
            return thread_id, thread_name, channel, bind_thread_to_user

        if isinstance(channel, discord.GroupChannel):
            thread_id = self._build_channel_thread_id(channel.id)
            thread_name = f"{channel.name}"
            return thread_id, thread_name, channel, bind_thread_to_user

        if isinstance(channel, discord.TextChannel):
            thread_id = self._build_channel_thread_id(channel.id)
            discord_thread_name = self._clean_content(message)[:100] or "Untitled"
            thread_channel = await channel.create_thread(name=discord_thread_name, message=message)
            thread_name = f"{thread_channel.name}"
            return thread_id, thread_name, thread_channel, bind_thread_to_user

        LOGGER.warning("Unsupported channel type: %s", getattr(channel, "type", "unknown"))
        return None

    def _build_channel_thread_id(self, channel_id: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(channel_id)))

    def _clean_content(self, message: discord.Message) -> str:
        if not self._client.user:
            return message.content

        bot_mention = f"<@!?{self._client.user.id}>"
        return re.sub(bot_mention, "", message.content).strip()

    def _resolve_author_name(self, author: object) -> str:
        for attr in ("display_name", "name"):
            candidate = getattr(author, attr, None)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        rendered = str(author).strip()
        if rendered:
            return rendered
        return "Unknown"

    async def _process_discord_message(
        self,
        *,
        message: discord.Message,
        thread_id: str,
        thread_name: str,
        channel: MessageableChannel,
        bind_thread_to_user: bool,
    ) -> None:
        user = await self._get_or_create_user(message.author)

        text = self._clean_content(message)
        discord_files = message.attachments

        session = HTTPSession(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            user=user,
            client_type="discord",
        )
        ctx = self._init_discord_context(session=session, channel=channel, message=message)

        try:
            await self._rebind_discord_thread_owner_to_runtime_auth(
                thread_id=thread_id,
                message=message,
                thread_name=thread_name,
                session_metadata=ctx.session.to_persistable(),
                bind_thread_to_user=bind_thread_to_user,
                discord_user=user,
            )

            file_elements = await download_discord_files(session, discord_files)

            if on_chat_start := config.code.on_chat_start:
                await on_chat_start()

            msg = Message(
                content=text,
                elements=file_elements,
                type="user_message",
                author=user.metadata.get("name"),
            )
            await msg.send()

            if on_message := config.code.on_message:
                typing_cm = channel.typing() if hasattr(channel, "typing") else None
                if typing_cm is None:
                    await on_message(msg)
                else:
                    async with typing_cm:
                        await on_message(msg)

            if on_chat_end := config.code.on_chat_end:
                await on_chat_end()
        finally:
            await ctx.session.delete()

    def _init_discord_context(
        self,
        *,
        session: HTTPSession,
        channel: MessageableChannel,
        message: discord.Message,
    ) -> ChainlitContext:
        emitter = DiscordEmitter(session=session, channel=channel)
        ctx = ChainlitContext(session=session, emitter=emitter)
        context_var.set(ctx)
        user_session.set("discord_message", message)
        user_session.set("discord_channel", channel)
        return ctx

    async def _get_or_create_user(self, discord_user: discord.User | discord.Member):
        if discord_user.id in self._users_by_discord_id:
            return self._users_by_discord_id[discord_user.id]

        metadata = {
            "name": discord_user.name,
            "id": discord_user.id,
        }
        user = User(identifier=_USER_PREFIX + str(discord_user.name), metadata=metadata)
        self._users_by_discord_id[discord_user.id] = user

        data_layer = self._data_layer_getter()
        if data_layer:
            try:
                persisted_user = await data_layer.create_user(user)
                if persisted_user:
                    self._users_by_discord_id[discord_user.id] = persisted_user
            except Exception:
                LOGGER.exception("Failed to create Discord user '%s'.", discord_user.name)

        return self._users_by_discord_id[discord_user.id]

    async def send_outgoing_command(self, channel_id: int, command: OutgoingCommand) -> bool:
        return await send_discord_command(
            client=self._client,
            channel_id=channel_id,
            command=command,
            logger=LOGGER,
        )

    async def _resolve_runtime_auth_owner_user_id(self, data_layer: object) -> str | None:
        auth = self._runtime.get_auth()
        if auth is None:
            return None

        identifier = auth.identifier or auth.username
        try:
            persisted_user = await data_layer.get_user(identifier)
        except Exception:
            LOGGER.warning("Failed to query owner user '%s' from data layer.", identifier)
            return None

        if persisted_user is not None:
            resolved_id = getattr(persisted_user, "id", None)
            return str(resolved_id) if resolved_id is not None else None

        if not hasattr(data_layer, "create_user"):
            return None

        try:
            created_user = await data_layer.create_user(
                User(
                    identifier=identifier,
                    metadata=auth.metadata or {},
                )
            )
        except Exception:
            LOGGER.warning("Failed to create owner user '%s' in data layer.", identifier)
            return None

        if created_user is None:
            return None

        resolved_id = getattr(created_user, "id", None)
        return str(resolved_id) if resolved_id is not None else None

    async def _rebind_discord_thread_owner_to_runtime_auth(
        self,
        *,
        thread_id: str,
        message: discord.Message,
        thread_name: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        bind_thread_to_user: bool = False,
        discord_user: User | PersistedUser | None = None,
    ) -> None:
        data_layer = self._data_layer_getter()
        if data_layer is None:
            return

        runtime_owner_user_id = await self._resolve_runtime_auth_owner_user_id(data_layer)
        owner_user_id = self._resolve_discord_thread_owner_user_id(
            runtime_owner_user_id=runtime_owner_user_id,
            bind_thread_to_user=bind_thread_to_user,
            discord_user=discord_user,
        )

        metadata: dict[str, object] = {}
        try:
            thread = await data_layer.get_thread(thread_id)
            if isinstance(thread, dict):
                existing_metadata = thread.get("metadata")
                if isinstance(existing_metadata, dict):
                    metadata.update(existing_metadata)
                elif isinstance(existing_metadata, str):
                    try:
                        decoded = json.loads(existing_metadata)
                        if isinstance(decoded, dict):
                            metadata.update(decoded)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            LOGGER.warning(
                "Failed to read existing thread metadata while upserting Discord thread '%s'.",
                thread_id,
            )

        if isinstance(session_metadata, dict):
            metadata.update(session_metadata)
        metadata.update(self._extract_discord_owner_metadata(message))

        update_kwargs: dict[str, object] = {
            "thread_id": thread_id,
            "metadata": metadata if metadata else None,
        }
        if owner_user_id is not None:
            update_kwargs["user_id"] = owner_user_id
        if isinstance(thread_name, str) and thread_name.strip():
            update_kwargs["name"] = thread_name

        try:
            await data_layer.update_thread(**update_kwargs)
        except Exception:
            LOGGER.warning(
                "Failed to upsert Discord thread '%s' owner metadata.",
                thread_id,
            )

    def _resolve_discord_thread_owner_user_id(
        self,
        *,
        runtime_owner_user_id: str | None,
        bind_thread_to_user: bool,
        discord_user: User | PersistedUser | None,
    ) -> str | None:
        if runtime_owner_user_id is not None:
            return runtime_owner_user_id

        if bind_thread_to_user and isinstance(discord_user, PersistedUser):
            resolved_id = getattr(discord_user, "id", None)
            if resolved_id is not None:
                return str(resolved_id)

        return None

    def _extract_discord_owner_metadata(self, message: discord.Message) -> dict[str, str]:
        metadata: dict[str, str] = {}

        author = getattr(message, "author", None)
        author_id = getattr(author, "id", None)
        if author_id is not None:
            metadata["easierlit_discord_owner_id"] = str(author_id)

        for attr in ("display_name", "name"):
            candidate = getattr(author, attr, None)
            if isinstance(candidate, str) and candidate.strip():
                metadata["easierlit_discord_owner_name"] = candidate.strip()
                break

        return metadata
