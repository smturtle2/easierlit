import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitClient
from easierlit.errors import AppClosedError, RunFuncExecutionError
from easierlit.runtime import get_runtime
from easierlit.storage import LocalFileStorageClient


class _FakeUIMessage:
    sent: list[tuple[str, str | None]] = []

    def __init__(self, content: str, author: str | None = None):
        self.content = content
        self.author = author

    async def send(self):
        self.__class__.sent.append((self.content, self.author))
        return self


@contextmanager
def _swap_attr(obj, name: str, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    runtime = get_runtime()
    runtime.unbind()

    chainlit_entry._APP_CLOSED_WARNING_EMITTED = False
    chainlit_entry._WORKER_FAILURE_UI_NOTIFIED = False
    chainlit_entry._DISCORD_BRIDGE = None
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False

    yield

    runtime.unbind()
    chainlit_entry._APP_CLOSED_WARNING_EMITTED = False
    chainlit_entry._WORKER_FAILURE_UI_NOTIFIED = False
    chainlit_entry._DISCORD_BRIDGE = None
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False


def _sample_message():
    return SimpleNamespace(
        id="msg-1",
        content="/help",
        author="User",
        created_at=None,
        metadata=None,
    )


def test_on_message_swallows_closed_app_with_worker_error(caplog):
    runtime = get_runtime()
    app = EasierlitApp()
    app.close()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)
    client._record_worker_error("Traceback (most recent call last):\nRuntimeError: boom")

    _FakeUIMessage.sent = []
    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1")

    caplog.set_level("WARNING")
    with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
        with _swap_attr(chainlit_entry.cl, "Message", _FakeUIMessage):
            asyncio.run(chainlit_entry._on_message(_sample_message()))
            asyncio.run(chainlit_entry._on_message(_sample_message()))

    assert len(_FakeUIMessage.sent) == 1
    assert "Server is shutting down" in _FakeUIMessage.sent[0][0]
    assert "server shutdown in progress" in caplog.text.lower()


def test_on_message_raises_when_closed_without_worker_error():
    runtime = get_runtime()
    app = EasierlitApp()
    app.close()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1")
    with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
        with pytest.raises(AppClosedError):
            asyncio.run(chainlit_entry._on_message(_sample_message()))


def test_on_app_shutdown_logs_summary_without_traceback(caplog):
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    worker_traceback = (
        "Traceback (most recent call last):\n"
        "RuntimeError: command '/new' failed: Chainlit context not found"
    )
    client._record_worker_error(worker_traceback)

    def fake_stop():
        raise RunFuncExecutionError(worker_traceback)

    original_stop = client.stop
    client.stop = fake_stop

    caplog.set_level("WARNING")
    try:
        asyncio.run(chainlit_entry._on_app_shutdown())
    finally:
        client.stop = original_stop

    assert "run_func crash acknowledged during shutdown" in caplog.text
    assert "command '/new' failed: Chainlit context not found" in caplog.text
    assert "Traceback (most recent call last)" not in caplog.text


def test_on_message_registers_discord_channel():
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1", client_type="discord")
    fake_channel = SimpleNamespace(id=777)
    fake_user_session = SimpleNamespace(get=lambda key: fake_channel if key == "discord_channel" else None)

    captured: dict[str, int | str] = {}
    original_register_discord_channel = runtime.register_discord_channel

    def fake_register_discord_channel(thread_id: str, channel_id: int):
        captured["thread_id"] = thread_id
        captured["channel_id"] = channel_id

    runtime.register_discord_channel = fake_register_discord_channel

    try:
        with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
            with _swap_attr(chainlit_entry.cl, "user_session", fake_user_session):
                asyncio.run(chainlit_entry._on_message(_sample_message()))
    finally:
        runtime.register_discord_channel = original_register_discord_channel

    assert captured["thread_id"] == "thread-1"
    assert captured["channel_id"] == 777


def test_on_message_does_not_register_session_for_discord_client():
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1", client_type="discord")
    fake_channel = SimpleNamespace(id=777)
    fake_user_session = SimpleNamespace(get=lambda key: fake_channel if key == "discord_channel" else None)

    calls = {"count": 0}
    original_register_session = runtime.register_session

    def fake_register_session(_thread_id: str, _session_id: str):
        calls["count"] += 1

    runtime.register_session = fake_register_session

    try:
        with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
            with _swap_attr(chainlit_entry.cl, "user_session", fake_user_session):
                asyncio.run(chainlit_entry._on_message(_sample_message()))
    finally:
        runtime.register_session = original_register_session

    assert calls["count"] == 0


def test_on_message_registers_session_for_non_discord_client():
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1", client_type="webapp")

    calls: list[tuple[str, str]] = []
    original_register_session = runtime.register_session

    def fake_register_session(thread_id: str, session_id: str):
        calls.append((thread_id, session_id))

    runtime.register_session = fake_register_session

    try:
        with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
            asyncio.run(chainlit_entry._on_message(_sample_message()))
    finally:
        runtime.register_session = original_register_session

    assert calls == [("thread-1", "session-1")]


def test_start_and_stop_discord_bridge_lifecycle():
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None, worker_mode="thread"),
        app=EasierlitApp(),
        discord_token="discord-token",
    )

    started = {"count": 0}
    stopped = {"count": 0}

    class _FakeBridge:
        def __init__(self, *, runtime, bot_token):
            assert runtime is get_runtime()
            assert bot_token == "discord-token"

        async def start(self):
            started["count"] += 1

        async def stop(self):
            stopped["count"] += 1

    with _swap_attr(chainlit_entry, "EasierlitDiscordBridge", _FakeBridge):
        asyncio.run(chainlit_entry._start_discord_bridge_if_needed())
        assert started["count"] == 1

        asyncio.run(chainlit_entry._start_discord_bridge_if_needed())
        assert started["count"] == 2

        asyncio.run(chainlit_entry._stop_discord_bridge_if_running())
        assert stopped["count"] == 1


def test_on_app_startup_runs_local_storage_preflight_for_default_data_layer(
    tmp_path, monkeypatch
):
    runtime = get_runtime()
    runtime.unbind()
    runtime.bind(
        client=EasierlitClient(run_func=lambda _app: None, worker_mode="thread"),
        app=EasierlitApp(),
    )

    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")
    fake_data_layer = SimpleNamespace(storage_provider=provider)
    calls = {"ensure": 0, "preflight": 0}

    monkeypatch.setattr(chainlit_entry, "_apply_runtime_configuration", lambda: None)
    monkeypatch.setattr(chainlit_entry, "get_data_layer", lambda: fake_data_layer)
    monkeypatch.setattr(chainlit_entry, "require_login", lambda: True)

    async def _noop_async():
        return None

    monkeypatch.setattr(chainlit_entry.RUNTIME, "start_dispatcher", _noop_async)
    monkeypatch.setattr(chainlit_entry, "_start_discord_bridge_if_needed", _noop_async)

    def _fake_ensure_local_storage_provider(storage_provider):
        calls["ensure"] += 1
        return storage_provider

    async def _fake_assert_local_storage_operational(storage_provider):
        del storage_provider
        calls["preflight"] += 1

    monkeypatch.setattr(
        chainlit_entry,
        "ensure_local_storage_provider",
        _fake_ensure_local_storage_provider,
    )
    monkeypatch.setattr(
        chainlit_entry,
        "assert_local_storage_operational",
        _fake_assert_local_storage_operational,
    )

    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = True
    asyncio.run(chainlit_entry._on_app_startup())

    assert calls["ensure"] == 1
    assert calls["preflight"] == 1
