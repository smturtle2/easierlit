from __future__ import annotations

import asyncio
import inspect
import logging
import queue
import threading
import traceback
from collections import OrderedDict, deque
from typing import Any, Callable, Literal

from .app import EasierlitApp
from .errors import AppClosedError, RunFuncExecutionError, WorkerAlreadyRunningError
from .models import IncomingMessage

RunFuncMode = Literal["auto", "sync", "async"]

LOGGER = logging.getLogger(__name__)


def _close_unawaited_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass

    cancel = getattr(value, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass


def _run_awaitable(awaitable: Any) -> None:
    if inspect.iscoroutine(awaitable):
        asyncio.run(awaitable)
        return

    async def _await_result() -> None:
        await awaitable

    asyncio.run(_await_result())


def _execute_run_func(
    run_func: Callable[[EasierlitApp], Any],
    app: EasierlitApp,
    run_func_mode: RunFuncMode,
) -> None:
    result = run_func(app)
    is_awaitable = inspect.isawaitable(result)

    if run_func_mode == "sync":
        if is_awaitable:
            _close_unawaited_awaitable(result)
            raise TypeError(
                "run_func_mode='sync' requires a synchronous run_func "
                "that does not return an awaitable."
            )
        return

    if run_func_mode == "async":
        if not is_awaitable:
            raise TypeError(
                "run_func_mode='async' requires run_func to return an awaitable."
            )
        _run_awaitable(result)
        return

    if is_awaitable:
        _run_awaitable(result)


def _execute_on_message(
    on_message: Callable[[EasierlitApp, IncomingMessage], Any],
    app: EasierlitApp,
    incoming: IncomingMessage,
) -> None:
    result = on_message(app, incoming)
    if inspect.isawaitable(result):
        _run_awaitable(result)


class EasierlitClient:
    def __init__(
        self,
        on_message: Callable[[EasierlitApp, IncomingMessage], Any],
        run_funcs: list[Callable[[EasierlitApp], Any]] | None = None,
        worker_mode: Literal["thread"] = "thread",
        run_func_mode: RunFuncMode = "auto",
        max_message_workers: int = 64,
    ):
        if not callable(on_message):
            raise TypeError("on_message must be callable.")
        if worker_mode != "thread":
            raise ValueError("worker_mode must be 'thread'.")
        if run_func_mode not in ("auto", "sync", "async"):
            raise ValueError("run_func_mode must be one of 'auto', 'sync', or 'async'.")
        if run_funcs is None:
            run_funcs = []
        if not isinstance(run_funcs, list):
            raise ValueError("run_funcs must be a list of callables when provided.")
        for run_func in run_funcs:
            if not callable(run_func):
                raise TypeError("Every run_funcs item must be callable.")
        if not isinstance(max_message_workers, int) or max_message_workers < 1:
            raise ValueError("max_message_workers must be an integer >= 1.")

        self.on_message = on_message
        self.run_funcs = run_funcs
        self.worker_mode = worker_mode
        self.run_func_mode = run_func_mode
        self.max_message_workers = max_message_workers

        self._app: EasierlitApp | None = None

        self._threads: list[threading.Thread] = []
        self._thread_error_queue: queue.Queue[str] = queue.Queue()

        self._worker_error_traceback: str | None = None
        self._worker_error_lock = threading.Lock()
        self._worker_crash_handler: Callable[[str], None] | None = None

        self._message_scheduler_lock = threading.RLock()
        self._pending_messages_by_thread: OrderedDict[str, deque[IncomingMessage]] = OrderedDict()
        self._active_chat_threads: set[str] = set()
        self._active_message_worker_count = 0
        self._inflight_message_workers: set[threading.Thread] = set()
        self._accept_incoming_messages = False

    def run(self, app: EasierlitApp) -> None:
        if self._is_worker_running():
            raise WorkerAlreadyRunningError("run() called while worker is already running.")

        self._reset_worker_error_state()
        self._app = app

        with self._message_scheduler_lock:
            self._pending_messages_by_thread.clear()
            self._active_chat_threads.clear()
            self._active_message_worker_count = 0
            self._inflight_message_workers.clear()
            self._accept_incoming_messages = True

        self._start_thread_workers(app)

    def stop(self, timeout: float = 5.0) -> None:
        with self._message_scheduler_lock:
            self._accept_incoming_messages = False
            self._pending_messages_by_thread.clear()

        app = self._app
        if app is not None:
            app.close()

        if self._threads:
            for thread in self._threads:
                thread.join(timeout=timeout)
            self._threads = []

        self._app = None
        self._raise_worker_error_if_any()

    def dispatch_incoming(self, incoming: IncomingMessage) -> None:
        app = self._app
        if app is None:
            return
        if app.is_closed():
            raise AppClosedError("Cannot dispatch incoming message to a closed app.")

        with self._message_scheduler_lock:
            if not self._accept_incoming_messages:
                return

            pending = self._pending_messages_by_thread.get(incoming.thread_id)
            if pending is None:
                pending = deque()
                self._pending_messages_by_thread[incoming.thread_id] = pending
            pending.append(incoming)
            self._schedule_pending_messages_locked(app)

    def _start_thread_workers(self, app: EasierlitApp) -> None:
        self._threads = []
        for run_func in self.run_funcs:
            thread = threading.Thread(
                target=self._thread_worker_entry,
                args=(app, run_func),
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _thread_worker_entry(
        self,
        app: EasierlitApp,
        run_func: Callable[[EasierlitApp], Any],
    ) -> None:
        try:
            _execute_run_func(run_func, app, self.run_func_mode)
        except Exception:
            traceback_text = traceback.format_exc()
            self._thread_error_queue.put(traceback_text)
            self._record_worker_error(traceback_text)
            app.close()

    def _message_worker_entry(self, app: EasierlitApp, incoming: IncomingMessage) -> None:
        try:
            app.start_thread_task(incoming.thread_id)
            _execute_on_message(self.on_message, app, incoming)
        except Exception:
            traceback_text = traceback.format_exc()
            LOGGER.exception(
                "on_message handler failed for thread '%s' message '%s'.",
                incoming.thread_id,
                incoming.message_id,
            )
            self._emit_on_message_failure_notice(
                app=app,
                thread_id=incoming.thread_id,
                traceback_text=traceback_text,
            )
        finally:
            try:
                app.end_thread_task(incoming.thread_id)
            except Exception:
                LOGGER.exception(
                    "Failed to end thread task state for thread '%s'.",
                    incoming.thread_id,
                )

            with self._message_scheduler_lock:
                self._active_message_worker_count = max(0, self._active_message_worker_count - 1)
                self._active_chat_threads.discard(incoming.thread_id)
                self._inflight_message_workers.discard(threading.current_thread())
                current_app = self._app
                if current_app is not None:
                    self._schedule_pending_messages_locked(current_app)

    def _emit_on_message_failure_notice(
        self,
        *,
        app: EasierlitApp,
        thread_id: str,
        traceback_text: str,
    ) -> None:
        summary = "Unknown on_message error"
        lines = [line.strip() for line in traceback_text.strip().splitlines() if line.strip()]
        if lines:
            summary = lines[-1]

        try:
            app.add_message(
                thread_id=thread_id,
                content=(
                    "Internal on_message error detected.\n"
                    f"Reason: {summary}"
                ),
                author="Easierlit",
            )
        except Exception:
            LOGGER.exception(
                "Failed to enqueue on_message failure notice for thread '%s'.",
                thread_id,
            )

    def _schedule_pending_messages_locked(self, app: EasierlitApp) -> None:
        if not self._accept_incoming_messages:
            return

        while self._active_message_worker_count < self.max_message_workers:
            next_item = self._pop_next_schedulable_message_locked()
            if next_item is None:
                return

            thread_id, incoming = next_item
            self._active_chat_threads.add(thread_id)
            self._active_message_worker_count += 1

            worker = threading.Thread(
                target=self._message_worker_entry,
                args=(app, incoming),
                daemon=True,
            )
            self._inflight_message_workers.add(worker)
            worker.start()

    def _pop_next_schedulable_message_locked(self) -> tuple[str, IncomingMessage] | None:
        stale_thread_ids: list[str] = []

        for thread_id, pending_messages in self._pending_messages_by_thread.items():
            if not pending_messages:
                stale_thread_ids.append(thread_id)
                continue
            if thread_id in self._active_chat_threads:
                continue

            incoming = pending_messages.popleft()
            if pending_messages:
                self._pending_messages_by_thread.move_to_end(thread_id)
            else:
                stale_thread_ids.append(thread_id)

            for stale_thread_id in stale_thread_ids:
                self._pending_messages_by_thread.pop(stale_thread_id, None)
            return thread_id, incoming

        for stale_thread_id in stale_thread_ids:
            self._pending_messages_by_thread.pop(stale_thread_id, None)
        return None

    def _is_worker_running(self) -> bool:
        self._threads = [thread for thread in self._threads if thread.is_alive()]

        with self._message_scheduler_lock:
            self._prune_inflight_message_workers_locked()
            has_message_worker = bool(self._inflight_message_workers)

        return bool(self._threads) or has_message_worker

    def _prune_inflight_message_workers_locked(self) -> None:
        dead_workers = [worker for worker in self._inflight_message_workers if not worker.is_alive()]
        for worker in dead_workers:
            self._inflight_message_workers.discard(worker)

    def _raise_worker_error_if_any(self) -> None:
        worker_error = self.peek_worker_error()
        if worker_error:
            raise RunFuncExecutionError(worker_error)

        if not self._thread_error_queue.empty():
            raise RunFuncExecutionError(self._thread_error_queue.get())

    def set_worker_crash_handler(self, handler: Callable[[str], None] | None) -> None:
        with self._worker_error_lock:
            self._worker_crash_handler = handler

    def peek_worker_error(self) -> str | None:
        with self._worker_error_lock:
            return self._worker_error_traceback

    def _record_worker_error(self, traceback_text: str) -> None:
        with self._worker_error_lock:
            if self._worker_error_traceback is not None:
                return
            self._worker_error_traceback = traceback_text
            handler = self._worker_crash_handler

        if handler is not None:
            handler(traceback_text)

    def _reset_worker_error_state(self) -> None:
        with self._worker_error_lock:
            self._worker_error_traceback = None

        while True:
            try:
                self._thread_error_queue.get_nowait()
            except queue.Empty:
                break
