import threading
import time

from easierlit import EasierlitApp, EasierlitClient, IncomingMessage



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



def test_dispatcher_auto_manages_thread_task_state():
    app = EasierlitApp()
    release = threading.Event()
    entered = threading.Event()

    def on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        assert _app.is_thread_task_running(incoming.thread_id) is True
        entered.set()
        release.wait(timeout=2.0)

    client = EasierlitClient(on_message=on_message)
    client.run(app)

    thread_id = "thread-1"
    client.dispatch_incoming(_incoming(thread_id, "msg-1", "hello"))
    assert entered.wait(timeout=1.0)
    assert app.is_thread_task_running(thread_id) is True

    release.set()
    for _ in range(50):
        if app.is_thread_task_running(thread_id) is False:
            break
        time.sleep(0.01)

    assert app.is_thread_task_running(thread_id) is False
    client.stop()



def test_dispatcher_isolates_handler_errors_and_continues():
    app = EasierlitApp()

    def on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        if incoming.content == "bad":
            raise RuntimeError("boom")
        _app.add_message(incoming.thread_id, incoming.content.upper(), author="Worker")

    client = EasierlitClient(on_message=on_message)
    client.run(app)

    client.dispatch_incoming(_incoming("thread-1", "msg-1", "bad"))
    client.dispatch_incoming(_incoming("thread-1", "msg-2", "ok"))

    notice = app._pop_outgoing(timeout=2.0)
    success = app._pop_outgoing(timeout=2.0)

    assert notice.author == "Easierlit"
    assert "Internal on_message error detected" in (notice.content or "")
    assert success.content == "OK"

    client.stop()
