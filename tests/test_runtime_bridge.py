import asyncio
import importlib
import time
from types import SimpleNamespace

import pytest

from easierlit import AppClosedError, EasierlitApp, EasierlitClient, IncomingMessage, OutgoingCommand
from easierlit.runtime import RuntimeRegistry
from easierlit.storage.local import LOCAL_STORAGE_ROUTE_PREFIX


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


def test_dispatch_incoming_delegates_to_client():
    runtime = RuntimeRegistry()
    app = EasierlitApp(runtime=runtime)
    captured = {"incoming": None}

    def _on_message(_app: EasierlitApp, incoming: IncomingMessage) -> None:
        captured["incoming"] = incoming

    client = EasierlitClient(on_message=_on_message)
    runtime.bind(client=client, app=app)
    client.run(app)

    incoming = IncomingMessage(
        thread_id="thread-1",
        session_id="session-1",
        message_id="msg-1",
        content="hello",
        author="User",
    )
    runtime.dispatch_incoming(incoming)

    for _ in range(20):
        if captured["incoming"] is not None:
            break
        time.sleep(0.01)
    assert captured["incoming"] is not None
    assert captured["incoming"].message_id == "msg-1"

    client.stop()


def test_dispatch_incoming_raises_when_app_closed():
    runtime = RuntimeRegistry()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(on_message=lambda _app, _incoming: None)
    runtime.bind(client=client, app=app)
    client.run(app)
    app.close()

    incoming = IncomingMessage(
        thread_id="thread-1",
        session_id="session-1",
        message_id="msg-1",
        content="hello",
        author="User",
    )
    with pytest.raises(AppClosedError):
        runtime.dispatch_incoming(incoming)

    client.stop()


def test_dispatcher_consumes_outgoing_queue():
    seen = []

    class _SpyRuntime(RuntimeRegistry):
        async def apply_outgoing_command(self, command):
            seen.append(command)

    runtime = _SpyRuntime()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None])
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


def test_dispatcher_preserves_order_within_same_thread_id():
    seen_message_ids: list[str] = []

    class _SpyRuntime(RuntimeRegistry):
        async def apply_outgoing_command(self, command):
            seen_message_ids.append(command.message_id or "")

    runtime = _SpyRuntime()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None])
    runtime.bind(client=client, app=app, max_outgoing_workers=4)

    async def scenario():
        runtime.set_main_loop(asyncio.get_running_loop())

        await runtime.start_dispatcher()
        app._put_outgoing(
            OutgoingCommand(
                command="add_message",
                thread_id="thread-1",
                message_id="msg-1",
                content="first",
            )
        )
        app._put_outgoing(
            OutgoingCommand(
                command="add_message",
                thread_id="thread-1",
                message_id="msg-2",
                content="second",
            )
        )
        await asyncio.sleep(0.3)
        await runtime.stop_dispatcher()

    asyncio.run(scenario())
    assert seen_message_ids[:2] == ["msg-1", "msg-2"]


def test_dispatcher_does_not_globally_block_other_thread_ids():
    seen_at: dict[str, float] = {}

    class _SpyRuntime(RuntimeRegistry):
        async def apply_outgoing_command(self, command):
            if command.message_id == "slow-msg":
                await asyncio.sleep(1.0)
            seen_at[command.message_id or ""] = time.perf_counter()

    runtime = _SpyRuntime()
    app = EasierlitApp(runtime=runtime)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None])
    runtime.bind(client=client, app=app, max_outgoing_workers=4)

    async def scenario():
        runtime.set_main_loop(asyncio.get_running_loop())
        await runtime.start_dispatcher()

        slow_thread_id = "thread-slow"
        slow_lane = runtime._resolve_outgoing_lane_index(slow_thread_id)
        fast_thread_id = None
        for index in range(256):
            candidate = f"thread-fast-{index}"
            if runtime._resolve_outgoing_lane_index(candidate) != slow_lane:
                fast_thread_id = candidate
                break
        assert fast_thread_id is not None

        t0 = time.perf_counter()
        app._put_outgoing(
            OutgoingCommand(
                command="add_message",
                thread_id=slow_thread_id,
                message_id="slow-msg",
                content="slow",
            )
        )
        await asyncio.sleep(0.05)
        app._put_outgoing(
            OutgoingCommand(
                command="add_message",
                thread_id=fast_thread_id,
                message_id="fast-msg",
                content="fast",
            )
        )

        for _ in range(100):
            if {"slow-msg", "fast-msg"}.issubset(seen_at.keys()):
                break
            await asyncio.sleep(0.02)

        await runtime.stop_dispatcher()

        fast_elapsed = seen_at["fast-msg"] - t0
        slow_elapsed = seen_at["slow-msg"] - t0
        assert fast_elapsed < slow_elapsed
        assert fast_elapsed < 0.7

    asyncio.run(scenario())


def test_apply_outgoing_command_persists_without_auto_sending_to_discord():
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
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None])
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
        content="hello persisted only",
        author="Assistant",
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert sent_messages == []
    assert len(fake_data_layer.created_steps) == 1
    assert fake_data_layer.created_steps[0]["id"] == "msg-1"
    assert fake_data_layer.created_steps[0]["threadId"] == "thread-1"
    assert fake_data_layer.created_steps[0]["type"] == "assistant_message"


def test_send_to_discord_sends_when_channel_is_registered():
    runtime = RuntimeRegistry()
    runtime.register_discord_channel(thread_id="thread-1", channel_id=123)

    sent_messages: list[str] = []

    async def fake_sender(channel_id: int, command: OutgoingCommand) -> bool:
        assert channel_id == 123
        sent_messages.append(command.content or "")
        return True

    runtime.set_discord_sender(fake_sender)
    result = asyncio.run(runtime.send_to_discord(thread_id="thread-1", content="hello discord"))

    assert result is True
    assert sent_messages == ["hello discord"]


def test_send_to_discord_returns_false_when_channel_missing():
    runtime = RuntimeRegistry()

    sent_messages: list[str] = []

    async def fake_sender(channel_id: int, command: OutgoingCommand) -> bool:
        sent_messages.append(command.content or "")
        return True

    runtime.set_discord_sender(fake_sender)
    result = asyncio.run(runtime.send_to_discord(thread_id="thread-missing", content="hello"))

    assert result is False
    assert sent_messages == []


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


def test_resolve_element_payload_uses_to_thread_for_local_file_reads(tmp_path, monkeypatch):
    runtime = RuntimeRegistry()
    direct_file = tmp_path / "direct.bin"
    direct_file.write_bytes(b"direct")
    local_file = tmp_path / "local.bin"
    local_file.write_bytes(b"local")
    to_thread_calls: list[object] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    class _FakeLocalStorage:
        def resolve_file_path(self, object_key: str):
            if object_key != "local.bin":
                raise ValueError(f"Unexpected object key: {object_key}")
            return local_file

    direct_payload = asyncio.run(
        runtime._resolve_element_payload(
            {"path": str(direct_file)},
            local_storage=_FakeLocalStorage(),
        )
    )
    assert direct_payload == b"direct"

    local_url_payload = asyncio.run(
        runtime._resolve_element_payload(
            {"url": f"http://localhost{LOCAL_STORAGE_ROUTE_PREFIX}/local.bin"},
            local_storage=_FakeLocalStorage(),
        )
    )
    assert local_url_payload == b"local"
    assert len(to_thread_calls) == 2
