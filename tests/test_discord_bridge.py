import asyncio
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest

from easierlit import EasierlitApp, EasierlitAuthConfig, EasierlitClient
from easierlit.discord_bridge import EasierlitDiscordBridge
from easierlit.runtime import RuntimeRegistry
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


def test_resolve_thread_target_text_channel_creates_thread_and_uses_channel_id():
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

    assert thread_id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "444"))
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


def test_owner_rebind_falls_back_to_dm_persisted_user_when_runtime_auth_missing():
    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads = []

        async def get_user(self, _identifier: str):
            return None

        async def create_user(self, _user):
            return None

        async def get_thread(self, thread_id: str):
            return {"id": thread_id, "metadata": {}}

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

    bridge = EasierlitDiscordBridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    message = SimpleNamespace(author=SimpleNamespace(id=321, name="discord-user"))
    persisted_dm_user = PersistedUser(
        id="user-dm",
        createdAt="2026-02-20T00:00:00.000Z",
        identifier="discord-user",
        metadata={"name": "discord-user"},
    )
    asyncio.run(
        bridge._rebind_discord_thread_owner_to_runtime_auth(
            thread_id="thread-1",
            message=message,
            bind_thread_to_user=True,
            discord_user=persisted_dm_user,
        )
    )

    assert len(fake_data_layer.updated_threads) == 1
    updated = fake_data_layer.updated_threads[0]
    assert updated["thread_id"] == "thread-1"
    assert updated["user_id"] == "user-dm"
    assert updated["metadata"]["easierlit_discord_owner_id"] == "321"


def test_owner_rebind_seeds_missing_thread_before_metadata_upsert():
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
            if metadata is None and thread["createdAt"] is None:
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

    assert len(fake_data_layer.updated_threads) == 2
    first_update = fake_data_layer.updated_threads[0]
    second_update = fake_data_layer.updated_threads[1]
    assert first_update["thread_id"] == "thread-1"
    assert first_update["metadata"] is None
    assert first_update["user_id"] == "user-admin"
    assert first_update["name"] == "Thread Name"
    assert second_update["thread_id"] == "thread-1"
    assert second_update["user_id"] == "user-admin"
    assert second_update["metadata"]["session"] == "discord"
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


def test_process_discord_message_uses_auth_owner_session_and_keeps_discord_author():
    import easierlit.discord_bridge as bridge_module
    from chainlit.config import config

    captured: dict[str, object] = {}

    class _FakeDataLayer:
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
            return None

        async def get_thread(self, thread_id: str):
            return {"id": thread_id, "metadata": {}}

        async def update_thread(self, thread_id: str, user_id=None, metadata=None, name=None, **_kwargs):
            captured["upsert_user_id"] = user_id
            captured["upsert_name"] = name
            captured["upsert_metadata"] = metadata

    class _FakeSession:
        def to_persistable(self):
            return {"session": "discord"}

        async def delete(self):
            return None

    class _Bridge(EasierlitDiscordBridge):
        def _init_discord_context(self, *, session, channel, message):
            captured["session_user"] = session.user
            return SimpleNamespace(session=_FakeSession())

        async def _get_or_create_user(self, _discord_user):
            return SimpleNamespace(metadata={"name": "discord-user"})

    class _FakeMessage:
        def __init__(self, **_kwargs):
            captured["message_author"] = _kwargs.get("author")
            self.content = _kwargs.get("content", "")

        async def send(self):
            return self

    class _FakeTyping:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeChannel:
        def typing(self):
            return _FakeTyping()

    async def _fake_download_discord_files(_session, _attachments):
        return []

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

    bridge = _Bridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    previous_on_chat_start = config.code.on_chat_start
    previous_on_message = config.code.on_message
    previous_on_chat_end = config.code.on_chat_end
    config.code.on_chat_start = None
    config.code.on_message = None
    config.code.on_chat_end = None

    message = SimpleNamespace(
        author=SimpleNamespace(id=321, name="discord-user"),
        attachments=[],
        content="hello",
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(
                _swap_attr(bridge_module, "download_discord_files", _fake_download_discord_files)
            )
            stack.enter_context(_swap_attr(bridge_module, "Message", _FakeMessage))
            asyncio.run(
                bridge._process_discord_message(
                    message=message,
                    thread_id="thread-1",
                    thread_name="Thread Name",
                    channel=_FakeChannel(),
                    bind_thread_to_user=False,
                )
            )
    finally:
        config.code.on_chat_start = previous_on_chat_start
        config.code.on_message = previous_on_message
        config.code.on_chat_end = previous_on_chat_end

    session_user = captured.get("session_user")
    assert isinstance(session_user, PersistedUser)
    assert session_user.id == "user-admin"
    assert captured["upsert_user_id"] == "user-admin"
    assert captured["message_author"] == "discord-user"


def test_process_discord_message_upserts_owner_before_on_message():
    import easierlit.discord_bridge as bridge_module
    from chainlit.config import config

    events: list[str] = []

    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            return None

        async def get_thread(self, thread_id: str):
            return {"id": thread_id, "metadata": {"existing": "keep"}}

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

    class _FakeSession:
        def to_persistable(self):
            return {"session": "discord"}

        async def delete(self):
            events.append("session_delete")

    class _Bridge(EasierlitDiscordBridge):
        def _init_discord_context(self, *, session, channel, message):
            return SimpleNamespace(session=_FakeSession())

        async def _get_or_create_user(self, _discord_user):
            return SimpleNamespace(metadata={"name": "discord-user"})

    class _FakeMessage:
        def __init__(self, **_kwargs):
            self.content = _kwargs.get("content", "")

        async def send(self):
            events.append("message_send")
            return self

    class _FakeTyping:
        async def __aenter__(self):
            events.append("typing_enter")

        async def __aexit__(self, exc_type, exc, tb):
            events.append("typing_exit")

    class _FakeChannel:
        def typing(self):
            return _FakeTyping()

    async def _fake_download_discord_files(_session, _attachments):
        return []

    async def _fake_on_chat_start():
        events.append("on_chat_start")

    async def _fake_on_message(_msg):
        events.append("on_message")

    async def _fake_on_chat_end():
        events.append("on_chat_end")

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = _Bridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    previous_on_chat_start = config.code.on_chat_start
    previous_on_message = config.code.on_message
    previous_on_chat_end = config.code.on_chat_end
    config.code.on_chat_start = _fake_on_chat_start
    config.code.on_message = _fake_on_message
    config.code.on_chat_end = _fake_on_chat_end

    message = SimpleNamespace(
        author=SimpleNamespace(id=321, name="discord-user"),
        attachments=[],
        content="hello",
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(
                _swap_attr(bridge_module, "download_discord_files", _fake_download_discord_files)
            )
            stack.enter_context(_swap_attr(bridge_module, "Message", _FakeMessage))
            asyncio.run(
                bridge._process_discord_message(
                    message=message,
                    thread_id="thread-1",
                    thread_name="Thread Name",
                    channel=_FakeChannel(),
                    bind_thread_to_user=False,
                )
            )
    finally:
        config.code.on_chat_start = previous_on_chat_start
        config.code.on_message = previous_on_message
        config.code.on_chat_end = previous_on_chat_end

    assert len(fake_data_layer.updated_threads) == 1
    updated = fake_data_layer.updated_threads[0]
    assert updated["thread_id"] == "thread-1"
    assert updated["name"] == "Thread Name"
    assert updated["user_id"] == "user-admin"
    assert updated["metadata"]["existing"] == "keep"
    assert updated["metadata"]["session"] == "discord"
    assert updated["metadata"]["easierlit_discord_owner_id"] == "321"
    assert events.index("upsert") < events.index("on_message")


def test_process_discord_message_upserts_owner_even_when_handler_raises():
    import easierlit.discord_bridge as bridge_module
    from chainlit.config import config

    events: list[str] = []

    class _FakeDataLayer:
        def __init__(self):
            self.updated_threads = []

        async def get_user(self, identifier: str):
            if identifier == "admin":
                return SimpleNamespace(id="user-admin")
            return None

        async def create_user(self, _user):
            return None

        async def get_thread(self, thread_id: str):
            return {"id": thread_id, "metadata": {}}

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

    class _FakeSession:
        def to_persistable(self):
            return {"session": "discord"}

        async def delete(self):
            events.append("session_delete")

    class _Bridge(EasierlitDiscordBridge):
        def _init_discord_context(self, *, session, channel, message):
            return SimpleNamespace(session=_FakeSession())

        async def _get_or_create_user(self, _discord_user):
            return SimpleNamespace(metadata={"name": "discord-user"})

    class _FakeMessage:
        def __init__(self, **_kwargs):
            self.content = _kwargs.get("content", "")

        async def send(self):
            events.append("message_send")
            return self

    class _FakeTyping:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeChannel:
        def typing(self):
            return _FakeTyping()

    async def _fake_download_discord_files(_session, _attachments):
        return []

    async def _fake_on_message(_msg):
        events.append("on_message")
        raise RuntimeError("boom")

    fake_data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake_data_layer)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake_data_layer)
    client = EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread")
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(username="admin", password="admin", identifier="admin"),
    )

    bridge = _Bridge(
        runtime=runtime,
        bot_token="token",
        data_layer_getter=lambda: fake_data_layer,
        client=_FakeDiscordClient(),
    )

    previous_on_chat_start = config.code.on_chat_start
    previous_on_message = config.code.on_message
    previous_on_chat_end = config.code.on_chat_end
    config.code.on_chat_start = None
    config.code.on_message = _fake_on_message
    config.code.on_chat_end = None

    message = SimpleNamespace(
        author=SimpleNamespace(id=321, name="discord-user"),
        attachments=[],
        content="hello",
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(
                _swap_attr(bridge_module, "download_discord_files", _fake_download_discord_files)
            )
            stack.enter_context(_swap_attr(bridge_module, "Message", _FakeMessage))
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(
                    bridge._process_discord_message(
                        message=message,
                        thread_id="thread-1",
                        thread_name="Thread Name",
                        channel=_FakeChannel(),
                        bind_thread_to_user=False,
                    )
                )
    finally:
        config.code.on_chat_start = previous_on_chat_start
        config.code.on_message = previous_on_message
        config.code.on_chat_end = previous_on_chat_end

    assert len(fake_data_layer.updated_threads) == 2
    first_updated = fake_data_layer.updated_threads[0]
    second_updated = fake_data_layer.updated_threads[1]
    assert first_updated["thread_id"] == "thread-1"
    assert first_updated["name"] == "Thread Name"
    assert first_updated["user_id"] == "user-admin"
    assert first_updated["metadata"]["session"] == "discord"
    assert first_updated["metadata"]["easierlit_discord_owner_id"] == "321"
    assert second_updated["thread_id"] == "thread-1"
    assert second_updated["name"] == "Thread Name"
    assert second_updated["user_id"] == "user-admin"
    assert second_updated["metadata"]["easierlit_discord_owner_id"] == "321"
    assert events[0] == "upsert"


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
