from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from chainlit.data import get_data_layer
from chainlit.types import Pagination, ThreadFilter
from chainlit.user import User

from .errors import AppClosedError, DataPersistenceNotEnabledError
from .models import IncomingMessage, OutgoingCommand
from .runtime import get_runtime


class EasierlitApp:
    """
    Communication bridge between Chainlit callbacks and user run_func.

    Easierlit runs with a single thread worker. This object is the primary API
    surface for worker message and thread CRUD operations.
    """

    _THOUGHT_TOOL_NAME = "Reasoning"
    _MESSAGE_STEP_TYPES = frozenset(
        {
            "user_message",
            "assistant_message",
            "system_message",
            "tool",
        }
    )

    def __init__(
        self,
        *,
        runtime=None,
        data_layer_getter: Callable[[], Any | None] = get_data_layer,
        uuid_factory: Callable[[], Any] = uuid4,
    ):
        self._incoming_queue: queue.Queue[IncomingMessage | None] = queue.Queue()
        self._outgoing_queue: queue.Queue[OutgoingCommand] = queue.Queue()
        self._closed = threading.Event()
        self._runtime = runtime if runtime is not None else get_runtime()
        self._data_layer_getter = data_layer_getter
        self._uuid_factory = uuid_factory

    def recv(self, timeout: float | None = None) -> IncomingMessage:
        if self._closed.is_set():
            raise AppClosedError("EasierlitApp is closed.")

        try:
            if timeout is None:
                item = self._incoming_queue.get()
            else:
                item = self._incoming_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("Timed out waiting for an incoming message.") from exc

        if item is None:
            raise AppClosedError("EasierlitApp is closed.")

        return item

    async def arecv(self, timeout: float | None = None) -> IncomingMessage:
        return await asyncio.to_thread(self.recv, timeout)

    def add_message(
        self,
        thread_id: str,
        content: str,
        author: str = "Assistant",
        metadata: dict | None = None,
    ) -> str:
        """Enqueue an assistant message and return the generated message id."""
        message_id = str(self._uuid_factory())
        self._put_outgoing(
            OutgoingCommand(
                command="add_message",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                author=author,
                metadata=metadata or {},
            )
        )
        return message_id

    def add_tool(
        self,
        thread_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
    ) -> str:
        """Enqueue a tool-call step and return the generated message id."""
        message_id = str(self._uuid_factory())
        self._put_outgoing(
            OutgoingCommand(
                command="add_tool",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                author=tool_name,
                metadata=metadata or {},
            )
        )
        return message_id

    def add_thought(
        self,
        thread_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> str:
        """Enqueue a reasoning step as a tool-call step."""
        return self.add_tool(
            thread_id=thread_id,
            tool_name=self._THOUGHT_TOOL_NAME,
            content=content,
            metadata=metadata,
        )

    def update_message(
        self,
        thread_id: str,
        message_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        self._put_outgoing(
            OutgoingCommand(
                command="update_message",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                metadata=metadata or {},
            )
        )

    def update_tool(
        self,
        thread_id: str,
        message_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        self._put_outgoing(
            OutgoingCommand(
                command="update_tool",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                author=tool_name,
                metadata=metadata or {},
            )
        )

    def update_thought(
        self,
        thread_id: str,
        message_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        self.update_tool(
            thread_id=thread_id,
            message_id=message_id,
            tool_name=self._THOUGHT_TOOL_NAME,
            content=content,
            metadata=metadata,
        )

    def delete_message(self, thread_id: str, message_id: str) -> None:
        self._put_outgoing(
            OutgoingCommand(
                command="delete",
                thread_id=thread_id,
                message_id=message_id,
            )
        )

    def list_threads(
        self,
        first: int = 20,
        cursor: str | None = None,
        search: str | None = None,
        user_identifier: str | None = None,
    ):
        data_layer = self._get_data_layer_or_raise()

        async def _list_threads():
            user_id = None
            if user_identifier is not None:
                persisted_user = await data_layer.get_user(user_identifier)
                if persisted_user is None:
                    raise ValueError(f"User '{user_identifier}' not found.")
                user_id = persisted_user.id

            pagination = Pagination(first=first, cursor=cursor)
            filters = ThreadFilter(search=search, userId=user_id)
            threads = await data_layer.list_threads(pagination, filters)
            return self._normalize_threads_tags(threads)

        return self._runtime.run_coroutine_sync(_list_threads())

    def get_thread(self, thread_id: str) -> dict:
        data_layer = self._get_data_layer_or_raise()

        async def _get_thread():
            thread = await data_layer.get_thread(thread_id)
            if thread is None:
                raise ValueError(f"Thread '{thread_id}' not found.")
            return self._normalize_thread_tags(thread)

        return self._runtime.run_coroutine_sync(_get_thread())

    def get_messages(self, thread_id: str) -> dict:
        """Return thread metadata and one ordered list of messages/tool steps."""
        thread = self.get_thread(thread_id)
        return self._build_messages_payload(thread)

    def timeline(self, thread_id: str) -> dict:
        """Backward-compatible alias for `get_messages`."""
        return self.get_messages(thread_id)

    def get_thread_timeline(self, thread_id: str) -> dict:
        """Backward-compatible alias for `get_messages`."""
        return self.get_messages(thread_id)

    def get_thread_messages_and_steps(self, thread_id: str) -> dict:
        """Backward-compatible alias for `get_messages`."""
        return self.get_messages(thread_id)

    def get_timeline(self, thread_id: str) -> dict:
        """Backward-compatible alias for `get_messages`."""
        return self.get_messages(thread_id)

    def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self._write_thread(
            thread_id=thread_id,
            name=name,
            metadata=metadata,
            tags=tags,
            require_existing=True,
        )

    def new_thread(
        self,
        name: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> str:
        data_layer = self._get_data_layer_or_raise()
        prepared_tags = self._prepare_tags_for_update(tags, data_layer)

        async def _new_thread() -> str:
            owner_user_id = await self._resolve_default_owner_user_id(data_layer)
            for _ in range(16):
                thread_id = str(self._uuid_factory())
                existing = await data_layer.get_thread(thread_id)
                if existing is not None:
                    continue

                await data_layer.update_thread(
                    thread_id=thread_id,
                    name=name,
                    user_id=owner_user_id,
                    metadata=metadata,
                    tags=prepared_tags,
                )
                return thread_id

            raise RuntimeError("Failed to allocate unique thread_id.")

        return self._runtime.run_coroutine_sync(_new_thread())

    def _write_thread(
        self,
        thread_id: str,
        name: str | None,
        metadata: dict | None,
        tags: list[str] | None,
        require_existing: bool,
    ) -> None:
        data_layer = self._get_data_layer_or_raise()
        prepared_tags = self._prepare_tags_for_update(tags, data_layer)

        async def _write_thread():
            existing = await data_layer.get_thread(thread_id)
            if require_existing and existing is None:
                raise ValueError(f"Thread '{thread_id}' not found.")
            if not require_existing and existing is not None:
                raise ValueError(f"Thread '{thread_id}' already exists.")

            owner_user_id = await self._resolve_default_owner_user_id(data_layer)
            await data_layer.update_thread(
                thread_id=thread_id,
                name=name,
                user_id=owner_user_id,
                metadata=metadata,
                tags=prepared_tags,
            )

        self._runtime.run_coroutine_sync(_write_thread())

    def delete_thread(self, thread_id: str) -> None:
        data_layer = self._get_data_layer_or_raise()

        async def _delete_thread():
            await data_layer.delete_thread(thread_id)

        self._runtime.run_coroutine_sync(_delete_thread())

    def close(self) -> None:
        if self._closed.is_set():
            return

        self._closed.set()
        self._incoming_queue.put_nowait(None)
        self._outgoing_queue.put_nowait(OutgoingCommand(command="close"))

    def is_closed(self) -> bool:
        return self._closed.is_set()

    def _enqueue_incoming(self, message: IncomingMessage) -> None:
        if self._closed.is_set():
            raise AppClosedError("Cannot enqueue incoming message to a closed app.")
        self._incoming_queue.put_nowait(message)

    def _pop_outgoing(self, timeout: float | None = 0.1) -> OutgoingCommand:
        if timeout is None:
            return self._outgoing_queue.get()
        return self._outgoing_queue.get(timeout=timeout)

    def _put_outgoing(self, command: OutgoingCommand) -> None:
        if self._closed.is_set():
            raise AppClosedError("Cannot send command to a closed app.")
        self._outgoing_queue.put_nowait(command)

    def _get_data_layer_or_raise(self):
        data_layer = self._data_layer_getter()
        if data_layer is None:
            raise DataPersistenceNotEnabledError(
                "Data persistence is not enabled. Configure Chainlit data layer first."
            )
        return data_layer

    def _is_sqlite_sqlalchemy_data_layer(self, data_layer: Any) -> bool:
        conninfo = getattr(data_layer, "_conninfo", None)
        if isinstance(conninfo, str) and conninfo.lower().startswith("sqlite"):
            return True

        engine = getattr(data_layer, "engine", None)
        url = getattr(engine, "url", None)
        drivername = getattr(url, "drivername", None)
        return isinstance(drivername, str) and drivername.lower().startswith("sqlite")

    def _prepare_tags_for_update(
        self,
        tags: list[str] | None,
        data_layer: Any,
    ) -> list[str] | str | None:
        if tags is None:
            return None
        if self._is_sqlite_sqlalchemy_data_layer(data_layer):
            return json.dumps(tags)
        return tags

    async def _resolve_default_owner_user_id(self, data_layer: Any) -> str | None:
        auth = self._runtime.get_auth()
        if auth is None:
            return None

        identifier = auth.identifier or auth.username
        persisted_user = await data_layer.get_user(identifier)
        if persisted_user is not None:
            return persisted_user.id

        if not hasattr(data_layer, "create_user"):
            return None

        created_user = await data_layer.create_user(
            User(
                identifier=identifier,
                metadata=auth.metadata or {},
            )
        )
        if created_user is None:
            return None

        return created_user.id

    def _normalize_thread_tags(self, thread: dict) -> dict:
        tags = thread.get("tags")
        if not isinstance(tags, str):
            return thread

        try:
            decoded = json.loads(tags)
        except json.JSONDecodeError:
            return thread

        if not isinstance(decoded, list):
            return thread

        normalized = dict(thread)
        normalized["tags"] = decoded
        return normalized

    def _normalize_threads_tags(self, threads):
        if not hasattr(threads, "data"):
            return threads

        threads.data = [
            self._normalize_thread_tags(thread)
            if isinstance(thread, dict)
            else thread
            for thread in threads.data
        ]
        return threads

    def _build_messages_payload(self, thread: dict) -> dict:
        raw_steps = thread.get("steps")
        step_items = raw_steps if isinstance(raw_steps, list) else []
        messages = self._filter_message_steps(step_items)

        raw_elements = thread.get("elements")
        element_items = raw_elements if isinstance(raw_elements, list) else []
        elements_by_for_id = self._index_elements_by_for_id(element_items)

        enriched_messages: list[dict] = []
        for message in messages:
            message_copy = dict(message)
            message_id = message_copy.get("id")
            if isinstance(message_id, str):
                related_elements = elements_by_for_id.get(message_id, [])
                message_copy["elements"] = [dict(element) for element in related_elements]
            else:
                message_copy["elements"] = []
            enriched_messages.append(message_copy)

        thread_metadata = dict(thread)
        thread_metadata.pop("steps", None)

        return {
            "thread": thread_metadata,
            "messages": enriched_messages,
        }

    def _filter_message_steps(self, steps: list[dict]) -> list[dict]:
        filtered_steps: list[dict] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = step.get("type")
            if not isinstance(step_type, str):
                continue
            if step_type in self._MESSAGE_STEP_TYPES:
                filtered_steps.append(step)
        return filtered_steps

    def _index_elements_by_for_id(self, elements: list[dict]) -> dict[str, list[dict]]:
        elements_by_for_id: dict[str, list[dict]] = {}
        for element in elements:
            if not isinstance(element, dict):
                continue
            for_id = element.get("forId")
            if not isinstance(for_id, str) or not for_id:
                continue
            elements_by_for_id.setdefault(for_id, []).append(element)
        return elements_by_for_id
