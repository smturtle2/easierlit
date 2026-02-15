import threading

import pytest

from easierlit import EasierlitApp, EasierlitClient, IncomingMessage, RunFuncExecutionError


def _single_message_worker(app: EasierlitApp) -> None:
    incoming = app.recv(timeout=2.0)
    app.send(incoming.thread_id, incoming.content.upper(), author="Worker")


def _crashing_worker(_app: EasierlitApp) -> None:
    raise RuntimeError("worker crashed")


def _crashing_process_worker(_app: EasierlitApp) -> None:
    raise RuntimeError("process worker crashed")


def test_thread_worker_run_and_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_func=_single_message_worker, worker_mode="thread")
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
    assert command.command == "send"
    assert command.content == "HELLO"

    client.stop()


def test_process_worker_run_and_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_func=_single_message_worker, worker_mode="process")
    client.run(app)

    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-2",
            session_id="session-2",
            message_id="msg-2",
            content="hi",
            author="User",
        )
    )

    command = app._pop_outgoing(timeout=5.0)
    assert command.command == "send"
    assert command.content == "HI"

    client.stop()


def test_worker_exception_is_raised_on_stop():
    app = EasierlitApp()
    client = EasierlitClient(run_func=_crashing_worker, worker_mode="thread")
    client.run(app)

    try:
        client.stop()
        assert False, "Expected stop() to raise run_func execution error."
    except RunFuncExecutionError:
        pass


def test_thread_worker_records_error_and_invokes_crash_handler():
    app = EasierlitApp()
    crash_event = threading.Event()
    crash_payloads: list[str] = []

    client = EasierlitClient(run_func=_crashing_worker, worker_mode="thread")
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


def test_process_worker_records_error_and_invokes_crash_handler():
    app = EasierlitApp()
    crash_event = threading.Event()
    crash_payloads: list[str] = []

    client = EasierlitClient(run_func=_crashing_process_worker, worker_mode="process")
    client.set_worker_crash_handler(
        lambda traceback_text: (
            crash_payloads.append(traceback_text),
            crash_event.set(),
        )
    )
    client.run(app)

    assert crash_event.wait(timeout=5.0)
    error = client.peek_worker_error()
    assert error is not None
    assert "process worker crashed" in error
    assert len(crash_payloads) == 1

    with pytest.raises(RunFuncExecutionError):
        client.stop()
