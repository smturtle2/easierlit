from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Coroutine

from chainlit.context import init_http_context
from chainlit.data import get_data_layer
from chainlit.utils import utc_now

from .errors import ThreadSessionNotActiveError
from .models import IncomingMessage, OutgoingCommand

if TYPE_CHECKING:
    from chainlit.session import WebsocketSession

    from .app import EasierlitApp
    from .client import EasierlitClient
    from .settings import EasierlitAuthConfig, EasierlitPersistenceConfig

LOGGER = logging.getLogger(__name__)


class RuntimeRegistry:
    def __init__(self) -> None:
        self._client: EasierlitClient | None = None
        self._app: EasierlitApp | None = None
        self._auth: EasierlitAuthConfig | None = None
        self._persistence: EasierlitPersistenceConfig | None = None

        self._thread_to_session: dict[str, str] = {}
        self._session_to_thread: dict[str, str] = {}

        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None

        self._lock = threading.RLock()

    def bind(
        self,
        *,
        client: EasierlitClient,
        app: EasierlitApp,
        auth: EasierlitAuthConfig | None = None,
        persistence: EasierlitPersistenceConfig | None = None,
    ) -> None:
        with self._lock:
            self._client = client
            self._app = app
            self._auth = auth
            self._persistence = persistence
            self._thread_to_session.clear()
            self._session_to_thread.clear()

    def unbind(self) -> None:
        with self._lock:
            self._client = None
            self._app = None
            self._auth = None
            self._persistence = None
            self._thread_to_session.clear()
            self._session_to_thread.clear()
            self._main_loop = None
            self._dispatcher_task = None

    def get_client(self) -> EasierlitClient | None:
        return self._client

    def get_app(self) -> EasierlitApp | None:
        return self._app

    def get_auth(self) -> EasierlitAuthConfig | None:
        return self._auth

    def get_persistence(self) -> EasierlitPersistenceConfig | None:
        return self._persistence

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

    def unregister_session(self, session_id: str) -> None:
        with self._lock:
            thread_id = self._session_to_thread.pop(session_id, None)
            if thread_id is not None:
                self._thread_to_session.pop(thread_id, None)

    def get_session_id_for_thread(self, thread_id: str) -> str | None:
        with self._lock:
            return self._thread_to_session.get(thread_id)

    def enqueue_incoming(self, message: IncomingMessage) -> None:
        app = self._app
        if app is None:
            return
        app._enqueue_incoming(message)

    async def start_dispatcher(self) -> None:
        if self._app is None:
            raise RuntimeError("No active app bound to runtime.")

        if self._dispatcher_task and not self._dispatcher_task.done():
            return

        self._dispatcher_task = asyncio.create_task(self._dispatch_outgoing_loop())

    async def stop_dispatcher(self) -> None:
        task = self._dispatcher_task
        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._dispatcher_task = None

    async def _dispatch_outgoing_loop(self) -> None:
        app = self._app
        if app is None:
            return

        while not app.is_closed():
            try:
                command = await asyncio.to_thread(app._pop_outgoing, 0.2)
            except queue.Empty:
                continue

            if command.command == "close":
                break

            try:
                await self.apply_outgoing_command(command)
            except Exception:
                LOGGER.exception(
                    "Failed to apply outgoing command: %s",
                    command.model_dump(),
                )

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

    async def apply_outgoing_command(self, command: OutgoingCommand) -> None:
        if command.command == "close":
            return

        thread_id = command.thread_id
        if not thread_id:
            raise ValueError("Outgoing command is missing thread_id.")

        session = self._resolve_session(thread_id)
        if session is not None:
            await self._apply_realtime_command(session, command)
            return

        data_layer = get_data_layer()
        if not data_layer:
            raise ThreadSessionNotActiveError(
                f"Thread '{thread_id}' has no active session and no data layer fallback."
            )

        await self._apply_data_layer_command(command)

    async def _apply_realtime_command(
        self, session: WebsocketSession, command: OutgoingCommand
    ) -> None:
        from chainlit.context import init_ws_context
        from chainlit.message import Message

        init_ws_context(session)

        if command.command == "send":
            message = Message(
                id=command.message_id,
                content=command.content or "",
                author=command.author,
                metadata=command.metadata,
            )
            await message.send()
            return

        if command.command == "update":
            if not command.message_id:
                raise ValueError("Update command requires message_id.")
            message = Message(
                id=command.message_id,
                content=command.content or "",
                author=command.author,
                metadata=command.metadata,
            )
            await message.update()
            return

        if command.command == "delete":
            if not command.message_id:
                raise ValueError("Delete command requires message_id.")
            message = Message(
                id=command.message_id,
                content="",
                author=command.author,
                metadata=command.metadata,
            )
            await message.remove()

    async def _apply_data_layer_command(self, command: OutgoingCommand) -> None:
        data_layer = get_data_layer()
        if not data_layer:
            raise RuntimeError("Data layer unexpectedly missing.")

        if not command.thread_id:
            raise ValueError(f"{command.command} command requires thread_id.")
        self._init_data_layer_http_context(command.thread_id)

        if command.command == "delete":
            if not command.message_id:
                raise ValueError("Delete command requires message_id.")
            await data_layer.delete_step(command.message_id)
            return

        if not command.message_id:
            raise ValueError(f"{command.command} command requires message_id.")

        timestamp = utc_now()
        step_dict = {
            "id": command.message_id,
            "threadId": command.thread_id,
            "name": command.author,
            "type": "assistant_message",
            "output": command.content or "",
            "createdAt": timestamp,
            "start": timestamp,
            "end": timestamp,
            "streaming": False,
            "isError": False,
            "waitForAnswer": False,
            "metadata": command.metadata,
        }

        if command.command == "send":
            await data_layer.create_step(step_dict)
            return

        if command.command == "update":
            await data_layer.update_step(step_dict)
            return

    def _init_data_layer_http_context(self, thread_id: str) -> None:
        init_http_context(thread_id=thread_id, client_type="webapp")


_RUNTIME = RuntimeRegistry()


def get_runtime() -> RuntimeRegistry:
    return _RUNTIME
