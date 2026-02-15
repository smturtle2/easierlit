from __future__ import annotations

import asyncio
import multiprocessing as mp
import pickle
import queue
from uuid import uuid4

from .errors import AppClosedError
from .models import IncomingMessage, OutgoingCommand


class EasierlitApp:
    """
    Communication bridge between Chainlit callbacks and user run_func.

    It is process-safe by default, so the same instance can be used by
    thread and process workers.
    """

    def __init__(self, mp_context: mp.context.BaseContext | None = None):
        context = mp_context or mp.get_context("spawn")
        self._incoming_queue = context.Queue()
        self._outgoing_queue = context.Queue()
        self._closed = context.Event()

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
        self._assert_picklable(message, "incoming message")
        self._incoming_queue.put_nowait(message)

    def _pop_outgoing(self, timeout: float | None = 0.1) -> OutgoingCommand:
        if timeout is None:
            return self._outgoing_queue.get()
        return self._outgoing_queue.get(timeout=timeout)

    def _put_outgoing(self, command: OutgoingCommand) -> None:
        if self._closed.is_set():
            raise AppClosedError("Cannot send command to a closed app.")
        self._assert_picklable(command, "outgoing command")
        self._outgoing_queue.put_nowait(command)

    @staticmethod
    def _assert_picklable(value: object, label: str) -> None:
        try:
            pickle.dumps(value)
        except Exception as exc:
            raise TypeError(f"{label} must be picklable.") from exc
