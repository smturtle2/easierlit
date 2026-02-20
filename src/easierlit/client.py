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


class AsyncAwaitableRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._thread_entry, daemon=True)
            self._thread.start()

        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Failed to start async awaitable runner loop.")

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread

        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass

        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = None
            self._loop = None

    def is_running(self) -> bool:
        with self._lock:
            loop = self._loop
            return loop is not None and loop.is_running()

    def run_awaitable(self, awaitable: Any) -> Any:
        with self._lock:
            loop = self._loop

        if loop is None or not loop.is_running():
            raise RuntimeError("Async awaitable runner loop is not running.")

        if inspect.iscoroutine(awaitable):
            coroutine = awaitable
        else:

            async def _await_result() -> Any:
                return await awaitable

            coroutine = _await_result()

        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result()

    def _thread_entry(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        self._ready.set()

        try:
            loop.run_forever()
        finally:
            pending_tasks = asyncio.all_tasks(loop)
            for pending_task in pending_tasks:
                pending_task.cancel()
            if pending_tasks:
                loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                self._loop = None


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


def _run_awaitable(awaitable: Any, awaitable_runner: AsyncAwaitableRunner) -> None:
    if awaitable_runner.is_running():
        try:
            awaitable_runner.run_awaitable(awaitable)
            return
        except RuntimeError:
            pass

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
    awaitable_runner: AsyncAwaitableRunner,
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
        _run_awaitable(result, awaitable_runner)
        return

    if is_awaitable:
        _run_awaitable(result, awaitable_runner)


def _execute_on_message(
    on_message: Callable[[EasierlitApp, IncomingMessage], Any],
    app: EasierlitApp,
    incoming: IncomingMessage,
    awaitable_runner: AsyncAwaitableRunner,
) -> None:
    result = on_message(app, incoming)
    if inspect.isawaitable(result):
        _run_awaitable(result, awaitable_runner)


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
        self._run_func_awaitable_runner = AsyncAwaitableRunner()
        message_runner_count = min(self.max_message_workers, 8)
        self._message_awaitable_runners = [
            AsyncAwaitableRunner() for _ in range(message_runner_count)
        ]

    def run(self, app: EasierlitApp) -> None:
        if self._is_worker_running():
            raise WorkerAlreadyRunningError("run() called while worker is already running.")

        self._reset_worker_error_state()
        self._start_awaitable_runners()
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
        self._stop_awaitable_runners(timeout=timeout)
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
            _execute_run_func(
                run_func,
                app,
                self.run_func_mode,
                self._run_func_awaitable_runner,
            )
        except Exception:
            traceback_text = traceback.format_exc()
            self._handle_fatal_worker_failure(
                source="run_func",
                traceback_text=traceback_text,
                app=app,
            )

    def _message_worker_entry(self, app: EasierlitApp, incoming: IncomingMessage) -> None:
        awaitable_runner = self._resolve_message_awaitable_runner(incoming.thread_id)
        try:
            app.start_thread_task(incoming.thread_id)
            _execute_on_message(
                self.on_message,
                app,
                incoming,
                awaitable_runner,
            )
        except Exception:
            traceback_text = traceback.format_exc()
            self._handle_fatal_worker_failure(
                source="on_message",
                traceback_text=traceback_text,
                app=app,
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

    def _handle_fatal_worker_failure(
        self,
        *,
        source: str,
        traceback_text: str,
        app: EasierlitApp,
    ) -> None:
        summary = self._summarize_traceback(traceback_text)
        LOGGER.error("%s crashed: %s", source, summary)
        try:
            self._thread_error_queue.put_nowait(traceback_text)
        except Exception:
            pass

        self._record_worker_error(traceback_text)
        with self._message_scheduler_lock:
            self._accept_incoming_messages = False
            self._pending_messages_by_thread.clear()
        app.close()

    def _summarize_traceback(self, traceback_text: str) -> str:
        lines = [line.strip() for line in traceback_text.strip().splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return "Unknown worker error"

    def _start_awaitable_runners(self) -> None:
        started_runners: list[AsyncAwaitableRunner] = []
        try:
            self._run_func_awaitable_runner.start()
            started_runners.append(self._run_func_awaitable_runner)
            for awaitable_runner in self._message_awaitable_runners:
                awaitable_runner.start()
                started_runners.append(awaitable_runner)
        except Exception:
            for started_runner in reversed(started_runners):
                try:
                    started_runner.stop(timeout=1.0)
                except Exception:
                    LOGGER.exception("Failed to stop partially started awaitable runner.")
            raise

    def _stop_awaitable_runners(self, *, timeout: float) -> None:
        for awaitable_runner in self._message_awaitable_runners:
            try:
                awaitable_runner.stop(timeout=timeout)
            except Exception:
                LOGGER.exception("Failed to stop message awaitable runner cleanly.")
        try:
            self._run_func_awaitable_runner.stop(timeout=timeout)
        except Exception:
            LOGGER.exception("Failed to stop run_func awaitable runner cleanly.")

    def _resolve_message_awaitable_runner(self, thread_id: str) -> AsyncAwaitableRunner:
        if not self._message_awaitable_runners:
            return self._run_func_awaitable_runner
        runner_index = self._resolve_message_awaitable_runner_index(thread_id)
        return self._message_awaitable_runners[runner_index]

    def _resolve_message_awaitable_runner_index(self, thread_id: str | None) -> int:
        runner_count = len(self._message_awaitable_runners)
        if runner_count < 2:
            return 0
        if not thread_id:
            return 0
        return hash(thread_id) % runner_count

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
