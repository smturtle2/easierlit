from __future__ import annotations

import asyncio
import inspect
import queue
import threading
import traceback
from typing import Any, Callable, Literal

from .app import EasierlitApp
from .errors import RunFuncExecutionError, WorkerAlreadyRunningError

RunFuncMode = Literal["auto", "sync", "async"]


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


class EasierlitClient:
    def __init__(
        self,
        run_funcs: list[Callable[[EasierlitApp], Any]],
        worker_mode: Literal["thread"] = "thread",
        run_func_mode: RunFuncMode = "auto",
    ):
        if worker_mode != "thread":
            raise ValueError("worker_mode must be 'thread'.")
        if run_func_mode not in ("auto", "sync", "async"):
            raise ValueError("run_func_mode must be one of 'auto', 'sync', or 'async'.")
        if not isinstance(run_funcs, list) or not run_funcs:
            raise ValueError("run_funcs must be a non-empty list of callables.")
        for run_func in run_funcs:
            if not callable(run_func):
                raise TypeError("Every run_funcs item must be callable.")

        self.run_funcs = run_funcs
        self.worker_mode = worker_mode
        self.run_func_mode = run_func_mode

        self._app: EasierlitApp | None = None

        self._threads: list[threading.Thread] = []
        self._thread_error_queue: queue.Queue[str] = queue.Queue()

        self._worker_error_traceback: str | None = None
        self._worker_error_lock = threading.Lock()
        self._worker_crash_handler: Callable[[str], None] | None = None

    def run(self, app: EasierlitApp) -> None:
        if self._is_worker_running():
            raise WorkerAlreadyRunningError("run() called while worker is already running.")

        self._reset_worker_error_state()
        self._app = app
        self._start_thread_workers(app)

    def stop(self, timeout: float = 5.0) -> None:
        app = self._app
        if app is not None:
            app.close()

        if self._threads:
            for thread in self._threads:
                thread.join(timeout=timeout)
            self._threads = []

        self._app = None
        self._raise_worker_error_if_any()

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

    def _is_worker_running(self) -> bool:
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        return bool(self._threads)

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
