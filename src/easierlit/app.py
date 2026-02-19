from __future__ import annotations

import inspect
import json
from pathlib import Path
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
    Communication bridge between Chainlit callbacks and user handlers.

    This object is the primary API surface for message and thread CRUD
    operations. Incoming user messages are dispatched through EasierlitClient
    on_message workers.
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
        self._outgoing_queue: queue.Queue[OutgoingCommand] = queue.Queue()
        self._closed = threading.Event()
        self._running_thread_tasks: set[str] = set()
        self._thread_task_state_lock = threading.RLock()
        self._runtime = runtime if runtime is not None else get_runtime()
        self._data_layer_getter = data_layer_getter
        self._uuid_factory = uuid_factory

    def start_thread_task(self, thread_id: str) -> None:
        resolved_thread_id = self._require_non_empty_thread_id(thread_id)
        state_changed = self._set_thread_task_running(
            thread_id=resolved_thread_id,
            is_running=True,
        )
        if state_changed:
            self._emit_thread_task_state(
                thread_id=resolved_thread_id,
                is_running=True,
            )

    def end_thread_task(self, thread_id: str) -> None:
        resolved_thread_id = self._require_non_empty_thread_id(thread_id)
        state_changed = self._set_thread_task_running(
            thread_id=resolved_thread_id,
            is_running=False,
        )
        if state_changed:
            self._emit_thread_task_state(
                thread_id=resolved_thread_id,
                is_running=False,
            )

    def is_thread_task_running(self, thread_id: str) -> bool:
        resolved_thread_id = self._require_non_empty_thread_id(thread_id)
        with self._thread_task_state_lock:
            return resolved_thread_id in self._running_thread_tasks

    def enqueue(
        self,
        thread_id: str,
        content: str,
        *,
        session_id: str = "external",
        author: str = "User",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        elements: list[Any] | None = None,
        created_at: str | None = None,
    ) -> str:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string.")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string.")
        if not isinstance(author, str) or not author.strip():
            raise ValueError("author must be a non-empty string.")

        resolved_message_id = message_id
        if resolved_message_id is None:
            resolved_message_id = self._new_message_id()
        elif not isinstance(resolved_message_id, str) or not resolved_message_id.strip():
            raise ValueError("message_id must be a non-empty string when provided.")

        resolved_elements = elements or []
        resolved_metadata = metadata or {}

        self._enqueue_outgoing_command(
            command="add_message",
            thread_id=thread_id,
            message_id=resolved_message_id,
            content=content,
            author=author,
            step_type="user_message",
            metadata=resolved_metadata,
            elements=resolved_elements,
        )

        incoming = IncomingMessage(
            thread_id=thread_id,
            session_id=session_id,
            message_id=resolved_message_id,
            content=content,
            elements=resolved_elements,
            author=author,
            created_at=created_at,
            metadata=resolved_metadata,
        )
        self._runtime.dispatch_incoming(incoming)
        return resolved_message_id

    def add_message(
        self,
        thread_id: str,
        content: str,
        author: str = "Assistant",
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> str:
        """Enqueue an assistant message and return the generated message id."""
        message_id = self._new_message_id()
        self._enqueue_outgoing_command(
            command="add_message",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            author=author,
            metadata=metadata,
            elements=elements,
        )
        return message_id

    def add_tool(
        self,
        thread_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> str:
        """Enqueue a tool-call step and return the generated message id."""
        message_id = self._new_message_id()
        self._enqueue_outgoing_command(
            command="add_tool",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            author=tool_name,
            metadata=metadata,
            elements=elements,
        )
        return message_id

    def add_thought(
        self,
        thread_id: str,
        content: str,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> str:
        """Enqueue a reasoning step as a tool-call step."""
        return self.add_tool(
            thread_id=thread_id,
            tool_name=self._THOUGHT_TOOL_NAME,
            content=content,
            metadata=metadata,
            elements=elements,
        )

    def update_message(
        self,
        thread_id: str,
        message_id: str,
        content: str,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> None:
        self._enqueue_outgoing_command(
            command="update_message",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            metadata=metadata,
            elements=elements,
        )

    def update_tool(
        self,
        thread_id: str,
        message_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> None:
        self._enqueue_outgoing_command(
            command="update_tool",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            author=tool_name,
            metadata=metadata,
            elements=elements,
        )

    def update_thought(
        self,
        thread_id: str,
        message_id: str,
        content: str,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> None:
        self.update_tool(
            thread_id=thread_id,
            message_id=message_id,
            tool_name=self._THOUGHT_TOOL_NAME,
            content=content,
            metadata=metadata,
            elements=elements,
        )

    def delete_message(self, thread_id: str, message_id: str) -> None:
        self._enqueue_outgoing_command(
            command="delete",
            thread_id=thread_id,
            message_id=message_id,
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
        return self._build_messages_payload(thread, data_layer=self._data_layer_getter())

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
        thread_id: str | None = None,
    ) -> str:
        if thread_id is not None:
            self._write_thread(
                thread_id=thread_id,
                name=name,
                metadata=metadata,
                tags=tags,
                require_existing=False,
            )
            return thread_id

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

        try:
            self._runtime.run_coroutine_sync(_delete_thread())
        finally:
            self._clear_thread_task_state(thread_id)

    def reset_thread(self, thread_id: str) -> None:
        try:
            thread = self.get_thread(thread_id)
            thread_name = thread.get("name") if isinstance(thread.get("name"), str) else None
            raw_steps = thread.get("steps")
            step_items = raw_steps if isinstance(raw_steps, list) else []
            step_ids: list[str] = []
            for step in step_items:
                if not isinstance(step, dict):
                    continue
                step_id = self._coerce_identifier(step.get("id"))
                if step_id is None:
                    continue
                step_ids.append(step_id)

            self._delete_thread_steps_immediately(thread_id=thread_id, step_ids=step_ids)
            self.delete_thread(thread_id)
            self._write_thread(
                thread_id=thread_id,
                name=thread_name,
                metadata=None,
                tags=None,
                require_existing=False,
            )
        finally:
            self._clear_thread_task_state(thread_id)

    def close(self) -> None:
        if self._closed.is_set():
            return

        self._closed.set()
        self._outgoing_queue.put_nowait(OutgoingCommand(command="close"))

    def is_closed(self) -> bool:
        return self._closed.is_set()

    def _pop_outgoing(self, timeout: float | None = 0.1) -> OutgoingCommand:
        if timeout is None:
            return self._outgoing_queue.get()
        return self._outgoing_queue.get(timeout=timeout)

    def _put_outgoing(self, command: OutgoingCommand) -> None:
        if self._closed.is_set():
            raise AppClosedError("Cannot send command to a closed app.")
        self._outgoing_queue.put_nowait(command)

    def _new_message_id(self) -> str:
        return str(self._uuid_factory())

    def _require_non_empty_thread_id(self, thread_id: str) -> str:
        if not isinstance(thread_id, str):
            raise ValueError("thread_id must be a non-empty string.")
        normalized = thread_id.strip()
        if not normalized:
            raise ValueError("thread_id must be a non-empty string.")
        return normalized

    def _set_thread_task_running(self, *, thread_id: str, is_running: bool) -> bool:
        with self._thread_task_state_lock:
            was_running = thread_id in self._running_thread_tasks
            if is_running:
                self._running_thread_tasks.add(thread_id)
                return not was_running

            self._running_thread_tasks.discard(thread_id)
            return was_running

    def _emit_thread_task_state(self, *, thread_id: str, is_running: bool) -> None:
        # Task indicator emission is best-effort and must not break worker flow.
        try:
            self._runtime.run_coroutine_sync(
                self._runtime.set_thread_task_state(
                    thread_id=thread_id,
                    is_running=is_running,
                )
            )
        except Exception:
            return

    def _clear_thread_task_state(self, thread_id: str) -> None:
        if not isinstance(thread_id, str):
            return
        normalized = thread_id.strip()
        if not normalized:
            return

        state_changed = self._set_thread_task_running(
            thread_id=normalized,
            is_running=False,
        )
        if state_changed:
            self._emit_thread_task_state(
                thread_id=normalized,
                is_running=False,
            )

    def _enqueue_outgoing_command(
        self,
        *,
        command: str,
        thread_id: str,
        message_id: str | None = None,
        content: str | None = None,
        author: str = "Assistant",
        step_type: str | None = None,
        metadata: dict | None = None,
        elements: list[Any] | None = None,
    ) -> None:
        self._put_outgoing(
            OutgoingCommand(
                command=command,
                thread_id=thread_id,
                message_id=message_id,
                content=content,
                elements=elements or [],
                author=author,
                step_type=step_type,
                metadata=metadata or {},
            )
        )

    def _delete_thread_steps_immediately(self, *, thread_id: str, step_ids: list[str]) -> None:
        if not step_ids:
            return

        for step_id in step_ids:
            self._runtime.run_coroutine_sync(
                self._runtime.apply_outgoing_command(
                    OutgoingCommand(
                        command="delete",
                        thread_id=thread_id,
                        message_id=step_id,
                    )
                )
            )

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

    def _build_messages_payload(self, thread: dict, *, data_layer: Any | None = None) -> dict:
        raw_steps = thread.get("steps")
        step_items = raw_steps if isinstance(raw_steps, list) else []
        messages = self._filter_message_steps(step_items)

        raw_elements = thread.get("elements")
        element_items = raw_elements if isinstance(raw_elements, list) else []
        elements_by_for_id = self._index_elements_by_for_id(element_items)

        enriched_messages: list[dict] = []
        for message in messages:
            message_copy = dict(message)
            message_id = self._coerce_identifier(message_copy.get("id"))
            if message_id is not None:
                related_elements = elements_by_for_id.get(message_id, [])
                message_copy["elements"] = [
                    self._normalize_message_element(element, data_layer=data_layer)
                    for element in related_elements
                ]
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
            for_id = self._extract_element_target_id(element)
            if for_id is None:
                continue
            elements_by_for_id.setdefault(for_id, []).append(element)
        return elements_by_for_id

    def _extract_element_target_id(self, element: dict) -> str | None:
        for key in ("forId", "for_id", "stepId", "step_id"):
            identifier = self._coerce_identifier(element.get(key))
            if identifier is not None:
                return identifier
        return None

    def _normalize_message_element(self, element: dict, *, data_layer: Any | None = None) -> dict:
        normalized = dict(element)
        source = self._resolve_element_source(normalized, data_layer=data_layer)
        normalized["source"] = source
        normalized["has_source"] = source is not None
        return normalized

    def _resolve_element_source(
        self,
        element: dict,
        *,
        data_layer: Any | None = None,
    ) -> dict[str, Any] | None:
        object_key = self._coerce_non_empty_string(
            element.get("objectKey") or element.get("object_key")
        )
        if object_key is not None:
            resolved_url = self._resolve_element_url_from_object_key(
                object_key=object_key,
                data_layer=data_layer,
            )
            resolved_path = self._resolve_element_path_from_object_key(
                object_key=object_key,
                data_layer=data_layer,
            )
            if resolved_path is not None:
                element["path"] = resolved_path
                if resolved_url is not None:
                    element["url"] = resolved_url
                return {"kind": "path", "value": resolved_path}

            if resolved_url is not None:
                element["url"] = resolved_url
                return {"kind": "url", "value": resolved_url}

        path = self._coerce_non_empty_string(element.get("path"))
        if path is not None:
            return {"kind": "path", "value": path}

        url = self._coerce_non_empty_string(element.get("url"))
        if url is not None:
            return {"kind": "url", "value": url}

        content = element.get("content")
        if isinstance(content, (bytes, bytearray)):
            return {"kind": "bytes", "value": {"length": len(content)}}
        if isinstance(content, str) and content:
            return {"kind": "bytes", "value": {"length": len(content.encode("utf-8"))}}

        if object_key is not None:
            return {"kind": "objectKey", "value": object_key}

        chainlit_key = self._coerce_non_empty_string(
            element.get("chainlitKey") or element.get("chainlit_key")
        )
        if chainlit_key is not None:
            return {"kind": "chainlitKey", "value": chainlit_key}

        return None

    def _resolve_element_url_from_object_key(
        self,
        *,
        object_key: str,
        data_layer: Any | None = None,
    ) -> str | None:
        storage = self._resolve_storage_provider(data_layer)
        if storage is None:
            return None

        get_read_url = getattr(storage, "get_read_url", None)
        if not callable(get_read_url):
            return None

        try:
            maybe_url = get_read_url(object_key)
            if inspect.isawaitable(maybe_url):
                maybe_url = self._runtime.run_coroutine_sync(maybe_url)
        except Exception:
            return None

        if isinstance(maybe_url, str) and maybe_url.strip():
            return maybe_url
        return None

    def _resolve_element_path_from_object_key(
        self,
        *,
        object_key: str,
        data_layer: Any | None = None,
    ) -> str | None:
        storage = self._resolve_storage_provider(data_layer)
        if storage is None:
            return None

        resolve_file_path = getattr(storage, "resolve_file_path", None)
        if not callable(resolve_file_path):
            return None

        try:
            maybe_path = resolve_file_path(object_key)
        except Exception:
            return None

        if maybe_path is None:
            return None

        path = maybe_path if isinstance(maybe_path, Path) else Path(str(maybe_path))
        if not path.is_file():
            return None
        return str(path)

    def _resolve_storage_provider(self, data_layer: Any | None):
        if data_layer is None:
            return None

        for attr in ("storage_provider", "storage_client"):
            storage = getattr(data_layer, attr, None)
            if storage is not None:
                return storage
        return None

    def _coerce_non_empty_string(self, value: Any) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    def _coerce_identifier(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value if value else None
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            return None

        rendered = str(value)
        if not rendered:
            return None
        return rendered
