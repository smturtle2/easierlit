from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import queue
import re
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine
from urllib.parse import unquote, urlparse
from uuid import uuid4

import aiohttp
from chainlit.context import init_http_context
from chainlit.data import get_data_layer
from chainlit.utils import utc_now

from .discord_outgoing import send_discord_command, supports_discord_command
from .errors import AppClosedError, ThreadSessionNotActiveError
from .models import IncomingMessage, OutgoingCommand
from .storage.local import LOCAL_STORAGE_ROUTE_PREFIX

if TYPE_CHECKING:
    from chainlit.session import WebsocketSession

    from .app import EasierlitApp
    from .client import EasierlitClient
    from .settings import EasierlitAuthConfig, EasierlitPersistenceConfig
    from .storage.local import LocalFileStorageClient

LOGGER = logging.getLogger(__name__)

_ELEMENT_DB_COLUMNS = (
    "id",
    "threadId",
    "type",
    "chainlitKey",
    "url",
    "objectKey",
    "name",
    "display",
    "size",
    "language",
    "page",
    "autoPlay",
    "playerConfig",
    "forId",
    "mime",
    "props",
)

_REALTIME_STEP_ACTIONS: dict[str, tuple[str, str]] = {
    "add_message": ("send_step", "Add message"),
    "update_message": ("update_step", "Update"),
    "add_tool": ("send_step", "Add tool"),
    "update_tool": ("update_step", "Update tool"),
}

_DATA_LAYER_STEP_WRITERS: dict[str, str] = {
    "add_message": "create_step",
    "add_tool": "create_step",
    "update_message": "update_step",
    "update_tool": "update_step",
}


class RuntimeRegistry:
    def __init__(
        self,
        *,
        data_layer_getter: Callable[[], Any | None] = get_data_layer,
        init_http_context_fn: Callable[..., Any] = init_http_context,
        utc_now_fn: Callable[[], Any] = utc_now,
    ) -> None:
        self._client: EasierlitClient | None = None
        self._app: EasierlitApp | None = None
        self._auth: EasierlitAuthConfig | None = None
        self._persistence: EasierlitPersistenceConfig | None = None
        self._discord_token: str | None = None

        self._thread_to_session: dict[str, str] = {}
        self._session_to_thread: dict[str, str] = {}
        self._thread_to_discord_channel: dict[str, int] = {}

        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._dispatcher_lane_tasks: list[asyncio.Task[None]] = []
        self._dispatcher_lane_queues: list[asyncio.Queue[OutgoingCommand]] = []
        self._max_outgoing_workers = 4

        self._data_layer_getter = data_layer_getter
        self._init_http_context_fn = init_http_context_fn
        self._utc_now_fn = utc_now_fn
        self._discord_sender: Callable[[int, OutgoingCommand], Awaitable[bool]] | None = None

        self._lock = threading.RLock()

    def bind(
        self,
        *,
        client: EasierlitClient,
        app: EasierlitApp,
        auth: EasierlitAuthConfig | None = None,
        persistence: EasierlitPersistenceConfig | None = None,
        discord_token: str | None = None,
        max_outgoing_workers: int = 4,
    ) -> None:
        if not isinstance(max_outgoing_workers, int) or max_outgoing_workers < 1:
            raise ValueError("max_outgoing_workers must be an integer >= 1.")

        with self._lock:
            self._client = client
            self._app = app
            self._auth = auth
            self._persistence = persistence
            self._discord_token = discord_token
            self._max_outgoing_workers = max_outgoing_workers
            self._thread_to_session.clear()
            self._session_to_thread.clear()
            self._thread_to_discord_channel.clear()
            self._dispatcher_task = None
            self._dispatcher_lane_tasks = []
            self._dispatcher_lane_queues = []
            self._discord_sender = None

    def unbind(self) -> None:
        with self._lock:
            self._client = None
            self._app = None
            self._auth = None
            self._persistence = None
            self._discord_token = None
            self._thread_to_session.clear()
            self._session_to_thread.clear()
            self._thread_to_discord_channel.clear()
            self._main_loop = None
            self._dispatcher_task = None
            self._dispatcher_lane_tasks = []
            self._dispatcher_lane_queues = []
            self._max_outgoing_workers = 4
            self._discord_sender = None

    def get_client(self) -> EasierlitClient | None:
        return self._client

    def get_app(self) -> EasierlitApp | None:
        return self._app

    def get_auth(self) -> EasierlitAuthConfig | None:
        return self._auth

    def get_persistence(self) -> EasierlitPersistenceConfig | None:
        return self._persistence

    def get_discord_token(self) -> str | None:
        return self._discord_token

    def set_discord_sender(
        self,
        sender: Callable[[int, OutgoingCommand], Awaitable[bool]] | None,
    ) -> None:
        with self._lock:
            self._discord_sender = sender

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._main_loop = loop

    def get_main_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._main_loop

    def run_coroutine_sync(self, coro: Coroutine[Any, Any, Any]) -> Any:
        loop = self._main_loop
        if loop and loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is loop:
                raise RuntimeError(
                    "Cannot synchronously wait for a coroutine from the Chainlit event loop."
                )

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result()

        return asyncio.run(coro)

    def register_session(self, thread_id: str, session_id: str) -> None:
        with self._lock:
            self._thread_to_session[thread_id] = session_id
            self._session_to_thread[session_id] = thread_id

    def register_discord_channel(self, thread_id: str, channel_id: int) -> None:
        with self._lock:
            self._thread_to_discord_channel[thread_id] = channel_id

    def unregister_session(self, session_id: str) -> None:
        with self._lock:
            thread_id = self._session_to_thread.pop(session_id, None)
            if thread_id is not None:
                self._thread_to_session.pop(thread_id, None)

    def get_session_id_for_thread(self, thread_id: str) -> str | None:
        with self._lock:
            return self._thread_to_session.get(thread_id)

    def get_discord_channel_for_thread(self, thread_id: str) -> int | None:
        with self._lock:
            return self._thread_to_discord_channel.get(thread_id)

    def is_discord_thread(self, thread_id: str) -> bool:
        if not isinstance(thread_id, str):
            return False
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            return False
        return self.get_discord_channel_for_thread(normalized_thread_id) is not None

    async def send_to_discord(
        self,
        *,
        thread_id: str,
        content: str,
        elements: list[Any] | None = None,
    ) -> bool:
        if not isinstance(thread_id, str):
            return False
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            return False
        if not isinstance(content, str):
            return False
        resolved_elements = elements or []
        if not content.strip() and not resolved_elements:
            return False

        discord_channel_id = self.get_discord_channel_for_thread(normalized_thread_id)
        if discord_channel_id is None:
            return False

        command = OutgoingCommand(
            command="add_message",
            thread_id=normalized_thread_id,
            content=content,
            elements=resolved_elements,
            author="Assistant",
        )
        return await self._apply_discord_command(discord_channel_id, command)

    def dispatch_incoming(self, message: IncomingMessage) -> None:
        app = self._app
        client = self._client
        if app is None or client is None:
            LOGGER.warning("Skipping incoming dispatch because runtime is not bound.")
            return
        if app.is_closed():
            raise AppClosedError("Cannot dispatch incoming message to a closed app.")
        client.dispatch_incoming(message)

    async def start_dispatcher(self) -> None:
        if self._app is None:
            raise RuntimeError("No active app bound to runtime.")

        if self._dispatcher_task and not self._dispatcher_task.done():
            return

        self._dispatcher_lane_queues = [
            asyncio.Queue() for _ in range(self._max_outgoing_workers)
        ]
        self._dispatcher_lane_tasks = [
            asyncio.create_task(self._dispatch_outgoing_lane_loop(lane_queue))
            for lane_queue in self._dispatcher_lane_queues
        ]
        self._dispatcher_task = asyncio.create_task(self._dispatch_outgoing_router_loop())

    async def stop_dispatcher(self) -> None:
        tasks: list[asyncio.Task[Any]] = []

        router_task = self._dispatcher_task
        if router_task is not None:
            router_task.cancel()
            tasks.append(router_task)

        for lane_task in self._dispatcher_lane_tasks:
            lane_task.cancel()
            tasks.append(lane_task)

        if not tasks:
            return

        await asyncio.gather(*tasks, return_exceptions=True)
        self._dispatcher_task = None
        self._dispatcher_lane_tasks = []
        self._dispatcher_lane_queues = []

    async def _dispatch_outgoing_router_loop(self) -> None:
        app = self._app
        if app is None:
            return

        while not app.is_closed():
            try:
                command = await asyncio.to_thread(app._pop_outgoing, 0.2)
            except queue.Empty:
                continue

            if command.command == "close":
                await self._broadcast_dispatcher_close_signal()
                break

            lane_queue = self._resolve_outgoing_lane_queue(command)
            if lane_queue is None:
                continue
            await lane_queue.put(command)

    async def _dispatch_outgoing_lane_loop(
        self,
        lane_queue: asyncio.Queue[OutgoingCommand],
    ) -> None:
        while True:
            command = await lane_queue.get()
            if command.command == "close":
                return

            try:
                await self.apply_outgoing_command(command)
            except Exception:
                LOGGER.exception(
                    "Failed to apply outgoing command: %s",
                    command.model_dump(),
                )

    async def _broadcast_dispatcher_close_signal(self) -> None:
        if not self._dispatcher_lane_queues:
            return

        close_command = OutgoingCommand(command="close")
        for lane_queue in self._dispatcher_lane_queues:
            await lane_queue.put(close_command)

    def _resolve_outgoing_lane_queue(
        self,
        command: OutgoingCommand,
    ) -> asyncio.Queue[OutgoingCommand] | None:
        if not self._dispatcher_lane_queues:
            return None
        lane_index = self._resolve_outgoing_lane_index(command.thread_id)
        return self._dispatcher_lane_queues[lane_index]

    def _resolve_outgoing_lane_index(self, thread_id: str | None) -> int:
        lane_count = len(self._dispatcher_lane_queues)
        if lane_count < 2:
            return 0
        if not thread_id:
            return 0
        return hash(thread_id) % lane_count

    def _resolve_session(self, thread_id: str) -> WebsocketSession | None:
        from chainlit.session import WebsocketSession

        session_id = self.get_session_id_for_thread(thread_id)
        if session_id is None:
            return None

        session = WebsocketSession.get_by_id(session_id)
        if session is None:
            self.unregister_session(session_id)
            return None

        return session

    async def set_thread_task_state(self, thread_id: str, is_running: bool) -> bool:
        session = self._resolve_session(thread_id)
        if session is None:
            return False

        try:
            from chainlit.context import context, init_ws_context

            init_ws_context(session)
            if is_running:
                await context.emitter.task_start()
            else:
                await context.emitter.task_end()
            return True
        except Exception:
            LOGGER.exception(
                "Failed to update task state for thread '%s' (is_running=%s).",
                thread_id,
                is_running,
            )
            return False

    @staticmethod
    def _require_thread_id(command: OutgoingCommand) -> str:
        thread_id = command.thread_id
        if not thread_id:
            raise ValueError("Outgoing command is missing thread_id.")
        return thread_id

    @staticmethod
    def _require_message_id(command: OutgoingCommand, *, action: str) -> str:
        message_id = command.message_id
        if not message_id:
            raise ValueError(f"{action} command requires message_id.")
        return message_id

    @staticmethod
    def _is_tool_command(command_name: str) -> bool:
        return command_name in ("add_tool", "update_tool")

    @staticmethod
    def _is_create_command(command_name: str) -> bool:
        return command_name in ("add_message", "add_tool")

    @staticmethod
    def _is_update_command(command_name: str) -> bool:
        return command_name in ("update_message", "update_tool")

    async def apply_outgoing_command(self, command: OutgoingCommand) -> None:
        if command.command == "close":
            return

        thread_id = self._require_thread_id(command)
        command = await self._prepare_command_elements(command, thread_id=thread_id)

        session_handled = False
        session = self._resolve_session(thread_id)
        if session is not None:
            await self._apply_realtime_command(session, command)
            session_handled = True

        data_layer = self._data_layer_getter()
        if data_layer:
            await self._apply_data_layer_command(command)
            return

        if session_handled:
            return

        raise ThreadSessionNotActiveError(
            f"Thread '{thread_id}' has no active session and no data layer fallback."
        )

    async def _prepare_command_elements(
        self,
        command: OutgoingCommand,
        *,
        thread_id: str,
    ) -> OutgoingCommand:
        if command.command == "delete":
            return command
        if not command.elements:
            return command
        if self.__resolve_local_storage_provider() is None:
            return command

        message_id = self._require_message_id(command, action=command.command)
        prepared: list[dict[str, Any]] = []
        for index, element in enumerate(command.elements):
            normalized = await self._prepare_element_for_local_storage(
                element=element,
                thread_id=thread_id,
                message_id=message_id,
                index=index,
            )
            if normalized is None:
                continue
            prepared.append(normalized)

        return command.model_copy(update={"elements": prepared})

    async def _apply_realtime_command(
        self, session: WebsocketSession, command: OutgoingCommand
    ) -> None:
        from chainlit.context import context, init_ws_context

        init_ws_context(session)
        thread_id = self._require_thread_id(command)

        realtime_step_action = _REALTIME_STEP_ACTIONS.get(command.command)
        if realtime_step_action is not None:
            emitter_name, action_name = realtime_step_action
            message_id = self._require_message_id(command, action=action_name)
            step_dict = self._build_step_payload(
                command=command,
                thread_id=thread_id,
                message_id=message_id,
            )
            emitter = getattr(context.emitter, emitter_name, None)
            if not callable(emitter):
                raise RuntimeError(
                    f"Chainlit realtime emitter '{emitter_name}' is not available."
                )
            await emitter(step_dict)
            await self._emit_realtime_elements(command.elements)
            return

        if command.command == "delete":
            message_id = self._require_message_id(command, action="Delete")
            await context.emitter.delete_step({"id": message_id, "threadId": thread_id})
            return

        raise ValueError(f"Unsupported command: {command.command}")

    async def _emit_realtime_elements(self, elements: list[Any]) -> None:
        if not elements:
            return

        from chainlit.context import context

        for element in elements:
            payload = element if isinstance(element, dict) else self._coerce_element_dict(element)
            if not isinstance(payload, dict):
                continue
            await context.emitter.send_element(payload)

    def _build_step_payload(
        self,
        *,
        command: OutgoingCommand,
        thread_id: str,
        message_id: str,
        timestamp: Any | None = None,
    ) -> dict[str, Any]:
        created_at = timestamp if timestamp is not None else self._utc_now_fn()
        is_tool_command = self._is_tool_command(command.command)
        step_type = command.step_type or ("tool" if is_tool_command else "assistant_message")
        return {
            "id": message_id,
            "threadId": thread_id,
            "name": command.author,
            "type": step_type,
            "output": command.content or "",
            "createdAt": created_at,
            "start": created_at,
            "end": created_at,
            "streaming": False,
            "isError": False,
            "waitForAnswer": False,
            "metadata": command.metadata or {},
        }

    async def _apply_data_layer_command(self, command: OutgoingCommand) -> None:
        data_layer = self._data_layer_getter()
        if not data_layer:
            raise RuntimeError("Data layer unexpectedly missing.")

        thread_id = self._require_thread_id(command)
        self._init_data_layer_http_context(thread_id)

        if command.command == "delete":
            message_id = self._require_message_id(command, action="Delete")
            await data_layer.delete_step(message_id)
            return

        writer_name = _DATA_LAYER_STEP_WRITERS.get(command.command)
        if writer_name is None:
            raise ValueError(f"Unsupported command: {command.command}")
        step_writer = getattr(data_layer, writer_name, None)
        if not callable(step_writer):
            raise RuntimeError(
                f"Data layer does not support required method '{writer_name}' for '{command.command}'."
            )

        message_id = self._require_message_id(command, action=command.command)
        timestamp = self._utc_now_fn()
        step_dict = self._build_step_payload(
            command=command,
            thread_id=thread_id,
            message_id=message_id,
            timestamp=timestamp,
        )
        await step_writer(step_dict)
        await self._persist_data_layer_elements(
            data_layer=data_layer,
            thread_id=thread_id,
            message_id=message_id,
            elements=command.elements,
        )

    async def _persist_data_layer_elements(
        self,
        *,
        data_layer: Any,
        thread_id: str,
        message_id: str,
        elements: list[Any],
    ) -> None:
        if not elements:
            return

        if self._can_upsert_element_directly(data_layer):
            for element in elements:
                element_dict = self._prepare_element_record(
                    element=element,
                    thread_id=thread_id,
                    message_id=message_id,
                )
                if element_dict is None:
                    continue
                await self._upsert_sqlalchemy_element(data_layer=data_layer, element_dict=element_dict)
            return

        create_element = getattr(data_layer, "create_element", None)
        if not callable(create_element):
            return

        for element in elements:
            if hasattr(element, "for_id"):
                element.for_id = message_id
            if hasattr(element, "thread_id"):
                element.thread_id = thread_id
            await create_element(element)

    def _can_upsert_element_directly(self, data_layer: Any) -> bool:
        execute_sql = getattr(data_layer, "execute_sql", None)
        return callable(execute_sql)

    async def _upsert_sqlalchemy_element(
        self,
        *,
        data_layer: Any,
        element_dict: dict[str, Any],
    ) -> None:
        execute_sql = getattr(data_layer, "execute_sql", None)
        if not callable(execute_sql):
            return

        payload = {
            column: element_dict.get(column)
            for column in _ELEMENT_DB_COLUMNS
            if element_dict.get(column) is not None
        }
        if not payload.get("id"):
            return

        props = payload.get("props")
        if isinstance(props, dict):
            payload["props"] = json.dumps(props, ensure_ascii=False)
        elif props is not None and not isinstance(props, str):
            payload["props"] = json.dumps(props, ensure_ascii=False)

        columns = ", ".join(f'"{column}"' for column in payload.keys())
        placeholders = ", ".join(f":{column}" for column in payload.keys())
        updates = ", ".join(
            f'"{column}" = :{column}' for column in payload.keys() if column != "id"
        )
        if updates:
            query = (
                f"INSERT INTO elements ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates};"
            )
        else:
            query = (
                f"INSERT INTO elements ({columns}) VALUES ({placeholders}) "
                'ON CONFLICT (id) DO NOTHING;'
            )
        await execute_sql(query=query, parameters=payload)

    async def _prepare_element_for_local_storage(
        self,
        *,
        element: Any,
        thread_id: str,
        message_id: str,
        index: int,
    ) -> dict[str, Any] | None:
        local_storage = self.__resolve_local_storage_provider()
        if local_storage is None:
            return self._prepare_element_record(
                element=element,
                thread_id=thread_id,
                message_id=message_id,
            )

        element_dict = self._coerce_element_dict(element)
        element_id = self._coerce_text(element_dict.get("id")) or str(uuid4())
        element_name = self._coerce_text(element_dict.get("name")) or f"element-{index + 1}"
        mime = self._coerce_text(element_dict.get("mime")) or self._guess_mime_type(element_name)
        element_type = self._coerce_text(element_dict.get("type")) or self._infer_element_type_from_mime(
            mime
        )
        object_key = self._resolve_element_object_key(element_dict)
        url = self._coerce_text(element_dict.get("url"))

        async def _resolve_uploaded_reference(
            uploaded: dict[str, Any],
            *,
            fallback_object_key: str,
        ) -> tuple[str, str | None]:
            resolved_object_key = (
                self._coerce_text(uploaded.get("object_key")) or fallback_object_key
            )
            uploaded_url = self._coerce_text(uploaded.get("url"))
            if uploaded_url:
                return resolved_object_key, uploaded_url
            try:
                return resolved_object_key, await local_storage.get_read_url(resolved_object_key)
            except Exception:
                return resolved_object_key, None

        if object_key:
            try:
                url = await local_storage.get_read_url(object_key)
            except Exception:
                payload = await self._resolve_element_payload(element_dict, local_storage=local_storage)
                if payload is None:
                    url = None
                else:
                    try:
                        uploaded = await local_storage.upload_file(
                            object_key=object_key,
                            data=payload,
                            mime=mime,
                            overwrite=True,
                        )
                        object_key, url = await _resolve_uploaded_reference(
                            uploaded, fallback_object_key=object_key
                        )
                    except Exception:
                        url = None
        else:
            payload = await self._resolve_element_payload(element_dict, local_storage=local_storage)
            if payload is None:
                LOGGER.warning(
                    "Skipping element '%s' for message '%s': no readable source.",
                    element_name,
                    message_id,
                )
                return None
            object_key = self._build_generated_object_key(
                thread_id=thread_id,
                message_id=message_id,
                element_id=element_id,
                element_name=element_name,
            )
            uploaded = await local_storage.upload_file(
                object_key=object_key,
                data=payload,
                mime=mime,
                overwrite=True,
            )
            object_key, url = await _resolve_uploaded_reference(
                uploaded, fallback_object_key=object_key
            )

        if not object_key and not url:
            LOGGER.warning(
                "Skipping element '%s' for message '%s': failed to resolve objectKey/url.",
                element_name,
                message_id,
            )
            return None

        normalized = {
            "id": element_id,
            "threadId": thread_id,
            "type": element_type,
            "url": url,
            "objectKey": object_key,
            "name": element_name,
            "display": self._coerce_text(element_dict.get("display")) or "inline",
            "size": self._coerce_text(element_dict.get("size")),
            "language": self._coerce_text(element_dict.get("language")),
            "page": element_dict.get("page"),
            "autoPlay": element_dict.get("autoPlay"),
            "playerConfig": element_dict.get("playerConfig"),
            "forId": message_id,
            "mime": mime,
            "props": element_dict.get("props"),
        }

        return {key: value for key, value in normalized.items() if value is not None}

    async def _resolve_element_payload(
        self,
        element_dict: dict[str, Any],
        *,
        local_storage: "LocalFileStorageClient",
    ) -> bytes | None:
        path = self._coerce_text(element_dict.get("path"))
        if path:
            file_path = Path(path).expanduser()
            if not file_path.is_absolute():
                file_path = Path.cwd() / file_path
            if file_path.is_file():
                return await asyncio.to_thread(file_path.read_bytes)
            return None

        content = element_dict.get("content")
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)
        if isinstance(content, str):
            return content.encode("utf-8")

        url = self._coerce_text(element_dict.get("url"))
        if not url:
            return None

        local_object_key = self._extract_local_route_object_key(url)
        if local_object_key:
            try:
                local_file_path = local_storage.resolve_file_path(local_object_key)
            except Exception:
                local_file_path = None
            if local_file_path is not None and local_file_path.is_file():
                return await asyncio.to_thread(local_file_path.read_bytes)

        return await self._download_url_bytes(url)

    async def _download_url_bytes(self, url: str) -> bytes | None:
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
            LOGGER.exception("Failed to download element URL: %s", url)
            return None

    def __resolve_local_storage_provider(self) -> "LocalFileStorageClient" | None:
        persistence = self._persistence
        if persistence is None or not persistence.enabled:
            return None

        from .settings import _resolve_local_storage_provider

        try:
            return _resolve_local_storage_provider(persistence)
        except (TypeError, ValueError):
            return None

    def _prepare_element_record(
        self,
        *,
        element: Any,
        thread_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        if not isinstance(element, dict):
            element = self._coerce_element_dict(element)
        if not isinstance(element, dict):
            return None

        element_id = self._coerce_text(element.get("id")) or str(uuid4())

        normalized = dict(element)
        normalized["id"] = element_id
        normalized["threadId"] = self._coerce_text(normalized.get("threadId")) or thread_id
        normalized["forId"] = self._coerce_text(normalized.get("forId")) or message_id
        normalized["type"] = self._coerce_text(normalized.get("type")) or "file"
        normalized["display"] = self._coerce_text(normalized.get("display")) or "inline"
        normalized["name"] = self._coerce_text(normalized.get("name")) or "file"

        if "object_key" in normalized and "objectKey" not in normalized:
            normalized["objectKey"] = normalized.get("object_key")
        if "chainlit_key" in normalized and "chainlitKey" not in normalized:
            normalized["chainlitKey"] = normalized.get("chainlit_key")
        if "for_id" in normalized and "forId" not in normalized:
            normalized["forId"] = normalized.get("for_id")
        if "thread_id" in normalized and "threadId" not in normalized:
            normalized["threadId"] = normalized.get("thread_id")

        normalized.pop("path", None)
        normalized.pop("content", None)

        return {key: value for key, value in normalized.items() if value is not None}

    def _coerce_element_dict(self, element: Any) -> dict[str, Any]:
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
            "id": "id",
            "threadId": "thread_id",
            "type": "type",
            "chainlitKey": "chainlit_key",
            "url": "url",
            "objectKey": "object_key",
            "path": "path",
            "content": "content",
            "name": "name",
            "display": "display",
            "size": "size",
            "language": "language",
            "page": "page",
            "autoPlay": "auto_play",
            "playerConfig": "player_config",
            "forId": "for_id",
            "mime": "mime",
            "props": "props",
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

    def _resolve_element_object_key(self, element_dict: dict[str, Any]) -> str | None:
        object_key = self._coerce_text(element_dict.get("objectKey"))
        if object_key:
            return object_key
        object_key = self._coerce_text(element_dict.get("object_key"))
        if object_key:
            return object_key
        url = self._coerce_text(element_dict.get("url"))
        if not url:
            return None
        return self._extract_local_route_object_key(url)

    def _extract_local_route_object_key(self, url: str) -> str | None:
        path = urlparse(url).path or ""
        prefix = f"{LOCAL_STORAGE_ROUTE_PREFIX}/"
        marker = path.find(prefix)
        if marker < 0:
            return None
        object_key = path[marker + len(prefix) :]
        if not object_key:
            return None
        decoded = unquote(object_key).strip("/")
        return decoded or None

    def _build_generated_object_key(
        self,
        *,
        thread_id: str,
        message_id: str,
        element_id: str,
        element_name: str,
    ) -> str:
        safe_thread = self._safe_path_segment(thread_id)
        safe_message = self._safe_path_segment(message_id)
        safe_element = self._safe_path_segment(element_id)
        safe_name = self._safe_file_name(element_name)
        return f"{safe_thread}/{safe_message}/{safe_element}/{safe_name}"

    def _safe_path_segment(self, value: str) -> str:
        rendered = self._coerce_text(value) or "item"
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", rendered).strip("-._")
        return sanitized or "item"

    def _safe_file_name(self, value: str) -> str:
        raw_name = Path(value).name
        if not raw_name:
            raw_name = "file"
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-._")
        return sanitized or "file"

    def _guess_mime_type(self, name: str) -> str:
        guessed, _ = mimetypes.guess_type(name)
        if guessed:
            return guessed
        return "application/octet-stream"

    def _infer_element_type_from_mime(self, mime: str) -> str:
        normalized = (mime or "").lower()
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
        if isinstance(value, (bytes, bytearray, dict, list, tuple, set)):
            return None
        rendered = str(value).strip()
        return rendered or None

    async def _apply_discord_command(self, channel_id: int, command: OutgoingCommand) -> bool:
        if not supports_discord_command(command.command):
            return False

        discord_sender = self._discord_sender
        if discord_sender is not None:
            try:
                return await discord_sender(channel_id, command)
            except Exception:
                LOGGER.exception("Registered Discord sender failed for channel %s.", channel_id)
                return False

        try:
            from chainlit.discord.app import client
        except Exception:
            LOGGER.exception("Failed to import Chainlit Discord client.")
            return False

        return await send_discord_command(
            client=client,
            channel_id=channel_id,
            command=command,
            logger=LOGGER,
        )

    def _init_data_layer_http_context(self, thread_id: str) -> None:
        self._init_http_context_fn(thread_id=thread_id, client_type="webapp")


_RUNTIME = RuntimeRegistry()


def get_runtime() -> RuntimeRegistry:
    return _RUNTIME
