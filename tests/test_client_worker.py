import asyncio
import gc
import threading
import time

import pytest

from easierlit import AppClosedError, EasierlitApp, EasierlitClient, IncomingMessage, RunFuncExecutionError


def _incoming(
    *,
    thread_id: str,
    message_id: str,
    content: str,
    session_id: str = "session-1",
    author: str = "User",
) -> IncomingMessage:
    return IncomingMessage(
        thread_id=thread_id,
        session_id=session_id,
        message_id=message_id,
        content=content,
        author=author,
    )


def _sync_noop_worker(_app: EasierlitApp) -> None:
    return None


async def _single_message_async_worker(_app: EasierlitApp) -> None:
    return None


def _crashing_worker(_app: EasierlitApp) -> None:
    raise RuntimeError("worker crashed")


def _long_running_worker(app: EasierlitApp) -> None:
    while True:
        if app.is_closed():
            return
        time.sleep(0.02)


def test_invalid_worker_mode_raises_value_error():
    with pytest.raises(ValueError, match="worker_mode"):
        EasierlitClient(
            on_message=lambda _app, _incoming: None,
            run_funcs=[_sync_noop_worker],
            worker_mode="process",  # type: ignore[arg-type]
        )


def test_invalid_run_func_mode_raises_value_error():
    with pytest.raises(ValueError, match="run_func_mode"):
        EasierlitClient(
            on_message=lambda _app, _incoming: None,
            run_funcs=[_sync_noop_worker],
            run_func_mode="invalid",  # type: ignore[arg-type]
        )


def test_on_message_must_be_callable():
    with pytest.raises(TypeError, match="on_message"):
        EasierlitClient(on_message="not-callable")  # type: ignore[arg-type]


def test_run_funcs_optional():
    app = EasierlitApp()
    client = EasierlitClient(on_message=lambda _app, _incoming: None)
    client.run(app)
    client.stop()


def test_invalid_run_funcs_raises_value_error():
    with pytest.raises(ValueError, match="run_funcs"):
        EasierlitClient(
            on_message=lambda _app, _incoming: None,
            run_funcs=_sync_noop_worker,  # type: ignore[arg-type]
        )


def test_non_callable_run_funcs_item_raises_type_error():
    with pytest.raises(TypeError, match="run_funcs item"):
        EasierlitClient(
            on_message=lambda _app, _incoming: None,
            run_funcs=[_sync_noop_worker, "not-callable"],  # type: ignore[list-item]
        )


def test_invalid_max_message_workers_raises_value_error():
    with pytest.raises(ValueError, match="max_message_workers"):
        EasierlitClient(
            on_message=lambda _app, _incoming: None,
            max_message_workers=0,
        )


def test_dispatch_incoming_sync_handler_enqueues_output():
    app = EasierlitApp()

    def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=_on_message)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="hello"))

    command = app._pop_outgoing(timeout=2.0)
    assert command.command == "add_message"
    assert command.content == "HELLO"
    assert command.author == "Worker"

    client.stop()


def test_dispatch_incoming_async_handler_enqueues_output():
    app = EasierlitApp()

    async def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        await asyncio_sleep(0.01)
        app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=_on_message)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-3", message_id="msg-3", content="hey"))

    command = app._pop_outgoing(timeout=2.0)
    assert command.command == "add_message"
    assert command.content == "HEY"

    client.stop()


def test_async_on_message_execution_does_not_call_asyncio_run(monkeypatch):
    app = EasierlitApp()
    asyncio_run_calls = 0

    def _forbid_asyncio_run(_awaitable):
        nonlocal asyncio_run_calls
        asyncio_run_calls += 1
        raise AssertionError("asyncio.run() should not be used for async on_message execution.")

    monkeypatch.setattr("easierlit.client.asyncio.run", _forbid_asyncio_run)

    async def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=_on_message)
    client.run(app)
    client.dispatch_incoming(_incoming(thread_id="thread-3", message_id="msg-3", content="hey"))

    command = app._pop_outgoing(timeout=2.0)
    assert command.command == "add_message"
    assert command.content == "HEY"

    client.stop()
    assert asyncio_run_calls == 0


def test_async_await_busy_message_blocks_when_worker_slots_are_exhausted():
    app = EasierlitApp()
    busy_started = threading.Event()
    quick_started = threading.Event()

    async def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "busy":
            busy_started.set()
            await asyncio.sleep(0.4)
            return
        quick_started.set()

    client = EasierlitClient(on_message=_on_message, max_message_workers=1)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-busy", message_id="msg-1", content="busy"))
    assert busy_started.wait(timeout=1.0)

    client.dispatch_incoming(_incoming(thread_id="thread-quick", message_id="msg-2", content="quick"))
    assert quick_started.wait(timeout=0.15) is False
    assert quick_started.wait(timeout=1.0) is True

    client.stop()


def test_async_await_busy_message_does_not_block_when_worker_slot_is_available():
    app = EasierlitApp()
    busy_started = threading.Event()
    quick_started = threading.Event()

    async def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "busy":
            busy_started.set()
            await asyncio.sleep(0.4)
            return
        quick_started.set()

    client = EasierlitClient(on_message=_on_message, max_message_workers=2)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-busy", message_id="msg-1", content="busy"))
    assert busy_started.wait(timeout=1.0)

    client.dispatch_incoming(_incoming(thread_id="thread-quick", message_id="msg-2", content="quick"))
    assert quick_started.wait(timeout=0.2)

    client.stop()


def test_same_chat_messages_execute_serially():
    app = EasierlitApp()
    gate = threading.Event()
    state = {"running": 0, "max_running": 0}
    lock = threading.Lock()
    handled_order: list[str] = []

    def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        with lock:
            state["running"] += 1
            state["max_running"] = max(state["max_running"], state["running"])
            handled_order.append(incoming.message_id)

        gate.wait(timeout=2.0)
        app.add_message(incoming.thread_id, incoming.message_id, author="Worker")

        with lock:
            state["running"] -= 1

    client = EasierlitClient(on_message=_on_message, max_message_workers=4)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="first"))
    time.sleep(0.05)
    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-2", content="second"))
    time.sleep(0.1)

    with lock:
        assert state["max_running"] == 1
        assert handled_order == ["msg-1"]

    gate.set()

    first = app._pop_outgoing(timeout=2.0)
    second = app._pop_outgoing(timeout=2.0)
    assert first.content == "msg-1"
    assert second.content == "msg-2"

    client.stop()


def test_different_chats_execute_in_parallel_when_slots_available():
    app = EasierlitApp()
    release = threading.Event()
    started_a = threading.Event()
    started_b = threading.Event()
    lock = threading.Lock()
    running = 0
    max_running = 0

    def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)

        if incoming.thread_id == "thread-a":
            started_a.set()
        if incoming.thread_id == "thread-b":
            started_b.set()

        release.wait(timeout=2.0)

        with lock:
            running -= 1

    client = EasierlitClient(on_message=_on_message, max_message_workers=2)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-a", message_id="msg-a", content="a"))
    client.dispatch_incoming(_incoming(thread_id="thread-b", message_id="msg-b", content="b"))

    assert started_a.wait(timeout=1.0)
    assert started_b.wait(timeout=1.0)
    with lock:
        assert max_running >= 2

    release.set()
    client.stop()


def test_max_message_workers_limits_parallelism():
    app = EasierlitApp()
    release = threading.Event()
    started_first = threading.Event()
    started_second = threading.Event()

    def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.thread_id == "thread-a":
            started_first.set()
        if incoming.thread_id == "thread-b":
            started_second.set()
        release.wait(timeout=2.0)

    client = EasierlitClient(on_message=_on_message, max_message_workers=1)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-a", message_id="msg-a", content="a"))
    assert started_first.wait(timeout=1.0)

    client.dispatch_incoming(_incoming(thread_id="thread-b", message_id="msg-b", content="b"))
    assert started_second.wait(timeout=0.2) is False

    release.set()
    assert started_second.wait(timeout=1.0)

    client.stop()


def test_on_message_exception_is_fatal_and_invokes_crash_handler():
    app = EasierlitApp()
    crash_event = threading.Event()
    crash_payloads: list[str] = []

    def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "bad":
            raise RuntimeError("boom")

    client = EasierlitClient(on_message=_on_message)
    client.set_worker_crash_handler(
        lambda traceback_text: (
            crash_payloads.append(traceback_text),
            crash_event.set(),
        )
    )
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="bad"))
    assert crash_event.wait(timeout=2.0)
    for _ in range(50):
        if app.is_closed():
            break
        time.sleep(0.01)

    assert app.is_closed() is True
    with pytest.raises(AppClosedError):
        client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-2", content="ok"))

    with pytest.raises(RunFuncExecutionError):
        client.stop()
    assert len(crash_payloads) == 1
    assert "RuntimeError: boom" in crash_payloads[0]


def test_run_func_blocking_async_does_not_block_async_on_message():
    app = EasierlitApp()
    run_func_started = threading.Event()
    release_run_func = threading.Event()

    async def _run_func(_app: EasierlitApp) -> None:
        run_func_started.set()
        while not release_run_func.is_set():
            time.sleep(0.01)

    async def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=_on_message, run_funcs=[_run_func], max_message_workers=4)
    try:
        client.run(app)
        assert run_func_started.wait(timeout=1.0)

        started_at = time.perf_counter()
        client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="ok"))
        command = app._pop_outgoing(timeout=2.0)
        elapsed = time.perf_counter() - started_at

        assert command.command == "add_message"
        assert command.content == "OK"
        assert elapsed < 0.3
    finally:
        release_run_func.set()
        client.stop()


def test_blocking_async_on_message_isolated_to_its_runner_lane():
    app = EasierlitApp()
    release_slow = threading.Event()
    slow_started = threading.Event()
    fast_started = threading.Event()
    slow_thread_id = "thread-slow"

    async def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.thread_id == slow_thread_id:
            slow_started.set()
            while not release_slow.is_set():
                time.sleep(0.01)
            app.add_message(incoming.thread_id, "SLOW_DONE", author="Worker")
            return

        fast_started.set()
        app.add_message(incoming.thread_id, "FAST_DONE", author="Worker")

    client = EasierlitClient(on_message=_on_message, max_message_workers=8)
    runner_count = len(client._message_awaitable_runners)
    assert runner_count == 8
    slow_runner_index = client._resolve_message_awaitable_runner_index(slow_thread_id)

    fast_thread_id = None
    for index in range(256):
        candidate = f"thread-fast-{index}"
        if client._resolve_message_awaitable_runner_index(candidate) != slow_runner_index:
            fast_thread_id = candidate
            break
    assert fast_thread_id is not None

    try:
        client.run(app)

        client.dispatch_incoming(_incoming(thread_id=slow_thread_id, message_id="msg-slow", content="slow"))
        assert slow_started.wait(timeout=1.0)

        started_at = time.perf_counter()
        client.dispatch_incoming(_incoming(thread_id=fast_thread_id, message_id="msg-fast", content="fast"))
        assert fast_started.wait(timeout=0.3)
        fast_elapsed = time.perf_counter() - started_at

        fast_command = app._pop_outgoing(timeout=2.0)
        assert fast_command.content == "FAST_DONE"
        assert fast_elapsed < 0.3
    finally:
        release_slow.set()
        client.stop()


def test_stop_does_not_wait_for_inflight_message_workers():
    app = EasierlitApp()
    started = threading.Event()
    release = threading.Event()

    def _on_message(_app: EasierlitApp, _incoming: IncomingMessage) -> None:
        started.set()
        release.wait(timeout=2.0)

    client = EasierlitClient(on_message=_on_message)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="x"))
    assert started.wait(timeout=1.0)

    t0 = time.perf_counter()
    client.stop(timeout=0.01)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2
    release.set()


def test_dispatch_incoming_raises_when_app_is_closed():
    app = EasierlitApp()
    client = EasierlitClient(on_message=lambda _app, _incoming: None)
    client.run(app)
    app.close()

    with pytest.raises(AppClosedError):
        client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="x"))

    client.stop()


def test_worker_exception_is_raised_on_stop():
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_crashing_worker],
        worker_mode="thread",
    )
    client.run(app)

    with pytest.raises(RunFuncExecutionError):
        client.stop()


def test_run_func_mode_sync_rejects_async_run_func():
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_single_message_async_worker],
        worker_mode="thread",
        run_func_mode="sync",
    )
    client.run(app)

    with pytest.raises(RunFuncExecutionError, match="run_func_mode='sync'"):
        client.stop()


def test_run_func_mode_async_rejects_sync_run_func():
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_sync_noop_worker],
        worker_mode="thread",
        run_func_mode="async",
    )
    client.run(app)

    with pytest.raises(RunFuncExecutionError, match="run_func_mode='async'"):
        client.stop()


def test_sync_mode_async_run_func_emits_no_never_awaited_warning(recwarn):
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_single_message_async_worker],
        worker_mode="thread",
        run_func_mode="sync",
    )
    client.run(app)

    with pytest.raises(RunFuncExecutionError):
        client.stop()

    gc.collect()
    never_awaited = [
        warning
        for warning in recwarn.list
        if "was never awaited" in str(warning.message)
    ]
    assert not never_awaited


def test_thread_worker_records_error_and_invokes_crash_handler():
    app = EasierlitApp()
    crash_event = threading.Event()
    crash_payloads: list[str] = []

    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_crashing_worker],
        worker_mode="thread",
    )
    client.set_worker_crash_handler(
        lambda traceback_text: (
            crash_payloads.append(traceback_text),
            crash_event.set(),
        )
    )
    client.run(app)

    assert crash_event.wait(timeout=2.0)
    error = client.peek_worker_error()
    assert error is not None
    assert "worker crashed" in error
    assert len(crash_payloads) == 1

    with pytest.raises(RunFuncExecutionError):
        client.stop()


def test_run_funcs_start_in_parallel_threads():
    app = EasierlitApp()
    started_a = threading.Event()
    started_b = threading.Event()
    release = threading.Event()

    def _worker_a(_app: EasierlitApp) -> None:
        started_a.set()
        release.wait(timeout=2.0)

    def _worker_b(_app: EasierlitApp) -> None:
        started_b.set()
        release.wait(timeout=2.0)

    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_worker_a, _worker_b],
        worker_mode="thread",
    )
    client.run(app)

    assert started_a.wait(timeout=1.0)
    assert started_b.wait(timeout=1.0)
    release.set()
    client.stop()


def test_crashing_run_func_fail_fast_closes_shared_app():
    app = EasierlitApp()
    crash_event = threading.Event()

    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_long_running_worker, _crashing_worker],
        worker_mode="thread",
    )
    client.set_worker_crash_handler(lambda _traceback: crash_event.set())
    client.run(app)

    assert crash_event.wait(timeout=2.0)
    assert app.is_closed() is True

    with pytest.raises(RunFuncExecutionError):
        client.stop()


def test_all_run_funcs_finishing_does_not_close_app_automatically():
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[_sync_noop_worker, _sync_noop_worker],
        worker_mode="thread",
    )
    client.run(app)

    time.sleep(0.1)
    assert app.is_closed() is False

    client.stop()


async def asyncio_sleep(duration: float) -> None:
    import asyncio

    await asyncio.sleep(duration)
