from __future__ import annotations

import asyncio
import inspect
import json
import multiprocessing as mp
import pickle
import queue
import threading
import traceback
from typing import Any, Callable, Literal
from uuid import uuid4

from chainlit.data import get_data_layer
from chainlit.types import Pagination, ThreadFilter
from chainlit.user import User

from .app import EasierlitApp
from .errors import (
    DataPersistenceNotEnabledError,
    RunFuncExecutionError,
    WorkerAlreadyRunningError,
)
from .models import OutgoingCommand
from .runtime import get_runtime


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


def _process_worker_entry(
    run_func: Callable[[EasierlitApp], Any],
    app: EasierlitApp,
    run_func_mode: RunFuncMode,
    error_queue: Any,
) -> None:
    try:
        _execute_run_func(run_func, app, run_func_mode)
    except Exception:
        error_queue.put(traceback.format_exc())
    finally:
        app.close()


class EasierlitClient:
    def __init__(
        self,
        run_func: Callable[[EasierlitApp], Any],
        worker_mode: Literal["thread", "process"] = "thread",
        run_func_mode: RunFuncMode = "auto",
    ):
        if worker_mode not in ("thread", "process"):
            raise ValueError("worker_mode must be either 'thread' or 'process'.")
        if run_func_mode not in ("auto", "sync", "async"):
            raise ValueError("run_func_mode must be one of 'auto', 'sync', or 'async'.")

        self.run_func = run_func
        self.worker_mode = worker_mode
        self.run_func_mode = run_func_mode

        self._runtime = get_runtime()
        self._app: EasierlitApp | None = None

        self._thread: threading.Thread | None = None
        self._thread_error_queue: queue.Queue[str] = queue.Queue()

        self._process: mp.Process | None = None
        self._process_error_queue: Any = None
        self._process_error_monitor_thread: threading.Thread | None = None
        self._process_error_monitor_stop = threading.Event()

        self._worker_error_traceback: str | None = None
        self._worker_error_lock = threading.Lock()
        self._worker_crash_handler: Callable[[str], None] | None = None

    def run(self, app: EasierlitApp) -> None:
        if self._is_worker_running():
            raise WorkerAlreadyRunningError("run() called while worker is already running.")

        self._reset_worker_error_state()
        self._app = app

        if self.worker_mode == "thread":
            self._start_thread_worker(app)
            return

        self._start_process_worker(app)

    def stop(self, timeout: float = 5.0) -> None:
        app = self._app
        if app is not None:
            app.close()

        self._process_error_monitor_stop.set()

        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

        if self._process is not None:
            self._process.join(timeout=timeout)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1.0)
            self._process = None

        if self._process_error_monitor_thread is not None:
            self._process_error_monitor_thread.join(timeout=timeout)
            self._process_error_monitor_thread = None

        self._process_error_queue = None
        self._app = None
        self._raise_worker_error_if_any()

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
        data_layer = self._get_data_layer_or_raise()
        prepared_tags = self._prepare_tags_for_update(tags, data_layer)

        async def _update_thread():
            owner_user_id = await self._resolve_default_owner_user_id(data_layer)
            await data_layer.update_thread(
                thread_id=thread_id,
                name=name,
                user_id=owner_user_id,
                metadata=metadata,
                tags=prepared_tags,
            )

        self._runtime.run_coroutine_sync(_update_thread())

    def delete_thread(self, thread_id: str) -> None:
        data_layer = self._get_data_layer_or_raise()

        async def _delete_thread():
            await data_layer.delete_thread(thread_id)

        self._runtime.run_coroutine_sync(_delete_thread())

    def add_message(
        self,
        thread_id: str,
        content: str,
        author: str = "Assistant",
        metadata: dict | None = None,
    ) -> str:
        message_id = str(uuid4())
        command = OutgoingCommand(
            command="send",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            author=author,
            metadata=metadata or {},
        )
        self._runtime.run_coroutine_sync(self._runtime.apply_outgoing_command(command))
        return message_id

    def update_message(
        self,
        thread_id: str,
        message_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        command = OutgoingCommand(
            command="update",
            thread_id=thread_id,
            message_id=message_id,
            content=content,
            metadata=metadata or {},
        )
        self._runtime.run_coroutine_sync(self._runtime.apply_outgoing_command(command))

    def delete_message(self, thread_id: str, message_id: str) -> None:
        command = OutgoingCommand(
            command="delete",
            thread_id=thread_id,
            message_id=message_id,
        )
        self._runtime.run_coroutine_sync(self._runtime.apply_outgoing_command(command))

    def _start_thread_worker(self, app: EasierlitApp) -> None:
        self._thread = threading.Thread(
            target=self._thread_worker_entry,
            args=(app,),
            daemon=True,
        )
        self._thread.start()

    def _thread_worker_entry(self, app: EasierlitApp) -> None:
        try:
            _execute_run_func(self.run_func, app, self.run_func_mode)
        except Exception:
            traceback_text = traceback.format_exc()
            self._thread_error_queue.put(traceback_text)
            self._record_worker_error(traceback_text)
        finally:
            app.close()

    def _start_process_worker(self, app: EasierlitApp) -> None:
        try:
            pickle.dumps(self.run_func)
        except Exception as exc:
            raise TypeError(
                "run_func must be picklable in process mode."
            ) from exc

        context = mp.get_context("spawn")
        self._process_error_queue = context.Queue()
        self._process = context.Process(
            target=_process_worker_entry,
            args=(
                self.run_func,
                app,
                self.run_func_mode,
                self._process_error_queue,
            ),
            daemon=True,
        )
        self._process.start()
        self._start_process_error_monitor()

    def _is_worker_running(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        if self._process is not None and self._process.is_alive():
            return True
        return False

    def _raise_worker_error_if_any(self) -> None:
        worker_error = self.peek_worker_error()
        if worker_error:
            raise RunFuncExecutionError(worker_error)

        if not self._thread_error_queue.empty():
            raise RunFuncExecutionError(self._thread_error_queue.get())

        if self._process_error_queue is not None:
            try:
                error = self._process_error_queue.get_nowait()
            except queue.Empty:
                error = None
            if error:
                raise RunFuncExecutionError(error)

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

    def set_worker_crash_handler(self, handler: Callable[[str], None] | None) -> None:
        with self._worker_error_lock:
            self._worker_crash_handler = handler

    def peek_worker_error(self) -> str | None:
        with self._worker_error_lock:
            return self._worker_error_traceback

    def _record_worker_error(self, traceback_text: str) -> None:
        with self._worker_error_lock:
            self._worker_error_traceback = traceback_text
            handler = self._worker_crash_handler

        if handler is not None:
            handler(traceback_text)

    def _reset_worker_error_state(self) -> None:
        with self._worker_error_lock:
            self._worker_error_traceback = None

        self._process_error_monitor_stop.clear()

        while True:
            try:
                self._thread_error_queue.get_nowait()
            except queue.Empty:
                break

    def _start_process_error_monitor(self) -> None:
        self._process_error_monitor_stop.clear()
        self._process_error_monitor_thread = threading.Thread(
            target=self._process_error_monitor_entry,
            daemon=True,
        )
        self._process_error_monitor_thread.start()

    def _process_error_monitor_entry(self) -> None:
        error_queue = self._process_error_queue
        if error_queue is None:
            return

        while not self._process_error_monitor_stop.is_set():
            try:
                error = error_queue.get(timeout=0.2)
            except queue.Empty:
                process = self._process
                if process is not None and process.is_alive():
                    continue
                try:
                    error = error_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
            except Exception:
                break

            if isinstance(error, str) and error:
                self._record_worker_error(error)

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
