from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord
from chainlit.data import get_data_layer
from chainlit.user import PersistedUser, User

from .discord_outgoing import resolve_discord_channel, send_discord_command
from .models import OutgoingCommand
from .runtime import RuntimeRegistry

if TYPE_CHECKING:
    from discord.abc import MessageableChannel

LOGGER = logging.getLogger(__name__)


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

        self._typing_tasks: dict[int, asyncio.Task[None]] = {}
        self._typing_task_lock = asyncio.Lock()

        self._client.event(self._on_ready)
        self._client.event(self._on_message)

    async def start(self) -> None:
        if self._started:
            return

        self._runtime.set_discord_sender(self.send_outgoing_command)
        self._runtime.set_discord_typing_state_sender(self.send_typing_state)
        await self._backfill_all_thread_owners_to_runtime_auth()
        self._started = True
        self._client_task = asyncio.create_task(self._client.start(self._bot_token))
        self._client_task.add_done_callback(self._on_client_task_done)

    async def stop(self) -> None:
        self._runtime.set_discord_sender(None)
        self._runtime.set_discord_typing_state_sender(None)

        if not self._started:
            return

        self._started = False
        await self._stop_all_typing_tasks()
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
            discord_thread_name = self._clean_content(message)[:100] or "Untitled"
            thread_channel = await channel.create_thread(name=discord_thread_name, message=message)
            thread_id = self._build_channel_thread_id(thread_channel.id)
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
        del bind_thread_to_user
        data_layer = self._data_layer_getter()
        if data_layer is None:
            return

        discord_author_name = self._resolve_author_name(message.author)
        channel_id = self._resolve_channel_id(channel)
        if channel_id is not None:
            self._runtime.register_discord_channel(thread_id=thread_id, channel_id=channel_id)
        LOGGER.warning(
            "Discord inbound received: thread_id=%s channel_id=%s message_id=%s author=%s",
            thread_id,
            channel_id,
            getattr(message, "id", None),
            discord_author_name,
        )

        thread_metadata = self._build_discord_thread_metadata(
            message=message,
            channel_id=channel_id,
        )
        is_authorized = await self._ensure_thread_authorized_for_runtime_owner(
            data_layer=data_layer,
            thread_id=thread_id,
            thread_name=thread_name,
            thread_metadata=thread_metadata,
        )
        if not is_authorized:
            LOGGER.warning(
                "Discord inbound dropped before enqueue: thread_id=%s channel_id=%s",
                thread_id,
                channel_id,
            )
            return

        app = self._runtime.get_app()
        if app is None:
            LOGGER.warning(
                "Skipping Discord inbound for thread '%s': runtime app is not bound.",
                thread_id,
            )
            return

        content = self._clean_content(message)
        elements = self._convert_discord_attachments_to_elements(message.attachments)
        message_created_at = self._coerce_message_created_at(message)
        session_id_suffix = str(channel_id) if channel_id is not None else thread_id
        session_id = f"discord:{session_id_suffix}"

        try:
            app.enqueue(
                thread_id=thread_id,
                content=content,
                session_id=session_id,
                author=discord_author_name,
                metadata=thread_metadata,
                elements=elements,
                created_at=message_created_at,
            )
            LOGGER.warning(
                "Discord inbound enqueued: thread_id=%s channel_id=%s session_id=%s content_len=%d elements=%d",
                thread_id,
                channel_id,
                session_id,
                len(content),
                len(elements),
            )
        except Exception:
            LOGGER.exception(
                "Failed to enqueue Discord inbound message for thread '%s'.",
                thread_id,
            )

    def _resolve_channel_id(self, channel: object) -> int | None:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return None
        try:
            return int(channel_id)
        except (TypeError, ValueError):
            return None

    def _coerce_message_created_at(self, message: discord.Message) -> str | None:
        created_at = getattr(message, "created_at", None)
        if created_at is None:
            return None
        isoformat = getattr(created_at, "isoformat", None)
        if callable(isoformat):
            try:
                return str(isoformat())
            except Exception:
                return None
        if isinstance(created_at, str) and created_at.strip():
            return created_at.strip()
        return None

    def _convert_discord_attachments_to_elements(
        self,
        attachments: list[object],
    ) -> list[dict[str, object]]:
        elements: list[dict[str, object]] = []
        for attachment in attachments:
            if attachment is None:
                continue

            url = getattr(attachment, "url", None)
            if not isinstance(url, str) or not url.strip():
                continue

            attachment_id = getattr(attachment, "id", None)
            element_name = getattr(attachment, "filename", None)
            content_type = getattr(attachment, "content_type", None)
            file_size = getattr(attachment, "size", None)

            element: dict[str, object] = {
                "id": str(attachment_id) if attachment_id is not None else str(uuid.uuid4()),
                "type": "file",
                "display": "inline",
                "name": element_name if isinstance(element_name, str) and element_name else "attachment",
                "url": url.strip(),
            }
            if isinstance(content_type, str) and content_type.strip():
                element["mime"] = content_type.strip()
            if isinstance(file_size, int):
                element["size"] = file_size
            elements.append(element)
        return elements

    def _build_discord_thread_metadata(
        self,
        *,
        message: discord.Message,
        channel_id: int | None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {"client_type": "discord"}
        if channel_id is not None:
            metadata["discord_channel_id"] = str(channel_id)
        metadata.update(self._extract_discord_owner_metadata(message))
        return metadata

    async def _ensure_thread_authorized_for_runtime_owner(
        self,
        *,
        data_layer: object,
        thread_id: str,
        thread_name: str | None,
        thread_metadata: dict[str, object],
    ) -> bool:
        resolved_owner = await self._resolve_required_runtime_owner_from_auth_config(
            data_layer=data_layer,
            thread_id=thread_id,
        )
        if resolved_owner is None:
            return False

        owner_user_id, expected_identifier = resolved_owner
        thread_record = await self._read_existing_thread_record(
            data_layer=data_layer,
            thread_id=thread_id,
        )
        LOGGER.warning(
            "Discord owner authorization check: thread_id=%s owner_user_id=%s expected_identifier=%s thread_exists=%s",
            thread_id,
            owner_user_id,
            expected_identifier,
            thread_record is not None,
        )

        merged_metadata = self._extract_thread_metadata(thread_record)
        if thread_metadata:
            merged_metadata.update(thread_metadata)
        await self._update_thread_owner(
            data_layer=data_layer,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            thread_name=thread_name,
            metadata=merged_metadata if merged_metadata else None,
        )

        observed_identifier = await self._read_thread_author_identifier(
            data_layer=data_layer,
            thread_id=thread_id,
        )

        can_verify_author = callable(getattr(data_layer, "get_thread_author", None))
        if observed_identifier is None and not can_verify_author:
            LOGGER.warning(
                "Discord owner verification skipped (no author reader): thread_id=%s expected_identifier=%s",
                thread_id,
                expected_identifier,
            )
            return True

        if observed_identifier == expected_identifier:
            LOGGER.warning(
                "Discord owner verification passed: thread_id=%s identifier=%s",
                thread_id,
                observed_identifier,
            )
            return True

        LOGGER.warning(
            "Skipping Discord enqueue due to thread authorization mismatch: thread_id=%s expected_identifier=%s observed_identifier=%s",
            thread_id,
            expected_identifier,
            observed_identifier,
        )
        return False

    async def send_outgoing_command(self, channel_id: int, command: OutgoingCommand) -> bool:
        return await send_discord_command(
            client=self._client,
            channel_id=channel_id,
            command=command,
            logger=LOGGER,
        )

    async def send_typing_state(self, channel_id: int, is_running: bool) -> bool:
        if is_running:
            async with self._typing_task_lock:
                existing = self._typing_tasks.get(channel_id)
                if existing is not None and not existing.done():
                    return True
                task = asyncio.create_task(self._typing_pulse_loop(channel_id))
                self._typing_tasks[channel_id] = task
            return True

        task: asyncio.Task[None] | None = None
        async with self._typing_task_lock:
            existing = self._typing_tasks.pop(channel_id, None)
            if existing is not None:
                task = existing

        if task is None:
            return True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.warning(
                "Failed to stop Discord typing task for channel '%s'.",
                channel_id,
            )
        return True

    async def _stop_all_typing_tasks(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._typing_task_lock:
            tasks = list(self._typing_tasks.values())
            self._typing_tasks.clear()

        for task in tasks:
            task.cancel()
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _typing_pulse_loop(self, channel_id: int) -> None:
        try:
            while True:
                channel = await resolve_discord_channel(
                    client=self._client,
                    channel_id=channel_id,
                    logger=LOGGER,
                )
                if channel is None:
                    await asyncio.sleep(2.0)
                    continue

                typing_cm = channel.typing() if hasattr(channel, "typing") else None
                if typing_cm is None:
                    await asyncio.sleep(2.0)
                    continue

                async with typing_cm:
                    await asyncio.sleep(8.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning(
                "Discord typing pulse loop failed for channel '%s'.",
                channel_id,
            )
        finally:
            async with self._typing_task_lock:
                current_task = self._typing_tasks.get(channel_id)
                if current_task is asyncio.current_task():
                    self._typing_tasks.pop(channel_id, None)

    async def _resolve_runtime_auth_owner_user(
        self, data_layer: object | None
    ) -> User | PersistedUser | None:
        if data_layer is None:
            return None

        auth = self._runtime.get_auth()
        if auth is None:
            return None

        identifier = auth.identifier or auth.username
        try:
            persisted_user = await data_layer.get_user(identifier)
        except Exception:
            LOGGER.warning("Failed to query owner user '%s' from data layer.", identifier)
            return None

        if isinstance(persisted_user, PersistedUser):
            return persisted_user

        if persisted_user is not None and self._coerce_user_id(persisted_user) is not None:
            return persisted_user

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

        if isinstance(created_user, PersistedUser):
            return created_user

        if created_user is not None and self._coerce_user_id(created_user) is not None:
            return created_user
        return None

    async def _lookup_user_id_by_identifier(
        self,
        *,
        data_layer: object,
        identifier: str,
    ) -> str | None:
        normalized_identifier = identifier.strip()
        if not normalized_identifier:
            return None

        execute_sql = getattr(data_layer, "execute_sql", None)
        if callable(execute_sql):
            try:
                rows = await execute_sql(
                    query='SELECT "id" AS id FROM users WHERE "identifier" = :identifier LIMIT 1',
                    parameters={"identifier": normalized_identifier},
                )
            except Exception:
                rows = None
            user_id = self._extract_first_row_id(rows)
            if user_id is not None:
                return user_id

        execute_query = getattr(data_layer, "execute_query", None)
        if callable(execute_query):
            try:
                rows = await execute_query(
                    query='SELECT id FROM "User" WHERE identifier = :identifier LIMIT 1',
                    params={"identifier": normalized_identifier},
                )
            except Exception:
                rows = None
            user_id = self._extract_first_row_id(rows)
            if user_id is not None:
                return user_id

        get_user = getattr(data_layer, "get_user", None)
        if callable(get_user):
            try:
                user = await get_user(normalized_identifier)
            except Exception:
                return None
            return self._coerce_user_id(user)

        return None

    def _extract_first_row_id(self, rows: object) -> str | None:
        if not isinstance(rows, list) or not rows:
            return None

        first = rows[0]
        if not isinstance(first, dict):
            return None

        raw_id = first.get("id")
        if raw_id is None:
            raw_id = first.get("user_id")
        if raw_id is None:
            raw_id = first.get("userId")
        if raw_id is None:
            return None

        rendered_id = str(raw_id).strip()
        if not rendered_id:
            return None
        return rendered_id

    async def _resolve_runtime_auth_owner_user_id(self, data_layer: object) -> str | None:
        owner_user = await self._resolve_runtime_auth_owner_user(data_layer)
        return self._coerce_user_id(owner_user)

    async def _resolve_required_runtime_owner_from_auth_config(
        self,
        *,
        data_layer: object,
        thread_id: str,
    ) -> tuple[str, str] | None:
        runtime_identifier = self._resolve_runtime_auth_identifier()
        if runtime_identifier is None:
            LOGGER.warning(
                "Skipping Discord inbound for thread '%s': runtime auth identifier is missing.",
                thread_id,
            )
            return None

        runtime_owner_user_id = await self._resolve_runtime_auth_owner_user_id(data_layer)
        if runtime_owner_user_id is None:
            runtime_owner_user_id = await self._lookup_user_id_by_identifier(
                data_layer=data_layer,
                identifier=runtime_identifier,
            )
        if runtime_owner_user_id is None:
            LOGGER.warning(
                "Skipping Discord inbound for thread '%s': runtime owner id could not be resolved for identifier '%s'.",
                thread_id,
                runtime_identifier,
            )
            return None

        LOGGER.warning(
            "Resolved runtime owner from auth config: thread_id=%s identifier=%s user_id=%s",
            thread_id,
            runtime_identifier,
            runtime_owner_user_id,
        )
        return runtime_owner_user_id, runtime_identifier

    async def _rebind_discord_thread_owner_to_runtime_auth(
        self,
        *,
        thread_id: str,
        message: discord.Message,
        thread_name: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        bind_thread_to_user: bool = False,
        discord_user: User | PersistedUser | None = None,
        resolved_owner_user_id: str | None = None,
    ) -> None:
        del bind_thread_to_user
        del discord_user
        del resolved_owner_user_id
        data_layer = self._data_layer_getter()
        if data_layer is None:
            return

        resolved_owner = await self._resolve_required_runtime_owner_from_auth_config(
            data_layer=data_layer,
            thread_id=thread_id,
        )
        if resolved_owner is None:
            return
        runtime_owner_user_id, _runtime_identifier = resolved_owner

        thread_record = await self._read_existing_thread_record(
            data_layer=data_layer,
            thread_id=thread_id,
        )
        merged_metadata = self._extract_thread_metadata(thread_record)
        thread_metadata = self._build_discord_thread_metadata(message=message, channel_id=None)
        merged_metadata.update(thread_metadata)
        if isinstance(session_metadata, dict):
            merged_metadata.update(session_metadata)

        LOGGER.warning(
            "Discord owner rebind requested: thread_id=%s owner_user_id=%s thread_name=%s",
            thread_id,
            runtime_owner_user_id,
            thread_name,
        )
        await self._update_thread_owner(
            data_layer=data_layer,
            thread_id=thread_id,
            owner_user_id=runtime_owner_user_id,
            thread_name=thread_name,
            metadata=merged_metadata if merged_metadata else None,
        )

    async def _update_thread_owner(
        self,
        *,
        data_layer: object,
        thread_id: str,
        owner_user_id: str,
        thread_name: str | None,
        metadata: dict[str, object] | None,
    ) -> None:
        update_kwargs: dict[str, object] = {
            "thread_id": thread_id,
            "user_id": owner_user_id,
            "metadata": metadata,
        }
        if isinstance(thread_name, str) and thread_name.strip():
            update_kwargs["name"] = thread_name

        LOGGER.warning(
            "Updating Discord thread owner: thread_id=%s owner_user_id=%s has_name=%s has_metadata=%s",
            thread_id,
            owner_user_id,
            "yes" if "name" in update_kwargs else "no",
            "yes" if metadata else "no",
        )
        try:
            await data_layer.update_thread(**update_kwargs)
        except Exception:
            LOGGER.warning("Failed to update Discord thread '%s' owner metadata.", thread_id)
            return

        record = await self._read_existing_thread_record(data_layer=data_layer, thread_id=thread_id)
        if record is None:
            return
        observed_owner_user_id = self._extract_thread_owner_user_id(record)
        observed_owner_identifier = self._extract_thread_owner_identifier(record)
        LOGGER.warning(
            "Post-update Discord thread owner snapshot: thread_id=%s observed_owner_user_id=%s observed_owner_identifier=%s",
            thread_id,
            observed_owner_user_id,
            observed_owner_identifier,
        )
        if observed_owner_user_id != owner_user_id:
            LOGGER.warning(
                "Discord thread owner mismatch after update: thread_id=%s expected_owner_user_id=%s observed_owner_user_id=%s",
                thread_id,
                owner_user_id,
                observed_owner_user_id,
            )

    async def _read_existing_thread_record(
        self,
        *,
        data_layer: object,
        thread_id: str,
    ) -> dict[str, object] | None:
        get_thread = getattr(data_layer, "get_thread", None)
        if not callable(get_thread):
            return None

        try:
            thread = await get_thread(thread_id)
        except Exception:
            LOGGER.warning(
                "Failed to fetch existing metadata for Discord thread '%s'.",
                thread_id,
            )
            return None

        if not isinstance(thread, dict):
            return None
        return thread

    def _extract_thread_metadata(self, thread_record: dict[str, object] | None) -> dict[str, object]:
        if not isinstance(thread_record, dict):
            return {}

        metadata = thread_record.get("metadata")
        if isinstance(metadata, dict):
            return dict(metadata)
        if isinstance(metadata, str):
            try:
                decoded = json.loads(metadata)
            except json.JSONDecodeError:
                return {}
            if isinstance(decoded, dict):
                return decoded
        return {}

    def _extract_thread_owner_user_id(self, thread_record: dict[str, object] | None) -> str | None:
        if not isinstance(thread_record, dict):
            return None

        raw_owner_user_id = thread_record.get("userId")
        if raw_owner_user_id is None:
            raw_owner_user_id = thread_record.get("user_id")
        if raw_owner_user_id is None:
            return None
        rendered_owner_user_id = str(raw_owner_user_id).strip()
        if not rendered_owner_user_id:
            return None
        return rendered_owner_user_id

    def _extract_thread_owner_identifier(self, thread_record: dict[str, object] | None) -> str | None:
        if not isinstance(thread_record, dict):
            return None

        raw_owner_identifier = thread_record.get("userIdentifier")
        if raw_owner_identifier is None:
            raw_owner_identifier = thread_record.get("user_identifier")
        if raw_owner_identifier is None:
            return None
        rendered_owner_identifier = str(raw_owner_identifier).strip()
        if not rendered_owner_identifier:
            return None
        return rendered_owner_identifier

    def _resolve_runtime_auth_identifier(self) -> str | None:
        auth = self._runtime.get_auth()
        if auth is None:
            return None
        identifier = auth.identifier or auth.username
        if not isinstance(identifier, str):
            return None
        normalized_identifier = identifier.strip()
        if not normalized_identifier:
            return None
        return normalized_identifier

    async def _read_thread_author_identifier(
        self,
        *,
        data_layer: object,
        thread_id: str,
    ) -> str | None:
        get_thread_author = getattr(data_layer, "get_thread_author", None)
        if callable(get_thread_author):
            try:
                author_identifier = await get_thread_author(thread_id)
            except Exception:
                author_identifier = None
            if isinstance(author_identifier, str):
                normalized_author_identifier = author_identifier.strip()
                if normalized_author_identifier:
                    return normalized_author_identifier

        thread_record = await self._read_existing_thread_record(
            data_layer=data_layer,
            thread_id=thread_id,
        )
        return self._extract_thread_owner_identifier(thread_record)

    def _coerce_user_id(self, user: object | None) -> str | None:
        if user is None:
            return None
        if isinstance(user, dict):
            raw_id = user.get("id")
            if raw_id is None:
                raw_id = user.get("userId")
            if raw_id is None:
                raw_id = user.get("user_id")
            if raw_id is None:
                return None
            rendered_id = str(raw_id).strip()
            if not rendered_id:
                return None
            return rendered_id
        resolved_id = getattr(user, "id", None)
        if resolved_id is None:
            return None
        rendered_id = str(resolved_id).strip()
        if not rendered_id:
            return None
        return rendered_id

    async def _backfill_all_thread_owners_to_runtime_auth(self) -> None:
        data_layer = self._data_layer_getter()
        if data_layer is None:
            return

        owner_user = await self._resolve_runtime_auth_owner_user(data_layer)
        owner_user_id = self._coerce_user_id(owner_user)
        if owner_user_id is None:
            auth = self._runtime.get_auth()
            if auth is not None:
                identifier = auth.identifier or auth.username
                LOGGER.warning(
                    "Skipping Discord thread owner backfill because auth owner '%s' could not be resolved.",
                    identifier,
                )
            return

        thread_ids = await self._collect_all_thread_ids_for_backfill(data_layer)
        if thread_ids is None:
            LOGGER.warning(
                "Skipping Discord thread owner backfill for unsupported data layer '%s'.",
                type(data_layer).__name__,
            )
            return

        success_count = 0
        failure_count = 0
        for thread_id in thread_ids:
            try:
                await data_layer.update_thread(thread_id=thread_id, user_id=owner_user_id)
                success_count += 1
            except Exception:
                failure_count += 1
                LOGGER.warning(
                    "Failed to backfill owner for thread '%s'.",
                    thread_id,
                )

        LOGGER.info(
            "Discord thread owner backfill completed: owner_user_id=%s total=%d success=%d failed=%d",
            owner_user_id,
            len(thread_ids),
            success_count,
            failure_count,
        )

    async def _collect_all_thread_ids_for_backfill(self, data_layer: object) -> list[str] | None:
        raw_rows: object
        try:
            if hasattr(data_layer, "execute_sql"):
                raw_rows = await data_layer.execute_sql(
                    query='SELECT "id" AS id FROM threads',
                    parameters={},
                )
            elif hasattr(data_layer, "execute_query"):
                raw_rows = await data_layer.execute_query(
                    query='SELECT id FROM "Thread"',
                    params={},
                )
            else:
                return None
        except Exception:
            LOGGER.warning(
                "Failed to fetch thread ids for owner backfill using data layer '%s'.",
                type(data_layer).__name__,
            )
            return []

        if not isinstance(raw_rows, list):
            return []

        ordered_ids: list[str] = []
        seen_ids: set[str] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            thread_id_value = row.get("id")
            if thread_id_value is None:
                continue
            thread_id = str(thread_id_value)
            if not thread_id or thread_id in seen_ids:
                continue
            seen_ids.add(thread_id)
            ordered_ids.append(thread_id)
        return ordered_ids

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
