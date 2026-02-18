from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Coroutine

from chainlit.context import init_http_context
from chainlit.data import get_data_layer
from chainlit.utils import utc_now

from .discord_outgoing import send_discord_command, supports_discord_command
from .errors import ThreadSessionNotActiveError
from .models import IncomingMessage, OutgoingCommand

if TYPE_CHECKING:
    from chainlit.session import WebsocketSession

    from .app import EasierlitApp
    from .client import EasierlitClient
    from .settings import EasierlitAuthConfig, EasierlitPersistenceConfig

LOGGER = logging.getLogger(__name__)


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
    ) -> None:
        with self._lock:
            self._client = client
            self._app = app
            self._auth = auth
            self._persistence = persistence
            self._discord_token = discord_token
            self._thread_to_session.clear()
            self._session_to_thread.clear()
            self._thread_to_discord_channel.clear()
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

        session_handled = False
        session = self._resolve_session(thread_id)
        if session is not None:
            await self._apply_realtime_command(session, command)
            session_handled = True

        discord_handled = False
        discord_channel_id = self.get_discord_channel_for_thread(thread_id)
        if discord_channel_id is not None:
            discord_handled = await self._apply_discord_command(discord_channel_id, command)

        # Realtime session handling is sufficient; avoid duplicate data-layer writes.
        if session_handled:
            return

        data_layer = self._data_layer_getter()

        # Discord-only paths should still persist when data layer exists so
        # web history can see assistant/tool outputs.
        if discord_handled:
            if data_layer:
                await self._apply_data_layer_command(command)
                return
            return

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
        from chainlit.step import Step

        init_ws_context(session)

        if command.command == "add_message":
            message = Message(
                id=command.message_id,
                content=command.content or "",
                elements=command.elements,
                author=command.author,
                metadata=command.metadata,
            )
            await message.send()
            return

        if command.command == "update_message":
            self._require_message_id(command, action="Update")
            message = Message(
                id=command.message_id,
                content=command.content or "",
                elements=command.elements,
                author=command.author,
                metadata=command.metadata,
            )
            await message.update()
            return

        if command.command == "add_tool":
            self._require_message_id(command, action="Add tool")
            step = Step(
                id=command.message_id,
                thread_id=command.thread_id,
                name=command.author,
                type="tool",
                elements=command.elements,
                metadata=command.metadata,
            )
            step.output = command.content or ""
            await step.send()
            return

        if command.command == "update_tool":
            self._require_message_id(command, action="Update tool")
            step = Step(
                id=command.message_id,
                thread_id=command.thread_id,
                name=command.author,
                type="tool",
                elements=command.elements,
                metadata=command.metadata,
            )
            step.output = command.content or ""
            await step.update()
            return

        if command.command == "delete":
            self._require_message_id(command, action="Delete")
            step = Step(
                id=command.message_id,
                thread_id=command.thread_id,
                name=command.author,
                type="undefined",
                metadata=command.metadata,
            )
            await step.remove()
            return

        raise ValueError(f"Unsupported command: {command.command}")

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

        message_id = self._require_message_id(command, action=command.command)

        timestamp = self._utc_now_fn()
        is_tool_command = self._is_tool_command(command.command)
        step_dict = {
            "id": message_id,
            "threadId": thread_id,
            "name": command.author,
            "type": "tool" if is_tool_command else "assistant_message",
            "output": command.content or "",
            "createdAt": timestamp,
            "start": timestamp,
            "end": timestamp,
            "streaming": False,
            "isError": False,
            "waitForAnswer": False,
            "metadata": command.metadata,
        }

        if self._is_create_command(command.command):
            await data_layer.create_step(step_dict)
            await self._persist_data_layer_elements(
                data_layer=data_layer,
                thread_id=thread_id,
                message_id=message_id,
                elements=command.elements,
            )
            return

        if self._is_update_command(command.command):
            await data_layer.update_step(step_dict)
            await self._persist_data_layer_elements(
                data_layer=data_layer,
                thread_id=thread_id,
                message_id=message_id,
                elements=command.elements,
            )
            return

        raise ValueError(f"Unsupported command: {command.command}")

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

        create_element = getattr(data_layer, "create_element", None)
        if not callable(create_element):
            return

        for element in elements:
            if hasattr(element, "for_id"):
                element.for_id = message_id
            if hasattr(element, "thread_id"):
                element.thread_id = thread_id
            await create_element(element)

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
