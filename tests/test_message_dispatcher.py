import threading
import time

import pytest

from easierlit import EasierlitApp, EasierlitClient, IncomingMessage, RunFuncExecutionError



def _incoming(thread_id: str, message_id: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        thread_id=thread_id,
        session_id="session-1",
        message_id=message_id,
        content=content,
        author="User",
    )



def test_dispatcher_serializes_same_thread_id():
    app = EasierlitApp()
    release = threading.Event()
    lock = threading.Lock()
    running = 0
    max_running = 0

    def on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        release.wait(timeout=2.0)
        _app.add_message(incoming.thread_id, incoming.message_id, author="Worker")
        with lock:
            running -= 1

    client = EasierlitClient(on_message=on_message, max_message_workers=8)
    client.run(app)

    client.dispatch_incoming(_incoming("thread-1", "msg-1", "a"))
    time.sleep(0.05)
    client.dispatch_incoming(_incoming("thread-1", "msg-2", "b"))
    time.sleep(0.1)

    with lock:
        assert max_running == 1

    release.set()
    first = app._pop_outgoing(timeout=2.0)
    second = app._pop_outgoing(timeout=2.0)
    assert first.content == "msg-1"
    assert second.content == "msg-2"

    client.stop()



def test_dispatcher_respects_global_worker_limit():
    app = EasierlitApp()
    release = threading.Event()
    started_a = threading.Event()
    started_b = threading.Event()

    def on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.thread_id == "thread-a":
            started_a.set()
        if incoming.thread_id == "thread-b":
            started_b.set()
        release.wait(timeout=2.0)

    client = EasierlitClient(on_message=on_message, max_message_workers=1)
    client.run(app)

    client.dispatch_incoming(_incoming("thread-a", "msg-a", "a"))
    assert started_a.wait(timeout=1.0)

    client.dispatch_incoming(_incoming("thread-b", "msg-b", "b"))
    assert started_b.wait(timeout=0.2) is False

    release.set()
    assert started_b.wait(timeout=1.0)

    client.stop()


def test_dispatcher_treats_handler_errors_as_fatal():
    app = EasierlitApp()

    def on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "bad":
            raise RuntimeError("boom")
        _app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=on_message)
    client.run(app)

    client.dispatch_incoming(_incoming("thread-1", "msg-1", "bad"))
    for _ in range(50):
        if app.is_closed():
            break
        time.sleep(0.01)

    assert app.is_closed() is True
    with pytest.raises(RunFuncExecutionError):
        client.stop()
