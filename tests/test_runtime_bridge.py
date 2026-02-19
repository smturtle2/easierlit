import asyncio
import importlib
from types import SimpleNamespace

import pytest

from easierlit import EasierlitApp, EasierlitClient, OutgoingCommand
from easierlit.runtime import RuntimeRegistry


@pytest.fixture(autouse=True)
def _reset_runtime_global():
    # Keep global runtime clean for other test modules.
    from easierlit.runtime import get_runtime

    runtime = get_runtime()
    runtime.unbind()
    yield
    runtime.unbind()


def test_register_and_unregister_session_mapping():
    runtime = RuntimeRegistry()
    runtime.register_session(thread_id="thread-1", session_id="session-1")
    assert runtime.get_session_id_for_thread("thread-1") == "session-1"

    runtime.unregister_session("session-1")
    assert runtime.get_session_id_for_thread("thread-1") is None


def test_dispatcher_consumes_outgoing_queue():
    seen = []

    class _SpyRuntime(RuntimeRegistry):
        async def apply_outgoing_command(self, command):
            seen.append(command)

    runtime = _SpyRuntime()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(run_funcs=[lambda _app: None])
    runtime.bind(client=client, app=app)

    async def scenario():
        runtime.set_main_loop(asyncio.get_running_loop())

        await runtime.start_dispatcher()
        app.add_message(thread_id="thread-1", content="hello")
        await asyncio.sleep(0.3)
        await runtime.stop_dispatcher()

        assert len(seen) >= 1
        assert seen[0].command == "add_message"
        assert seen[0].thread_id == "thread-1"

    asyncio.run(scenario())


def test_apply_outgoing_command_sends_to_discord_channel():
    runtime = RuntimeRegistry()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(run_funcs=[lambda _app: None])
    runtime.bind(client=client, app=app)
    runtime.register_discord_channel(thread_id="thread-1", channel_id=123)

    sent_messages: list[str] = []

    async def fake_sender(channel_id: int, command: OutgoingCommand) -> bool:
        assert channel_id == 123
        sent_messages.append(command.content or "")
        return True

    runtime.set_discord_sender(fake_sender)

    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello discord",
        author="Assistant",
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert sent_messages == ["hello discord"]


def test_apply_outgoing_command_discord_also_persists_when_data_layer_exists():
    class _FakeDataLayer:
        def __init__(self):
            self.created_steps = []

        async def create_step(self, step_dict):
            self.created_steps.append(step_dict)

        async def update_step(self, step_dict):
            self.created_steps.append(step_dict)

        async def delete_step(self, _step_id: str):
            return None

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake_data_layer,
        init_http_context_fn=lambda **_kwargs: None,
    )
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(run_funcs=[lambda _app: None])
    runtime.bind(client=client, app=app)
    runtime.register_discord_channel(thread_id="thread-1", channel_id=123)

    sent_messages: list[str] = []

    async def fake_sender(channel_id: int, command: OutgoingCommand) -> bool:
        assert channel_id == 123
        sent_messages.append(command.content or "")
        return True

    runtime.set_discord_sender(fake_sender)

    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello both",
        author="Assistant",
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert sent_messages == ["hello both"]
    assert len(fake_data_layer.created_steps) == 1
    assert fake_data_layer.created_steps[0]["id"] == "msg-1"
    assert fake_data_layer.created_steps[0]["threadId"] == "thread-1"
    assert fake_data_layer.created_steps[0]["type"] == "assistant_message"


def test_apply_outgoing_command_respects_explicit_step_type():
    class _FakeDataLayer:
        def __init__(self):
            self.created_steps = []

        async def create_step(self, step_dict):
            self.created_steps.append(step_dict)

        async def update_step(self, step_dict):
            self.created_steps.append(step_dict)

        async def delete_step(self, _step_id: str):
            return None

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake_data_layer,
        init_http_context_fn=lambda **_kwargs: None,
    )

    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello from webhook",
        author="Webhook",
        step_type="user_message",
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert len(fake_data_layer.created_steps) == 1
    assert fake_data_layer.created_steps[0]["type"] == "user_message"
    assert fake_data_layer.created_steps[0]["name"] == "Webhook"


def test_set_thread_task_state_returns_false_without_session():
    runtime = RuntimeRegistry()

    assert asyncio.run(runtime.set_thread_task_state("thread-1", True)) is False
    assert asyncio.run(runtime.set_thread_task_state("thread-1", False)) is False


def test_set_thread_task_state_emits_task_events(monkeypatch):
    runtime = RuntimeRegistry()
    calls: list[str] = []

    class _FakeEmitter:
        async def task_start(self):
            calls.append("start")

        async def task_end(self):
            calls.append("end")

    fake_session = SimpleNamespace(id="session-1")
    monkeypatch.setattr(runtime, "_resolve_session", lambda _thread_id: fake_session)

    chainlit_context_module = importlib.import_module("chainlit.context")
    monkeypatch.setattr(chainlit_context_module, "init_ws_context", lambda _session: None)
    monkeypatch.setattr(
        chainlit_context_module,
        "context",
        SimpleNamespace(emitter=_FakeEmitter()),
    )

    assert asyncio.run(runtime.set_thread_task_state("thread-1", True)) is True
    assert asyncio.run(runtime.set_thread_task_state("thread-1", False)) is True
    assert calls == ["start", "end"]
