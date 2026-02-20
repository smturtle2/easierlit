import asyncio

import pytest

from easierlit import AppClosedError, EasierlitApp


class _CapturingRuntime:
    def __init__(self):
        self.incoming = []

    def dispatch_incoming(self, message):
        self.incoming.append(message)


class _DiscordRuntime:
    def __init__(self, *, result: bool):
        self.calls: list[tuple[str, str]] = []
        self.result = result

    async def send_to_discord(self, *, thread_id: str, content: str) -> bool:
        self.calls.append((thread_id, content))
        return self.result

    def run_coroutine_sync(self, coro):
        return asyncio.run(coro)



def test_add_update_delete_flow():
    app = EasierlitApp()

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



def test_enqueue_default_values_dispatches_incoming_and_enqueues_user_step():
    runtime = _CapturingRuntime()
    app = EasierlitApp(runtime=runtime)

    message_id = app.enqueue(thread_id="thread-3", content="from external")
    enqueue_cmd = app._pop_outgoing(timeout=1.0)

    assert enqueue_cmd.command == "add_message"
    assert enqueue_cmd.thread_id == "thread-3"
    assert enqueue_cmd.message_id == message_id
    assert enqueue_cmd.content == "from external"
    assert enqueue_cmd.author == "User"
    assert enqueue_cmd.step_type == "user_message"

    assert len(runtime.incoming) == 1
    incoming = runtime.incoming[0]
    assert incoming.thread_id == "thread-3"
    assert incoming.content == "from external"
    assert incoming.session_id == "external"
    assert incoming.author == "User"
    assert incoming.message_id == message_id



def test_enqueue_with_explicit_values_returns_same_message_id_and_dispatches():
    runtime = _CapturingRuntime()
    app = EasierlitApp(runtime=runtime)

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
    enqueue_cmd = app._pop_outgoing(timeout=1.0)

    assert message_id == "msg-explicit"
    assert enqueue_cmd.command == "add_message"
    assert enqueue_cmd.thread_id == "thread-4"
    assert enqueue_cmd.message_id == "msg-explicit"
    assert enqueue_cmd.author == "Webhook"
    assert enqueue_cmd.step_type == "user_message"
    assert enqueue_cmd.content == "payload"
    assert enqueue_cmd.metadata == {"source": "integration"}
    assert enqueue_cmd.elements == [{"id": "el-1"}]

    assert len(runtime.incoming) == 1
    incoming = runtime.incoming[0]
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


def test_send_to_discord_returns_runtime_result():
    runtime = _DiscordRuntime(result=True)
    app = EasierlitApp(runtime=runtime)

    sent = app.send_to_discord("thread-1", "hello")

    assert sent is True
    assert runtime.calls == [("thread-1", "hello")]


def test_send_to_discord_returns_false_when_runtime_send_fails():
    runtime = _DiscordRuntime(result=False)
    app = EasierlitApp(runtime=runtime)

    sent = app.send_to_discord("thread-1", "hello")

    assert sent is False
    assert runtime.calls == [("thread-1", "hello")]


def test_send_to_discord_rejects_blank_fields():
    runtime = _DiscordRuntime(result=True)
    app = EasierlitApp(runtime=runtime)

    with pytest.raises(ValueError, match="thread_id"):
        app.send_to_discord(" ", "hello")
    with pytest.raises(ValueError, match="content"):
        app.send_to_discord("thread-1", " ")



def test_thread_task_state_api_flow():
    app = EasierlitApp()

    assert app.is_thread_task_running("thread-1") is False

    app.start_thread_task("thread-1")
    assert app.is_thread_task_running("thread-1") is True

    app.end_thread_task("thread-1")
    assert app.is_thread_task_running("thread-1") is False



def test_thread_task_state_uses_simple_mode_for_repeated_start():
    app = EasierlitApp()

    app.start_thread_task("thread-1")
    app.start_thread_task("thread-1")
    assert app.is_thread_task_running("thread-1") is True

    app.end_thread_task("thread-1")
    assert app.is_thread_task_running("thread-1") is False



def test_thread_task_state_validates_non_empty_thread_id():
    app = EasierlitApp()

    with pytest.raises(ValueError, match="thread_id"):
        app.start_thread_task(" ")
    with pytest.raises(ValueError, match="thread_id"):
        app.end_thread_task(" ")
    with pytest.raises(ValueError, match="thread_id"):
        app.is_thread_task_running(" ")
