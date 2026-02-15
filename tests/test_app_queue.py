import asyncio

import pytest

from easierlit import AppClosedError, EasierlitApp, IncomingMessage


def test_recv_send_update_delete_flow():
    app = EasierlitApp()

    incoming = IncomingMessage(
        thread_id="thread-1",
        session_id="session-1",
        message_id="msg-1",
        content="hello",
        author="User",
    )
    app._enqueue_incoming(incoming)

    received = app.recv(timeout=1.0)
    assert received.thread_id == "thread-1"
    assert received.content == "hello"

    sent_message_id = app.send(thread_id="thread-1", content="world", author="Bot")
    send_cmd = app._pop_outgoing(timeout=1.0)
    assert send_cmd.command == "send"
    assert send_cmd.message_id == sent_message_id
    assert send_cmd.content == "world"

    app.update_message(thread_id="thread-1", message_id=sent_message_id, content="new")
    update_cmd = app._pop_outgoing(timeout=1.0)
    assert update_cmd.command == "update"
    assert update_cmd.message_id == sent_message_id
    assert update_cmd.content == "new"

    app.delete_message(thread_id="thread-1", message_id=sent_message_id)
    delete_cmd = app._pop_outgoing(timeout=1.0)
    assert delete_cmd.command == "delete"
    assert delete_cmd.message_id == sent_message_id


def test_recv_timeout_and_close():
    app = EasierlitApp()

    try:
        app.recv(timeout=0.05)
        assert False, "recv() should timeout when no message exists."
    except TimeoutError:
        pass

    app.close()

    try:
        app.recv(timeout=0.05)
        assert False, "recv() should fail once app is closed."
    except AppClosedError:
        pass


def test_arecv_flow():
    app = EasierlitApp()
    incoming = IncomingMessage(
        thread_id="thread-2",
        session_id="session-2",
        message_id="msg-2",
        content="hello async",
        author="User",
    )
    app._enqueue_incoming(incoming)

    received = asyncio.run(app.arecv(timeout=1.0))
    assert received.thread_id == "thread-2"
    assert received.content == "hello async"


def test_arecv_timeout_and_close():
    app = EasierlitApp()

    with pytest.raises(TimeoutError):
        asyncio.run(app.arecv(timeout=0.05))

    app.close()

    with pytest.raises(AppClosedError):
        asyncio.run(app.arecv(timeout=0.05))
