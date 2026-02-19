import gc
import threading
import time

import pytest

from easierlit import AppClosedError, EasierlitApp, EasierlitClient, IncomingMessage, RunFuncExecutionError


def _single_message_worker(app: EasierlitApp) -> None:
    incoming = app.recv(timeout=2.0)
    app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")


async def _single_message_async_worker(app: EasierlitApp) -> None:
    incoming = await app.arecv(timeout=2.0)
    app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")


def _crashing_worker(_app: EasierlitApp) -> None:
    raise RuntimeError("worker crashed")


def _sync_noop_worker(_app: EasierlitApp) -> None:
    return None


def _long_running_worker(app: EasierlitApp) -> None:
    while True:
        try:
            app.recv(timeout=0.05)
        except TimeoutError:
            continue
        except AppClosedError:
            return


def test_invalid_worker_mode_raises_value_error():
    with pytest.raises(ValueError, match="worker_mode"):
        EasierlitClient(run_funcs=[_sync_noop_worker], worker_mode="process")  # type: ignore[arg-type]


def test_invalid_run_func_mode_raises_value_error():
    with pytest.raises(ValueError, match="run_func_mode"):
        EasierlitClient(run_funcs=[_sync_noop_worker], run_func_mode="invalid")  # type: ignore[arg-type]


def test_invalid_run_funcs_raises_value_error():
    with pytest.raises(ValueError, match="run_funcs"):
        EasierlitClient(run_funcs=[])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_funcs"):
        EasierlitClient(run_funcs=_sync_noop_worker)  # type: ignore[arg-type]


def test_non_callable_run_funcs_item_raises_type_error():
    with pytest.raises(TypeError, match="run_funcs item"):
        EasierlitClient(run_funcs=[_sync_noop_worker, "not-callable"])  # type: ignore[list-item]


def test_thread_worker_run_and_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_funcs=[_single_message_worker], worker_mode="thread")
    client.run(app)

    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-1",
            session_id="session-1",
            message_id="msg-1",
            content="hello",
            author="User",
        )
    )

    command = app._pop_outgoing(timeout=3.0)
    assert command.command == "add_message"
    assert command.content == "HELLO"

    client.stop()


def test_thread_async_worker_auto_mode_run_and_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_funcs=[_single_message_async_worker], worker_mode="thread")
    client.run(app)

    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-3",
            session_id="session-3",
            message_id="msg-3",
            content="hey",
            author="User",
        )
    )

    command = app._pop_outgoing(timeout=3.0)
    assert command.command == "add_message"
    assert command.content == "HEY"

    client.stop()


def test_worker_exception_is_raised_on_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_funcs=[_crashing_worker], worker_mode="thread")
    client.run(app)

    try:
        client.stop()
        assert False, "Expected stop() to raise run_func execution error."
    except RunFuncExecutionError:
        pass


def test_run_func_mode_sync_rejects_async_run_func():
    app = EasierlitApp()
    client = EasierlitClient(
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

    client = EasierlitClient(run_funcs=[_crashing_worker], worker_mode="thread")
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

    client = EasierlitClient(run_funcs=[_worker_a, _worker_b], worker_mode="thread")
    client.run(app)

    assert started_a.wait(timeout=1.0)
    assert started_b.wait(timeout=1.0)
    release.set()
    client.stop()


def test_crashing_run_func_fail_fast_closes_shared_app():
    app = EasierlitApp()
    crash_event = threading.Event()

    client = EasierlitClient(
        run_funcs=[_long_running_worker, _crashing_worker],
        worker_mode="thread",
    )
    client.set_worker_crash_handler(lambda _traceback: crash_event.set())
    client.run(app)

    assert crash_event.wait(timeout=2.0)
    assert app.is_closed() is True

    with pytest.raises(RunFuncExecutionError):
        client.stop()


def test_finished_run_func_does_not_stop_other_run_funcs():
    app = EasierlitApp()

    def _single_message_until_close_worker(app: EasierlitApp) -> None:
        handled = False
        while True:
            try:
                incoming = app.recv(timeout=0.05)
            except TimeoutError:
                continue
            except AppClosedError:
                return

            if not handled:
                app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")
                handled = True

    client = EasierlitClient(
        run_funcs=[_sync_noop_worker, _single_message_until_close_worker],
        worker_mode="thread",
    )
    client.run(app)

    time.sleep(0.1)
    assert app.is_closed() is False

    app.enqueue(thread_id="thread-1", content="hello")
    enqueue_command = app._pop_outgoing(timeout=2.0)
    worker_command = app._pop_outgoing(timeout=2.0)
    assert enqueue_command.command == "add_message"
    assert enqueue_command.content == "hello"
    assert enqueue_command.author == "User"
    assert enqueue_command.step_type == "user_message"
    assert worker_command.command == "add_message"
    assert worker_command.content == "HELLO"

    client.stop()


def test_all_run_funcs_finishing_does_not_close_app_automatically():
    app = EasierlitApp()
    client = EasierlitClient(
        run_funcs=[_sync_noop_worker, _sync_noop_worker],
        worker_mode="thread",
    )
    client.run(app)

    time.sleep(0.1)
    assert app.is_closed() is False

    client.stop()
