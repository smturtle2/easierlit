from __future__ import annotations

import asyncio
import json
import queue
import threading
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

    def __init__(self):
        self._incoming_queue: queue.Queue[IncomingMessage | None] = queue.Queue()
        self._outgoing_queue: queue.Queue[OutgoingCommand] = queue.Queue()
        self._closed = threading.Event()
        self._runtime = get_runtime()

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

    def send(
        self,
        thread_id: str,
        content: str,
        author: str = "Assistant",
        metadata: dict | None = None,
    ) -> str:
        message_id = str(uuid4())
        self._put_outgoing(
            OutgoingCommand(
                command="send",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                author=author,
                metadata=metadata or {},
            )
        )
        return message_id

    def add_message(
        self,
        thread_id: str,
        content: str,
        author: str = "Assistant",
        metadata: dict | None = None,
    ) -> str:
        return self.send(
            thread_id=thread_id,
            content=content,
            author=author,
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
                command="update",
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                metadata=metadata or {},
            )
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
            require_existing=False,
        )

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
        data_layer = get_data_layer()
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
