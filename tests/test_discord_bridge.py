import asyncio
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.types import Pagination, ThreadFilter

from easierlit import EasierlitApp, EasierlitAuthConfig, EasierlitClient
from easierlit.discord_bridge import EasierlitDiscordBridge
from easierlit.runtime import RuntimeRegistry
from easierlit.sqlite_bootstrap import ensure_sqlite_schema
from chainlit.user import PersistedUser


@contextmanager
def _swap_attr(obj, name: str, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


class _FakeDiscordClient:
    def __init__(self):
        self.user = SimpleNamespace(id=999, mentioned_in=lambda _message: False)
        self._channels: dict[int, object] = {}

    def event(self, func):
        setattr(self, func.__name__, func)
        return func

    async def start(self, _token: str):
        return None

    async def close(self):
        return None

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class _FakeSendChannel:
    def __init__(self):
        self.messages: list[str] = []
        self.file_names: list[list[str]] = []

    async def send(self, content: str, **kwargs):
        self.messages.append(content)
        files = kwargs.get("files") or []
        self.file_names.append([getattr(file, "filename", "") for file in files])


def test_resolve_thread_target_normalizes_dm_name_without_date_suffix():
    import easierlit.discord_bridge as bridge_module

    class FakeThread:
        pass

    class FakeForum:
        pass

    class FakeDM:
        def __init__(self, channel_id: int):
            self.id = channel_id

    class FakeGroup:
        pass

    class FakeText:
        pass

    fake_client = _FakeDiscordClient()
    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    message = SimpleNamespace(
        channel=FakeDM(123),
        author=SimpleNamespace(name="geobuyee"),
        content="hello",
    )

    with ExitStack() as stack:
        stack.enter_context(_swap_attr(bridge_module.discord, "Thread", FakeThread))
        stack.enter_context(_swap_attr(bridge_module.discord, "ForumChannel", FakeForum))
        stack.enter_context(_swap_attr(bridge_module.discord, "DMChannel", FakeDM))
        stack.enter_context(_swap_attr(bridge_module.discord, "GroupChannel", FakeGroup))
        stack.enter_context(_swap_attr(bridge_module.discord, "TextChannel", FakeText))

        thread_id, thread_name, _channel, bind = asyncio.run(
            bridge._resolve_thread_target(message)
        )

    assert thread_id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "123"))
    assert thread_name == "geobuyee Discord DM"
    assert bind is True


def test_resolve_thread_target_text_channel_uses_created_thread_id():
    import easierlit.discord_bridge as bridge_module

    class FakeThread:
        pass

    class FakeForum:
        pass

    class FakeDM:
        pass

    class FakeGroup:
        pass

    class FakeCreatedThread:
        def __init__(self, name: str, channel_id: int):
            self.name = name
            self.id = channel_id

    class FakeText:
        def __init__(self, channel_id: int):
            self.id = channel_id
            self.created_names: list[str] = []

        async def create_thread(self, *, name: str, message):
            self.created_names.append(name)
            return FakeCreatedThread(name="worker-thread", channel_id=7777)

    fake_client = _FakeDiscordClient()
    fake_client.user = SimpleNamespace(id=42, mentioned_in=lambda _message: True)
    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    text_channel = FakeText(444)
    message = SimpleNamespace(
        channel=text_channel,
        author=SimpleNamespace(name="discord-user"),
        content="<@42> summarize this",
    )

    with ExitStack() as stack:
        stack.enter_context(_swap_attr(bridge_module.discord, "Thread", FakeThread))
        stack.enter_context(_swap_attr(bridge_module.discord, "ForumChannel", FakeForum))
        stack.enter_context(_swap_attr(bridge_module.discord, "DMChannel", FakeDM))
        stack.enter_context(_swap_attr(bridge_module.discord, "GroupChannel", FakeGroup))
        stack.enter_context(_swap_attr(bridge_module.discord, "TextChannel", FakeText))

        thread_id, thread_name, resolved_channel, bind = asyncio.run(
            bridge._resolve_thread_target(message)
        )

    assert thread_id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "7777"))
    assert thread_name == "worker-thread"
    assert bind is False
    assert text_channel.created_names == ["summarize this"]
    assert getattr(resolved_channel, "name", None) == "worker-thread"


def test_on_message_requires_mention_for_non_dm_channels():
    import easierlit.discord_bridge as bridge_module

    calls = {"processed": 0}

    class _Bridge(EasierlitDiscordBridge):
        async def _process_discord_message(self, **_kwargs):
            calls["processed"] += 1

        async def _resolve_thread_target(self, _message):
            return "thread", "name", SimpleNamespace(), False

    class FakeDM:
        pass

    class FakeText:
        def __init__(self):
            self.id = 500

    fake_client = _FakeDiscordClient()
    fake_client.user = SimpleNamespace(id=777, mentioned_in=lambda _message: False)
    bridge = _Bridge(runtime=RuntimeRegistry(), bot_token="token", client=fake_client)

    message = SimpleNamespace(
        channel=FakeText(),
        author=SimpleNamespace(id=1),
        content="hello",
    )

    with ExitStack() as stack:
        stack.enter_context(_swap_attr(bridge_module.discord, "DMChannel", FakeDM))
        stack.enter_context(_swap_attr(bridge_module.discord, "TextChannel", FakeText))
        asyncio.run(bridge._on_message(message))

    assert calls["processed"] == 0


def test_on_message_processes_dm_without_mention():
    import easierlit.discord_bridge as bridge_module

    calls = {"processed": 0}

    class _Bridge(EasierlitDiscordBridge):
        async def _process_discord_message(self, **_kwargs):
            calls["processed"] += 1

        async def _resolve_thread_target(self, _message):
            return "thread", "name", SimpleNamespace(), True

    class FakeDM:
        def __init__(self):
            self.id = 88

    fake_client = _FakeDiscordClient()
    fake_client.user = SimpleNamespace(id=777, mentioned_in=lambda _message: False)
    bridge = _Bridge(runtime=RuntimeRegistry(), bot_token="token", client=fake_client)

    message = SimpleNamespace(
        channel=FakeDM(),
        author=SimpleNamespace(id=1),
        content="hello",
    )

    with _swap_attr(bridge_module.discord, "DMChannel", FakeDM):
        asyncio.run(bridge._on_message(message))

    assert calls["processed"] == 1


def test_owner_rebind_uses_runtime_auth_and_merges_metadata():
    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def get_thread(self, thread_id: str):
            return {"id": thread_id, "metadata": {"existing": "keep"}}

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, **_kwargs):
            self.updated_threads.append(
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "metadata": metadata,
                }
            )

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user"))
    asyncio.run(
        bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=message,
        )
    )

    assert len(fake_data_layer.updated_threads) == 1
    updated = fake_data_layer.updated_threads[0]
    assert updated["thread_id"] == "thread-1"
    assert updated["user_id"] == "user-admin"
    assert updated["metadata"]["existing"] == "keep"
    assert updated["metadata"]["easierlit_discord_owner_id"] == "321"
    assert updated["metadata"]["easierlit_discord_owner_name"] == "discord-user"


def test_skip_enqueue_when_owner_cannot_be_resolved(caplog):
    class _FakeDataLayer:
        def __init__(self):
            self.update_calls = 0

        async def get_thread(self, _thread_id: str):
            return None

        async def update_thread(self, **_kwargs):
            self.update_calls += 1

    class _FakeApp:
        def __init__(self):
            self.enqueue_calls: list[dict[str, object]] = []

        def enqueue(self, **kwargs):
            self.enqueue_calls.append(dict(kwargs))
            return "message-id"

    fake_data_layer = _FakeDataLayer()
    fake_app = _FakeApp()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    runtime.bind(
        client=SimpleNamespace(dispatch_incoming=lambda _incoming: None),
        app=fake_app,
        auth=None,
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    with caplog.at_level("WARNING"):
        asyncio.run(
            bridge._process_discord_message(
                message=SimpleNamespace(
                    author=SimpleNamespace(id=321, name="discord-user"),
                    attachments=[],
                    content="hello",
                    created_at="2026-02-20T00:00:00.000Z",
                ),
                thread_id="thread-1",
                thread_name="Thread Name",
                channel=SimpleNamespace(id=456),
                bind_thread_to_user=False,
            )
        )

    assert fake_data_layer.update_calls == 0
    assert fake_app.enqueue_calls == []
    assert "runtime owner could not be resolved" in caplog.text


def test_owner_rebind_upserts_missing_thread_with_single_update():
    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads: list[dict] = []
            self.threads: dict[str, dict] = {}

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            return None

        async def get_thread(self, thread_id: str):
            return self.threads.get(thread_id)

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            self.updated_threads.append(
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "metadata": metadata,
                    "name": name,
                }
            )
            thread = self.threads.setdefault(
                thread_id,
                {"id": thread_id, "createdAt": None, "metadata": {}, "userId": None, "name": None},
            )
            if thread["createdAt"] is None:
                thread["createdAt"] = "2026-02-20T00:00:00.000Z"
            if isinstance(metadata, dict):
                thread["metadata"].update(metadata)
            if user_id is not None:
                thread["userId"] = user_id
            if name is not None:
                thread["name"] = name

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user"))
    asyncio.run(
        bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=message,
            thread_name="Thread Name",
            session_metadata={"session": "discord"},
        )
    )

    assert len(fake_data_layer.updated_threads) == 1
    updated = fake_data_layer.updated_threads[0]
    assert updated["thread_id"] == "thread-1"
    assert updated["user_id"] == "user-admin"
    assert updated["name"] == "Thread Name"
    assert updated["metadata"]["session"] == "discord"
    assert fake_data_layer.threads["thread-1"]["createdAt"] == "2026-02-20T00:00:00.000Z"


def test_start_backfills_all_threads_to_runtime_auth_owner():
    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads: list[tuple[str, str | None]] = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def execute_sql(self, *, query: str, parameters: dict):
            assert 'SELECT "id" AS id FROM threads' in query
            assert parameters == {}
            return [{"id": "thread-1"}, {"id": "thread-2"}]

        async def update_thread(self, thread_id: str, user_id=None, **_kwargs):
            self.updated_threads.append((thread_id, user_id))

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[lambda _app: None],
        worker_mode="thread",
    )
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    async def _start_and_stop():
        await bridge.start()
        await asyncio.sleep(0)
        await bridge.stop()

    asyncio.run(_start_and_stop())

    assert fake_data_layer.updated_threads == [
        ("thread-1", "user-admin"),
        ("thread-2", "user-admin"),
    ]


def test_start_backfill_continues_when_some_thread_updates_fail():
    class _FakeDataLayer:
        def __init__(self):
            self.update_attempts: list[tuple[str, str | None]] = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def execute_sql(self, *, query: str, parameters: dict):
            assert 'SELECT "id" AS id FROM threads' in query
            assert parameters == {}
            return [{"id": "thread-1"}, {"id": "thread-2"}, {"id": "thread-3"}]

        async def update_thread(self, thread_id: str, user_id=None, **_kwargs):
            self.update_attempts.append((thread_id, user_id))
            if thread_id == "thread-2":
                raise RuntimeError("boom")

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(
        on_message=lambda _app, _incoming: None,
        run_funcs=[lambda _app: None],
        worker_mode="thread",
    )
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    async def _start_and_stop():
        await bridge.start()
        await asyncio.sleep(0)
        await bridge.stop()

    asyncio.run(_start_and_stop())

    assert fake_data_layer.update_attempts == [
        ("thread-1", "user-admin"),
        ("thread-2", "user-admin"),
        ("thread-3", "user-admin"),
    ]


def test_discord_inbound_uses_enqueue_path_only():
    events: list[str] = []

    class _FakeDataLayer:
        def __init__(self):
            self.thread = {
                "id": "thread-1",
                "userId": "legacy-owner",
                "userIdentifier": "legacy",
                "metadata": {"existing": "keep"},
            }
            self.updated_threads: list[dict[str, object]] = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def get_thread(self, _thread_id: str):
            return dict(self.thread)

        async def get_thread_author(self, _thread_id: str):
            return str(self.thread.get("userIdentifier"))

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            events.append("upsert")
            self.updated_threads.append(
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "metadata": metadata,
                    "name": name,
                }
            )
            if user_id is not None:
                self.thread["userId"] = user_id
                self.thread["userIdentifier"] = "admin"
            if isinstance(metadata, dict):
                self.thread["metadata"] = dict(metadata)
            if name is not None:
                self.thread["name"] = name

    class _FakeApp:
        def __init__(self):
            self.enqueue_calls: list[dict[str, object]] = []

        def enqueue(self, **kwargs):
            events.append("enqueue")
            self.enqueue_calls.append(dict(kwargs))
            return "message-id"

    fake_data_layer = _FakeDataLayer()
    fake_app = _FakeApp()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    runtime.bind(
        client=SimpleNamespace(dispatch_incoming=lambda _incoming: None),
        app=fake_app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(
        author=SimpleNamespace(id=321, name="discord-user"),
        attachments=[],
        content="hello",
        created_at="2026-02-20T00:00:00.000Z",
    )
    asyncio.run(
        bridge._process_discord_message(
            message=message,
            thread_id="thread-1",
            thread_name="Thread Name",
            channel=SimpleNamespace(id=777),
            bind_thread_to_user=False,
        )
    )

    assert events == ["upsert", "enqueue"]
    assert len(fake_data_layer.updated_threads) == 1
    assert fake_data_layer.updated_threads[0]["user_id"] == "user-admin"
    assert len(fake_app.enqueue_calls) == 1
    enqueue_call = fake_app.enqueue_calls[0]
    assert enqueue_call["thread_id"] == "thread-1"
    assert enqueue_call["author"] == "discord-user"
    assert runtime.get_discord_channel_for_thread("thread-1") == 777


def test_missing_thread_is_created_with_runtime_owner_before_enqueue():
    events: list[str] = []

    class _FakeDataLayer:
        def __init__(self):
            self.threads: dict[str, dict[str, object]] = {}
            self.user_identifiers = {"user-admin": "admin"}

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def get_thread(self, thread_id: str):
            thread = self.threads.get(thread_id)
            return dict(thread) if isinstance(thread, dict) else None

        async def get_thread_author(self, thread_id: str):
            thread = self.threads.get(thread_id) or {}
            identifier = thread.get("userIdentifier")
            if isinstance(identifier, str) and identifier:
                return identifier
            raise ValueError("author missing")

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            events.append("upsert")
            thread = self.threads.setdefault(thread_id, {"id": thread_id, "metadata": {}})
            if user_id is not None:
                thread["userId"] = user_id
                thread["userIdentifier"] = self.user_identifiers.get(str(user_id))
            if isinstance(metadata, dict):
                thread["metadata"] = dict(metadata)
            if name is not None:
                thread["name"] = name

    class _FakeApp:
        def __init__(self):
            self.enqueue_calls: list[dict[str, object]] = []

        def enqueue(self, **kwargs):
            events.append("enqueue")
            self.enqueue_calls.append(dict(kwargs))
            return "message-id"

    fake_data_layer = _FakeDataLayer()
    fake_app = _FakeApp()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    runtime.bind(
        client=SimpleNamespace(dispatch_incoming=lambda _incoming: None),
        app=fake_app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    asyncio.run(
        bridge._process_discord_message(
            message=SimpleNamespace(
                author=SimpleNamespace(id=321, name="discord-user"),
                attachments=[],
                content="hello",
                created_at="2026-02-20T00:00:00.000Z",
            ),
            thread_id="thread-1",
            thread_name="Thread Name",
            channel=SimpleNamespace(id=777),
            bind_thread_to_user=False,
        )
    )

    assert events == ["upsert", "enqueue"]
    created_thread = fake_data_layer.threads["thread-1"]
    assert created_thread["userId"] == "user-admin"
    assert created_thread["userIdentifier"] == "admin"
    assert len(fake_app.enqueue_calls) == 1


def test_owner_mismatch_is_fixed_before_enqueue():
    events: list[str] = []

    class _FakeDataLayer:
        def __init__(self):
            self.thread = {
                "id": "thread-1",
                "userId": "legacy-owner",
                "userIdentifier": "legacy",
                "metadata": {},
            }

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def get_thread(self, _thread_id: str):
            return dict(self.thread)

        async def get_thread_author(self, _thread_id: str):
            return str(self.thread.get("userIdentifier"))

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            events.append("upsert")
            if user_id is not None:
                self.thread["userId"] = user_id
                self.thread["userIdentifier"] = "admin"
            if isinstance(metadata, dict):
                self.thread["metadata"] = dict(metadata)
            if name is not None:
                self.thread["name"] = name

    class _FakeApp:
        def __init__(self):
            self.enqueue_calls: list[dict[str, object]] = []

        def enqueue(self, **kwargs):
            events.append("enqueue")
            self.enqueue_calls.append(dict(kwargs))
            return "message-id"

    fake_data_layer = _FakeDataLayer()
    fake_app = _FakeApp()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    runtime.bind(
        client=SimpleNamespace(dispatch_incoming=lambda _incoming: None),
        app=fake_app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    asyncio.run(
        bridge._process_discord_message(
            message=SimpleNamespace(
                author=SimpleNamespace(id=321, name="discord-user"),
                attachments=[],
                content="hello",
                created_at="2026-02-20T00:00:00.000Z",
            ),
            thread_id="thread-1",
            thread_name="Thread Name",
            channel=SimpleNamespace(id=777),
            bind_thread_to_user=False,
        )
    )

    assert events == ["upsert", "enqueue"]
    assert fake_data_layer.thread["userId"] == "user-admin"
    assert fake_data_layer.thread["userIdentifier"] == "admin"
    assert len(fake_app.enqueue_calls) == 1


def test_owner_rebind_verifies_owner_after_successful_upsert():
    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads: list[dict[str, object]] = []
            self.thread = {
                "id": "thread-1",
                "userId": "legacy-owner",
                "metadata": {"existing": "keep"},
            }

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def execute_sql(self, *, query: str, parameters: dict):
            assert parameters == {"thread_id": "thread-1"}
            assert 'FROM threads WHERE "id" = :thread_id' in query
            return [
                {
                    "id": self.thread["id"],
                    "user_id": self.thread["userId"],
                    "metadata": self.thread["metadata"],
                }
            ]

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            self.updated_threads.append(
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "metadata": metadata,
                    "name": name,
                }
            )
            if user_id is not None:
                self.thread["userId"] = user_id
            if isinstance(metadata, dict):
                self.thread["metadata"] = dict(metadata)
            if name is not None:
                self.thread["name"] = name

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user"))
    asyncio.run(
        bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=message,
            thread_name="Thread Name",
            session_metadata={"session": "discord"},
        )
    )

    assert len(fake_data_layer.updated_threads) == 1
    assert fake_data_layer.thread["userId"] == "user-admin"
    assert fake_data_layer.thread["metadata"]["session"] == "discord"
    assert fake_data_layer.thread["metadata"]["easierlit_discord_owner_id"] == "321"


def test_owner_rebind_attempts_single_update_without_retry():
    class _FakeDataLayer:
        def __init__(self):
            self.update_calls = 0
            self.noop_updates_remaining = 2
            self.thread = {
                "id": "thread-1",
                "userId": None,
                "metadata": {"existing": "keep"},
            }

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def execute_sql(self, *, query: str, parameters: dict):
            assert parameters == {"thread_id": "thread-1"}
            assert 'FROM threads WHERE "id" = :thread_id' in query
            return [
                {
                    "id": self.thread["id"],
                    "user_id": self.thread["userId"],
                    "metadata": self.thread["metadata"],
                }
            ]

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            self.update_calls += 1
            if self.noop_updates_remaining > 0:
                self.noop_updates_remaining -= 1
                return
            if user_id is not None:
                self.thread["userId"] = user_id
            if isinstance(metadata, dict):
                self.thread["metadata"] = dict(metadata)
            if name is not None:
                self.thread["name"] = name

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user"))
    asyncio.run(
        bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=message,
            thread_name="Thread Name",
            session_metadata={"session": "discord"},
        )
    )

    assert fake_data_layer.update_calls == 1
    assert fake_data_layer.thread["userId"] is None


def test_owner_rebind_uses_cached_runtime_owner_id_when_runtime_lookup_fails():
    class _FakeDataLayer:
        def __init__(self):
            self.fail_owner_lookup = False
            self.updated_threads: list[dict[str, object]] = []
            self.thread = {"id": "thread-1", "userId": None, "metadata": {}}

        async def get_user(self, identifier: str):
            if self.fail_owner_lookup:
                raise RuntimeError("owner lookup failed")
            if identifier == "admin":
                return PersistedUser(
                    id="user-admin",
                    createdAt="2026-02-20T00:00:00.000Z",
                    identifier="admin",
                    metadata={"name": "admin"},
                )
            return None

        async def create_user(self, _user):
            raise AssertionError("create_user should not be called")

        async def execute_sql(self, *, query: str, parameters: dict):
            if 'SELECT "id" AS id FROM threads' in query:
                assert parameters == {}
                return []
            if 'FROM threads WHERE "id" = :thread_id' in query:
                assert parameters == {"thread_id": "thread-1"}
                return [
                    {
                        "id": self.thread["id"],
                        "user_id": self.thread["userId"],
                        "metadata": self.thread["metadata"],
                    }
                ]
            raise AssertionError(f"Unexpected query: {query}")

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            self.updated_threads.append(
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "metadata": metadata,
                    "name": name,
                }
            )
            if user_id is not None:
                self.thread["userId"] = user_id
            if isinstance(metadata, dict):
                self.thread["metadata"] = dict(metadata)
            if name is not None:
                self.thread["name"] = name

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    async def _scenario() -> None:
        await bridge.start()
        fake_data_layer.fail_owner_lookup = True
        await bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user")),
            thread_name="Thread Name",
            session_metadata={"session": "discord"},
        )
        await bridge.stop()

    asyncio.run(_scenario())

    assert bridge._cached_runtime_auth_owner_user_id == "user-admin"
    assert fake_data_layer.thread["userId"] == "user-admin"
    assert len(fake_data_layer.updated_threads) == 1


def test_delete_then_recreate_then_discord_message_still_authorized(tmp_path):
    db_path = ensure_sqlite_schema(tmp_path / "discord-recreate.db")
    data_layer = SQLAlchemyDataLayer(conninfo=f"sqlite+aiosqlite:///{db_path}")

    runtime = RuntimeRegistry(data_layer_getter=lambda: data_layer)

    class _FakeApp:
        def __init__(self):
            self.enqueue_calls: list[dict[str, object]] = []

        def enqueue(self, **kwargs):
            self.enqueue_calls.append(dict(kwargs))
            return "message-id"

    fake_app = _FakeApp()
    runtime.bind(
        client=SimpleNamespace(dispatch_incoming=lambda _incoming: None),
        app=fake_app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: data_layer,
        client=_FakeDiscordClient(),
    )

    async def _scenario() -> None:
        thread_id = "thread-1"
        message = SimpleNamespace(
            author=SimpleNamespace(id=321, name="discord-user"),
            attachments=[],
            content="hello",
            created_at="2026-02-20T00:00:00.000Z",
        )
        channel = SimpleNamespace(id=9876)
        await bridge._process_discord_message(
            message=message,
            thread_id=thread_id,
            thread_name="Thread One",
            channel=channel,
            bind_thread_to_user=False,
        )

        persisted_user = await data_layer.get_user("admin")
        assert persisted_user is not None
        assert await data_layer.get_thread_author(thread_id) == persisted_user.identifier

        await data_layer.delete_thread(thread_id)

        await bridge._process_discord_message(
            message=message,
            thread_id=thread_id,
            thread_name="Thread One Recreated",
            channel=channel,
            bind_thread_to_user=False,
        )

        threads_page = await data_layer.list_threads(
            Pagination(first=20, cursor=None),
            ThreadFilter(search=None, userId=persisted_user.id),
        )
        assert [thread.get("id") for thread in threads_page.data] == [thread_id]

        recreated_thread = await data_layer.get_thread(thread_id)
        assert recreated_thread is not None
        assert recreated_thread.get("userId") == persisted_user.id
        assert await data_layer.get_thread_author(thread_id) == persisted_user.identifier
        assert len(fake_app.enqueue_calls) == 2

    asyncio.run(_scenario())


def test_send_outgoing_command_sends_message_and_tool_prefix():
    fake_client = _FakeDiscordClient()
    channel = _FakeSendChannel()
    fake_client._channels[123] = channel

    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    add_message = SimpleNamespace(
        command="add_message",
        thread_id="thread-1",
        content="hello",
        author="Assistant",
    )
    add_tool = SimpleNamespace(
        command="add_tool",
        thread_id="thread-1",
        content="running",
        author="Search",
    )

    message_result = asyncio.run(bridge.send_outgoing_command(123, add_message))
    tool_result = asyncio.run(bridge.send_outgoing_command(123, add_tool))

    assert message_result is True
    assert tool_result is True
    assert channel.messages == ["hello", "[Search] running"]


def test_send_typing_state_keeps_typing_until_stopped():
    class _FakeTypingChannel:
        def __init__(self):
            self.enter_count = 0
            self.exit_count = 0

        def typing(self):
            channel = self

            class _Typing:
                async def __aenter__(self):
                    channel.enter_count += 1
                    return None

                async def __aexit__(self, exc_type, exc, tb):
                    channel.exit_count += 1
                    return False

            return _Typing()

    fake_client = _FakeDiscordClient()
    typing_channel = _FakeTypingChannel()
    fake_client._channels[777] = typing_channel
    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    async def _scenario():
        assert await bridge.send_typing_state(777, True) is True
        await asyncio.sleep(0.05)
        assert typing_channel.enter_count >= 1
        assert await bridge.send_typing_state(777, False) is True
        assert typing_channel.exit_count >= 1

    asyncio.run(_scenario())


def test_send_outgoing_command_returns_false_for_unsupported_command():
    fake_client = _FakeDiscordClient()
    channel = _FakeSendChannel()
    fake_client._channels[123] = channel

    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    unsupported = SimpleNamespace(
        command="update_message",
        thread_id="thread-1",
        content="hello",
        author="Assistant",
    )
    result = asyncio.run(bridge.send_outgoing_command(123, unsupported))

    assert result is False
    assert channel.messages == []


def test_send_outgoing_command_fetches_channel_when_cache_misses():
    class _FetchOnlyClient(_FakeDiscordClient):
        def __init__(self):
            super().__init__()
            self.fetch_calls = 0

        def get_channel(self, _channel_id: int):
            return None

        async def fetch_channel(self, channel_id: int):
            self.fetch_calls += 1
            return self._channels.get(channel_id)

    fake_client = _FetchOnlyClient()
    channel = _FakeSendChannel()
    fake_client._channels[456] = channel

    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    add_message = SimpleNamespace(
        command="add_message",
        thread_id="thread-1",
        content="hello",
        author="Assistant",
    )
    result = asyncio.run(bridge.send_outgoing_command(456, add_message))

    assert result is True
    assert fake_client.fetch_calls == 1
    assert channel.messages == ["hello"]


def test_send_outgoing_command_sends_element_file_attachment(tmp_path):
    fake_client = _FakeDiscordClient()
    channel = _FakeSendChannel()
    fake_client._channels[789] = channel

    bridge = EasierlitDiscordBridge(
        runtime=RuntimeRegistry(),
        bot_token="token",
        client=fake_client,
    )

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-image-bytes")

    add_message = SimpleNamespace(
        command="add_message",
        thread_id="thread-1",
        content="with image",
        author="Assistant",
        elements=[{"path": str(image_path), "name": "sample.png"}],
    )
    result = asyncio.run(bridge.send_outgoing_command(789, add_message))

    assert result is True
    assert channel.messages == ["with image"]
    assert channel.file_names == [["sample.png"]]
