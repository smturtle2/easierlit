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


def test_on_message_exception_is_isolated_and_emits_notice():
    app = EasierlitApp()

    def _on_message(app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "bad":
            raise RuntimeError("boom")
        app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=_on_message)
    client.run(app)

    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-1", content="bad"))
    client.dispatch_incoming(_incoming(thread_id="thread-1", message_id="msg-2", content="ok"))

    notice = app._pop_outgoing(timeout=2.0)
    success = app._pop_outgoing(timeout=2.0)

    assert notice.command == "add_message"
    assert notice.author == "Easierlit"
    assert "Internal on_message error detected" in (notice.content or "")

    assert success.command == "add_message"
    assert success.content == "OK"
    assert app.is_closed() is False

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
