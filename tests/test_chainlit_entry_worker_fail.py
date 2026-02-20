import asyncio
from contextlib import contextmanager
import os
from types import SimpleNamespace

import chainlit.data.sql_alchemy as chainlit_sql_alchemy
from chainlit.config import config
import pytest
from fastapi.testclient import TestClient

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitClient, EasierlitPersistenceConfig
from easierlit.errors import AppClosedError, RunFuncExecutionError
from easierlit.runtime import get_runtime
from easierlit.settings import _resolve_local_storage_provider
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
    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None

    yield

    runtime.unbind()
    chainlit_entry._APP_CLOSED_WARNING_EMITTED = False
    chainlit_entry._WORKER_FAILURE_UI_NOTIFIED = False
    chainlit_entry._DISCORD_BRIDGE = None
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False
    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None


def _sample_message():
    return SimpleNamespace(
        id="msg-1",
        content="/help",
        elements=[],
        author="User",
        created_at=None,
        metadata=None,
    )


def test_on_message_swallows_closed_app_with_worker_error(caplog):
    runtime = get_runtime()
    app = EasierlitApp()
    app.close()
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
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
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(client=client, app=app)

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1")
    with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
        with pytest.raises(AppClosedError):
            asyncio.run(chainlit_entry._on_message(_sample_message()))


def test_on_app_shutdown_logs_summary_without_traceback(caplog):
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
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
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
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
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
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
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
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


def test_on_message_dispatches_even_when_thread_state_is_irrelevant():
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[lambda _app: None],
        worker_mode="thread",
    )
    runtime.bind(client=client, app=app)

    captured = {"count": 0, "thread_id": None}
    original_dispatch_incoming = runtime.dispatch_incoming

    def fake_dispatch_incoming(incoming):
        captured["count"] += 1
        captured["thread_id"] = incoming.thread_id

    runtime.dispatch_incoming = fake_dispatch_incoming

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1", client_type="webapp")
    try:
        with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
            asyncio.run(chainlit_entry._on_message(_sample_message()))
    finally:
        runtime.dispatch_incoming = original_dispatch_incoming

    assert captured["count"] == 1
    assert captured["thread_id"] == "thread-1"


def test_on_message_dispatches_incoming_elements():
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[lambda _app: None],
        worker_mode="thread",
    )
    runtime.bind(client=client, app=app)

    captured = {"incoming": None}
    original_dispatch_incoming = runtime.dispatch_incoming

    def fake_dispatch_incoming(incoming):
        captured["incoming"] = incoming

    runtime.dispatch_incoming = fake_dispatch_incoming

    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1", client_type="webapp")
    message = _sample_message()
    message.elements = [{"id": "el-1", "path": "/tmp/random.jpg"}]

    try:
        with _swap_attr(chainlit_entry.cl, "context", SimpleNamespace(session=fake_session)):
            asyncio.run(chainlit_entry._on_message(message))
    finally:
        runtime.dispatch_incoming = original_dispatch_incoming

    incoming = captured["incoming"]
    assert incoming is not None
    assert incoming.elements == [{"id": "el-1", "path": "/tmp/random.jpg"}]


def test_start_and_stop_discord_bridge_lifecycle():
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
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
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
    )

    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    provider = LocalFileStorageClient(base_dir=tmp_path / "public" / "easierlit")
    fake_data_layer = SimpleNamespace(storage_provider=provider)
    calls = {"preflight": 0}

    monkeypatch.setattr(chainlit_entry, "_apply_runtime_configuration", lambda: None)
    monkeypatch.setattr(chainlit_entry, "get_data_layer", lambda: fake_data_layer)
    monkeypatch.setattr(chainlit_entry, "require_login", lambda: True)

    async def _noop_async():
        return None

    monkeypatch.setattr(chainlit_entry.RUNTIME, "start_dispatcher", _noop_async)
    monkeypatch.setattr(chainlit_entry, "_start_discord_bridge_if_needed", _noop_async)

    async def _fake_assert_local_storage_operational(storage_provider):
        del storage_provider
        calls["preflight"] += 1

    monkeypatch.setattr(
        chainlit_entry,
        "assert_local_storage_operational",
        _fake_assert_local_storage_operational,
    )

    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = True
    asyncio.run(chainlit_entry._on_app_startup())

    assert calls["preflight"] == 1


def test_local_storage_route_serves_file_without_public_mount(tmp_path):
    runtime = get_runtime()
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-test.db"),
        local_storage_dir=tmp_path / "outside",
    )
    storage_provider = _resolve_local_storage_provider(persistence)

    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=persistence,
    )

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._apply_runtime_configuration()
    uploaded = asyncio.run(storage_provider.upload_file("user-1/image.png", b"payload"))

    with TestClient(chainlit_entry.chainlit_app) as client:
        response = client.get(uploaded["url"])

    assert uploaded["url"] == "/easierlit/local/user-1/image.png"
    assert response.status_code == 200
    assert response.content == b"payload"


def test_local_storage_route_missing_file_returns_404_not_spa_html(tmp_path):
    runtime = get_runtime()
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-test.db"),
        local_storage_dir=tmp_path / "outside",
    )
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=persistence,
    )

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None
    chainlit_entry._LOCAL_STORAGE_ROUTE_REGISTERED = False
    chainlit_entry._apply_runtime_configuration()

    with TestClient(chainlit_entry.chainlit_app) as client:
        response = client.get("/easierlit/local/not-found/random.jpg")

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found."


def test_local_storage_route_resolves_tilde_local_storage_dir(tmp_path, monkeypatch):
    runtime = get_runtime()
    monkeypatch.setenv("HOME", str(tmp_path))
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-test.db"),
        local_storage_dir="~/fablit/workspace/images",
    )

    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=persistence,
    )

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None
    chainlit_entry._apply_runtime_configuration()

    provider = _resolve_local_storage_provider(persistence)
    uploaded = asyncio.run(provider.upload_file("user-1/image.png", b"payload"))

    assert provider.base_dir == (tmp_path / "fablit" / "workspace" / "images").resolve()
    assert (provider.base_dir / "user-1" / "image.png").is_file()

    with TestClient(chainlit_entry.chainlit_app) as client:
        response = client.get(uploaded["url"])

    assert response.status_code == 200
    assert response.content == b"payload"


def test_local_storage_route_uses_runtime_persistence_provider_only(tmp_path, monkeypatch):
    runtime = get_runtime()
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-test.db"),
        local_storage_dir=tmp_path / "persistence-images",
    )
    persistence_provider = _resolve_local_storage_provider(persistence)
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=persistence,
    )

    data_layer_provider = LocalFileStorageClient(base_dir=tmp_path / "actual-images")
    fake_data_layer = SimpleNamespace(storage_provider=data_layer_provider)
    monkeypatch.setattr(chainlit_entry, "get_data_layer", lambda: fake_data_layer)

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._apply_runtime_configuration()

    uploaded = asyncio.run(persistence_provider.upload_file("user-1/image.png", b"payload"))
    assert not (data_layer_provider.base_dir / "user-1" / "image.png").exists()

    with TestClient(chainlit_entry.chainlit_app) as client:
        response = client.get(uploaded["url"])

    assert response.status_code == 200
    assert response.content == b"payload"


def test_local_storage_route_uses_latest_runtime_persistence_provider_after_rebind(tmp_path):
    runtime = get_runtime()

    first_persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-first.db"),
        local_storage_dir=tmp_path / "images-first",
    )
    first_provider = _resolve_local_storage_provider(first_persistence)
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=first_persistence,
    )
    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None
    chainlit_entry._LOCAL_STORAGE_ROUTE_REGISTERED = False
    chainlit_entry._apply_runtime_configuration()
    first_uploaded = asyncio.run(first_provider.upload_file("user-1/first.png", b"first"))

    with TestClient(chainlit_entry.chainlit_app) as client:
        first_response = client.get(first_uploaded["url"])
    assert first_response.status_code == 200
    assert first_response.content == b"first"

    second_persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "route-second.db"),
        local_storage_dir=tmp_path / "images-second",
    )
    second_provider = _resolve_local_storage_provider(second_persistence)
    runtime.unbind()
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=second_persistence,
    )
    chainlit_entry._CONFIG_APPLIED = False
    # Intentionally keep route registration and previous provider state.
    second_uploaded = asyncio.run(second_provider.upload_file("user-2/second.png", b"second"))

    with TestClient(chainlit_entry.chainlit_app) as client:
        second_response = client.get(second_uploaded["url"])
    assert second_response.status_code == 200
    assert second_response.content == b"second"


def test_default_sqlite_data_layer_get_thread_refreshes_element_url_from_object_key(
    tmp_path, monkeypatch
):
    runtime = get_runtime()
    runtime.unbind()
    config.code.data_layer = None

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._LOCAL_STORAGE_PROVIDER = None

    class _FakeSQLAlchemyDataLayer:
        def __init__(self, conninfo: str, storage_provider=None):
            self._conninfo = conninfo
            self.storage_provider = storage_provider

        async def get_thread(self, thread_id: str):
            return {
                "id": thread_id,
                "elements": [
                    {
                        "id": "el-1",
                        "objectKey": "user-1/image.png",
                        "url": "/stale/url",
                    },
                    {
                        "id": "el-2",
                        "url": "https://example.com/keep.png",
                    },
                ],
            }

    class _FakeStorageProvider:
        async def get_read_url(self, object_key: str) -> str:
            return f"/easierlit/local/{object_key}"

    monkeypatch.setattr(chainlit_sql_alchemy, "SQLAlchemyDataLayer", _FakeSQLAlchemyDataLayer)

    db_path = tmp_path / "refresh-thread-url.db"
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(),
        persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=str(db_path)),
    )

    try:
        chainlit_entry._apply_runtime_configuration()
        assert config.code.data_layer is not None

        data_layer = config.code.data_layer()
        data_layer.storage_provider = _FakeStorageProvider()
        thread = asyncio.run(data_layer.get_thread("thread-1"))

        assert thread["elements"][0]["url"] == "/easierlit/local/user-1/image.png"
        assert thread["elements"][1]["url"] == "https://example.com/keep.png"
    finally:
        runtime.unbind()
        config.code.data_layer = None
        chainlit_entry._CONFIG_APPLIED = False
        chainlit_entry._LOCAL_STORAGE_PROVIDER = None

        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

        if previous_literal_api_key is None:
            os.environ.pop("LITERAL_API_KEY", None)
        else:
            os.environ["LITERAL_API_KEY"] = previous_literal_api_key
