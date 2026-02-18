import asyncio

import pytest

from easierlit import AppClosedError, EasierlitApp, IncomingMessage


def test_recv_add_update_delete_flow():
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

    message_id = app.add_message(thread_id="thread-1", content="world", author="Bot")
    add_cmd = app._pop_outgoing(timeout=1.0)
    assert add_cmd.command == "add_message"
    assert add_cmd.message_id == message_id
    assert add_cmd.content == "world"

    app.update_message(thread_id="thread-1", message_id=message_id, content="new")
    update_cmd = app._pop_outgoing(timeout=1.0)
    assert update_cmd.command == "update_message"
    assert update_cmd.message_id == message_id
    assert update_cmd.content == "new"

    app.delete_message(thread_id="thread-1", message_id=message_id)
    delete_cmd = app._pop_outgoing(timeout=1.0)
    assert delete_cmd.command == "delete"
    assert delete_cmd.message_id == message_id


def test_add_message_enqueues_elements():
    app = EasierlitApp()
    element = object()

    message_id = app.add_message(
        thread_id="thread-1",
        content="world",
        author="Bot",
        elements=[element],
    )
    add_cmd = app._pop_outgoing(timeout=1.0)

    assert add_cmd.command == "add_message"
    assert add_cmd.message_id == message_id
    assert add_cmd.elements == [element]


def test_tool_and_thought_enqueue_flow():
    app = EasierlitApp()

    tool_message_id = app.add_tool(
        thread_id="thread-1",
        tool_name="SearchTool",
        content='{"query":"chainlit"}',
    )
    tool_add_cmd = app._pop_outgoing(timeout=1.0)
    assert tool_add_cmd.command == "add_tool"
    assert tool_add_cmd.message_id == tool_message_id
    assert tool_add_cmd.author == "SearchTool"
    assert tool_add_cmd.content == '{"query":"chainlit"}'

    thought_message_id = app.add_thought(
        thread_id="thread-1",
        content="I should call a retrieval tool first.",
    )
    thought_add_cmd = app._pop_outgoing(timeout=1.0)
    assert thought_add_cmd.command == "add_tool"
    assert thought_add_cmd.message_id == thought_message_id
    assert thought_add_cmd.author == "Reasoning"
    assert thought_add_cmd.content == "I should call a retrieval tool first."

    app.update_tool(
        thread_id="thread-1",
        message_id=tool_message_id,
        tool_name="SearchTool",
        content='{"results":3}',
    )
    tool_update_cmd = app._pop_outgoing(timeout=1.0)
    assert tool_update_cmd.command == "update_tool"
    assert tool_update_cmd.message_id == tool_message_id
    assert tool_update_cmd.author == "SearchTool"
    assert tool_update_cmd.content == '{"results":3}'

    app.update_thought(
        thread_id="thread-1",
        message_id=thought_message_id,
        content="Now I can synthesize the final answer.",
    )
    thought_update_cmd = app._pop_outgoing(timeout=1.0)
    assert thought_update_cmd.command == "update_tool"
    assert thought_update_cmd.message_id == thought_message_id
    assert thought_update_cmd.author == "Reasoning"
    assert thought_update_cmd.content == "Now I can synthesize the final answer."


def test_update_tool_enqueues_elements():
    app = EasierlitApp()
    tool_message_id = app.add_tool(
        thread_id="thread-1",
        tool_name="SearchTool",
        content='{"query":"chainlit"}',
    )
    _ = app._pop_outgoing(timeout=1.0)

    element = object()
    app.update_tool(
        thread_id="thread-1",
        message_id=tool_message_id,
        tool_name="SearchTool",
        content='{"results":3}',
        elements=[element],
    )
    tool_update_cmd = app._pop_outgoing(timeout=1.0)

    assert tool_update_cmd.command == "update_tool"
    assert tool_update_cmd.message_id == tool_message_id
    assert tool_update_cmd.elements == [element]


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


def test_enqueue_default_values_and_recv_flow():
    app = EasierlitApp()

    message_id = app.enqueue(thread_id="thread-3", content="from external")
    incoming = app.recv(timeout=1.0)

    assert incoming.thread_id == "thread-3"
    assert incoming.content == "from external"
    assert incoming.session_id == "external"
    assert incoming.author == "External"
    assert incoming.message_id == message_id
    assert isinstance(message_id, str)
    assert message_id


def test_enqueue_with_explicit_values_returns_same_message_id():
    app = EasierlitApp()

    message_id = app.enqueue(
        thread_id="thread-4",
        content="payload",
        session_id="session-x",
        author="Webhook",
        message_id="msg-explicit",
        metadata={"source": "integration"},
        elements=[{"id": "el-1"}],
        created_at="2026-02-18T10:00:00Z",
    )
    incoming = app.recv(timeout=1.0)

    assert message_id == "msg-explicit"
    assert incoming.message_id == "msg-explicit"
    assert incoming.session_id == "session-x"
    assert incoming.author == "Webhook"
    assert incoming.metadata == {"source": "integration"}
    assert incoming.elements == [{"id": "el-1"}]
    assert incoming.created_at == "2026-02-18T10:00:00Z"


def test_enqueue_rejects_blank_required_fields():
    app = EasierlitApp()

    with pytest.raises(ValueError, match="thread_id"):
        app.enqueue(thread_id=" ", content="x")
    with pytest.raises(ValueError, match="session_id"):
        app.enqueue(thread_id="thread-1", content="x", session_id=" ")
    with pytest.raises(ValueError, match="author"):
        app.enqueue(thread_id="thread-1", content="x", author=" ")
    with pytest.raises(ValueError, match="message_id"):
        app.enqueue(thread_id="thread-1", content="x", message_id=" ")


def test_enqueue_raises_app_closed_error_when_app_is_closed():
    app = EasierlitApp()
    app.close()

    with pytest.raises(AppClosedError):
        app.enqueue(thread_id="thread-1", content="x")
