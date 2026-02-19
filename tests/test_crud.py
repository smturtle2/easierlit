from types import SimpleNamespace
from uuid import uuid4

import pytest
from chainlit.context import context_var
from chainlit.data.utils import queue_until_user_message
from chainlit.types import PageInfo, PaginatedResponse

from easierlit import (
    DataPersistenceNotEnabledError,
    EasierlitApp,
    EasierlitAuthConfig,
    EasierlitClient,
    IncomingMessage,
    ThreadSessionNotActiveError,
)
from easierlit.runtime import RuntimeRegistry, get_runtime


class _FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class FakeDataLayer:
    def __init__(self, users: dict[str, str] | None = None):
        self.created_steps = []
        self.updated_steps = []
        self.deleted_steps = []
        self.updated_threads = []
        self.deleted_threads = []
        self.requested_threads = []
        self.created_users = []
        self._users = users or {"known-user": "user-1"}
        self._threads = {"thread-1"}

    async def get_user(self, identifier: str):
        user_id = self._users.get(identifier)
        if user_id is None:
            return None
        return _FakeUser(user_id)

    async def create_user(self, user):
        user_id = f"user-created-{len(self.created_users) + 1}"
        self.created_users.append(
            {
                "id": user_id,
                "identifier": user.identifier,
                "metadata": user.metadata,
            }
        )
        self._users[user.identifier] = user_id
        return _FakeUser(user_id)

    async def list_threads(self, pagination, filters):
        return PaginatedResponse(
            pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None),
            data=[
                {
                    "id": "thread-1",
                    "createdAt": "2024-01-01T00:00:00.000Z",
                    "name": "Thread 1",
                    "userId": "user-1",
                    "userIdentifier": "known-user",
                    "metadata": {},
                    "steps": [],
                    "elements": [],
                    "tags": [],
                }
            ],
        )

    async def get_thread(self, thread_id: str):
        self.requested_threads.append(thread_id)
        if thread_id not in self._threads:
            return None
        return {"id": thread_id, "name": "Thread 1", "tags": ["existing-tag"]}

    async def update_thread(
        self,
        thread_id: str,
        name=None,
        user_id=None,
        metadata=None,
        tags=None,
    ):
        self.updated_threads.append(
            {
                "thread_id": thread_id,
                "name": name,
                "user_id": user_id,
                "metadata": metadata,
                "tags": tags,
            }
        )
        self._threads.add(thread_id)

    async def delete_thread(self, thread_id: str):
        self.deleted_threads.append(thread_id)
        self._threads.discard(thread_id)

    async def create_step(self, step_dict):
        self.created_steps.append(step_dict)

    async def update_step(self, step_dict):
        self.updated_steps.append(step_dict)

    async def delete_step(self, step_id: str):
        self.deleted_steps.append(step_id)


class FakeSQLiteDataLayer(FakeDataLayer):
    def __init__(self):
        super().__init__()
        self._conninfo = "sqlite+aiosqlite:///tmp/easierlit-test.db"

    async def list_threads(self, pagination, filters):
        return PaginatedResponse(
            pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None),
            data=[
                {
                    "id": "thread-1",
                    "createdAt": "2024-01-01T00:00:00.000Z",
                    "name": "Thread 1",
                    "userId": "user-1",
                    "userIdentifier": "known-user",
                    "metadata": {},
                    "steps": [],
                    "elements": [],
                    "tags": '["tag-a", "tag-b"]',
                }
            ],
        )

    async def get_thread(self, thread_id: str):
        self.requested_threads.append(thread_id)
        if thread_id != "thread-1":
            return None
        return {"id": thread_id, "name": "Thread 1", "tags": '["tag-a"]'}


class _FakeEngineUrl:
    drivername = "sqlite+aiosqlite"


class _FakeEngine:
    url = _FakeEngineUrl()


class FakeSQLiteEngineOnlyDataLayer(FakeDataLayer):
    def __init__(self):
        super().__init__()
        self.engine = _FakeEngine()


class FakeDecoratedDataLayer(FakeDataLayer):
    @queue_until_user_message()
    async def create_step(self, step_dict):
        await super().create_step(step_dict)

    @queue_until_user_message()
    async def update_step(self, step_dict):
        await super().update_step(step_dict)

    @queue_until_user_message()
    async def delete_step(self, step_id: str):
        await super().delete_step(step_id)


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime = get_runtime()
    runtime.unbind()
    yield
    runtime.unbind()


def _apply_next_outgoing_command(app: EasierlitApp, runtime: RuntimeRegistry):
    command = app._pop_outgoing(timeout=1.0)
    runtime.run_coroutine_sync(runtime.apply_outgoing_command(command))
    return command


def test_thread_crud_requires_data_layer():
    app = EasierlitApp(data_layer_getter=lambda: None)

    with pytest.raises(DataPersistenceNotEnabledError):
        app.list_threads()


def test_thread_crud_with_data_layer():
    fake = FakeDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)

    threads = app.list_threads(first=5, user_identifier="known-user")
    assert len(threads.data) == 1

    thread = app.get_thread("thread-1")
    assert thread["id"] == "thread-1"
    assert thread["tags"] == ["existing-tag"]

    app.update_thread("thread-1", name="Renamed", metadata={"x": 1}, tags=["tag"])
    assert fake.updated_threads[0]["name"] == "Renamed"
    assert fake.updated_threads[0]["user_id"] is None
    assert fake.updated_threads[0]["tags"] == ["tag"]

    app.delete_thread("thread-1")
    assert fake.deleted_threads == ["thread-1"]


def test_reset_thread_recreates_same_thread_id_and_only_restores_name():
    class _FakeDataLayerForReset(FakeDataLayer):
        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            if thread_id not in self._threads:
                return None
            return {
                "id": thread_id,
                "name": "Reset Name",
                "metadata": {"keep": "no"},
                "tags": ["tag-a"],
                "steps": [
                    {"id": "msg-1", "type": "user_message"},
                    {"id": "msg-2", "type": "assistant_message"},
                    {"id": None, "type": "assistant_message"},
                    "invalid-step",
                ],
            }

    fake = _FakeDataLayerForReset()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake,
        init_http_context_fn=lambda **_kwargs: None,
    )
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake)

    app.reset_thread("thread-1")

    assert fake.deleted_steps == ["msg-1", "msg-2"]
    assert fake.deleted_threads == ["thread-1"]
    assert fake.updated_threads[0]["thread_id"] == "thread-1"
    assert fake.updated_threads[0]["name"] == "Reset Name"
    assert fake.updated_threads[0]["metadata"] is None
    assert fake.updated_threads[0]["tags"] is None


def test_reset_thread_clears_pending_incoming_messages_for_thread():
    fake = FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake,
        init_http_context_fn=lambda **_kwargs: None,
    )
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake)

    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-1",
            session_id="session-1",
            message_id="msg-1",
            content="clear me",
            author="User",
        )
    )
    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-2",
            session_id="session-2",
            message_id="msg-2",
            content="keep me",
            author="User",
        )
    )
    app._enqueue_incoming(
        IncomingMessage(
            thread_id="thread-1",
            session_id="session-3",
            message_id="msg-3",
            content="clear me too",
            author="User",
        )
    )

    app.reset_thread("thread-1")

    received = app.recv(timeout=1.0)
    assert received.thread_id == "thread-2"
    assert received.content == "keep me"

    with pytest.raises(TimeoutError):
        app.recv(timeout=0.05)


def test_reset_thread_raises_for_missing_thread():
    fake = FakeDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)

    with pytest.raises(ValueError, match="missing-thread"):
        app.reset_thread("missing-thread")


def test_reset_thread_requires_data_layer():
    app = EasierlitApp(data_layer_getter=lambda: None)

    with pytest.raises(DataPersistenceNotEnabledError):
        app.reset_thread("thread-1")


def test_get_messages_preserves_supported_order_and_maps_elements():
    class _FakeDataLayerWithSteps(FakeDataLayer):
        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            if thread_id != "thread-1":
                return None
            return {
                "id": thread_id,
                "name": "Thread 1",
                "tags": ["existing-tag"],
                "steps": [
                    {"id": "msg-1", "type": "user_message", "output": "hello"},
                    {"id": "msg-2", "type": "assistant_message", "output": "hi"},
                    {"id": "tool-1", "type": "tool", "output": '{"hits":2}'},
                    {"id": "run-1", "type": "run", "output": "done"},
                    "invalid-entry",
                ],
                "elements": [
                    {
                        "id": "el-1",
                        "type": "image",
                        "forId": "msg-1",
                        "url": "https://example.com/a.png",
                    },
                    {
                        "id": "el-2",
                        "type": "image",
                        "forId": "tool-1",
                        "url": "https://example.com/tool.png",
                    },
                    {
                        "id": "el-3",
                        "type": "image",
                        "forId": "run-1",
                        "url": "https://example.com/run.png",
                    },
                    "invalid-element",
                ],
            }

    fake = _FakeDataLayerWithSteps()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    assert messages_payload["thread"]["id"] == "thread-1"
    assert "steps" not in messages_payload["thread"]
    assert len(messages_payload["thread"]["elements"]) == 4
    assert [item["id"] for item in messages_payload["messages"]] == [
        "msg-1",
        "msg-2",
        "tool-1",
    ]
    assert [element["id"] for element in messages_payload["messages"][0]["elements"]] == ["el-1"]
    assert messages_payload["messages"][1]["elements"] == []
    assert [element["id"] for element in messages_payload["messages"][2]["elements"]] == ["el-2"]
    first_element = messages_payload["messages"][0]["elements"][0]
    assert first_element["has_source"] is True
    assert first_element["source"] == {
        "kind": "url",
        "value": "https://example.com/a.png",
    }


def test_get_messages_handles_missing_steps_key():
    fake = FakeDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    assert messages_payload["thread"]["id"] == "thread-1"
    assert messages_payload["messages"] == []


def test_get_messages_handles_missing_elements_key():
    class _FakeDataLayerWithMessages(FakeDataLayer):
        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            if thread_id != "thread-1":
                return None
            return {
                "id": thread_id,
                "name": "Thread 1",
                "steps": [
                    {"id": "msg-1", "type": "assistant_message", "output": "hi"},
                    {"id": "tool-1", "type": "tool", "output": "{}"},
                    {"id": "run-1", "type": "run", "output": "done"},
                ],
            }

    fake = _FakeDataLayerWithMessages()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    assert [item["id"] for item in messages_payload["messages"]] == ["msg-1", "tool-1"]
    assert messages_payload["messages"][0]["elements"] == []
    assert messages_payload["messages"][1]["elements"] == []


def test_get_messages_maps_elements_with_for_id_aliases_and_non_string_step_ids():
    uuid_step_id = uuid4()

    class _FakeDataLayerWithAliasedElementKeys(FakeDataLayer):
        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            if thread_id != "thread-1":
                return None
            return {
                "id": thread_id,
                "name": "Thread 1",
                "steps": [
                    {"id": 1, "type": "assistant_message", "output": "one"},
                    {"id": uuid_step_id, "type": "tool", "output": "{}"},
                ],
                "elements": [
                    {
                        "id": "el-1",
                        "type": "image",
                        "for_id": 1,
                        "path": "/tmp/one.png",
                    },
                    {
                        "id": "el-2",
                        "type": "image",
                        "stepId": uuid_step_id,
                        "content": b"\x89PNG",
                    },
                ],
            }

    fake = _FakeDataLayerWithAliasedElementKeys()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    assert [item["id"] for item in messages_payload["messages"]] == [1, uuid_step_id]
    first_element = messages_payload["messages"][0]["elements"][0]
    assert first_element["id"] == "el-1"
    assert first_element["has_source"] is True
    assert first_element["source"] == {"kind": "path", "value": "/tmp/one.png"}

    second_element = messages_payload["messages"][1]["elements"][0]
    assert second_element["id"] == "el-2"
    assert second_element["has_source"] is True
    assert second_element["source"] == {"kind": "bytes", "value": {"length": 4}}


def test_get_messages_resolves_url_from_object_key_and_marks_missing_source():
    class _FakeStorageProvider:
        async def get_read_url(self, object_key: str) -> str:
            return f"/public/easierlit/{object_key}"

    class _FakeDataLayerWithObjectKeyOnlyElements(FakeDataLayer):
        def __init__(self):
            super().__init__()
            self.storage_provider = _FakeStorageProvider()

        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            if thread_id != "thread-1":
                return None
            return {
                "id": thread_id,
                "name": "Thread 1",
                "steps": [
                    {"id": "msg-1", "type": "assistant_message", "output": "hi"},
                ],
                "elements": [
                    {
                        "id": "el-object",
                        "type": "image",
                        "forId": "msg-1",
                        "objectKey": "user-1/img.png",
                    },
                    {
                        "id": "el-none",
                        "type": "image",
                        "forId": "msg-1",
                    },
                ],
            }

    fake = _FakeDataLayerWithObjectKeyOnlyElements()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    object_element = messages_payload["messages"][0]["elements"][0]
    assert object_element["url"] == "/public/easierlit/user-1/img.png"
    assert object_element["has_source"] is True
    assert object_element["source"] == {
        "kind": "url",
        "value": "/public/easierlit/user-1/img.png",
    }

    missing_source_element = messages_payload["messages"][0]["elements"][1]
    assert missing_source_element["has_source"] is False
    assert missing_source_element["source"] is None


@pytest.mark.parametrize(
    "file_exists, expected_source_kind",
    [
        (True, "path"),
        (False, "url"),
    ],
)
def test_get_messages_resolves_object_key_source_with_minimal_branches(
    tmp_path, file_exists: bool, expected_source_kind: str
):
    local_file = tmp_path / "images" / "user-1" / "img.png"
    if file_exists:
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_bytes(b"payload")

    class _FakeStorageProvider:
        def resolve_file_path(self, _object_key: str):
            if file_exists:
                return local_file
            return "/tmp/definitely-missing/local-image.png"

        async def get_read_url(self, object_key: str) -> str:
            return f"/easierlit/local/{object_key}"

    class _FakeDataLayerWithObjectKeyElement(FakeDataLayer):
        def __init__(self):
            super().__init__()
            self.storage_provider = _FakeStorageProvider()

        async def get_thread(self, thread_id: str):
            self.requested_threads.append(thread_id)
            return {
                "id": thread_id,
                "name": "Thread 1",
                "steps": [{"id": "msg-1", "type": "assistant_message", "output": "hi"}],
                "elements": [
                    {
                        "id": "el-1",
                        "type": "image",
                        "forId": "msg-1",
                        "objectKey": "user-1/img.png",
                        "url": "/stale/url",
                    }
                ],
            }

    fake = _FakeDataLayerWithObjectKeyElement()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    messages_payload = app.get_messages("thread-1")

    element = messages_payload["messages"][0]["elements"][0]
    assert element["url"] == "/easierlit/local/user-1/img.png"
    assert element["has_source"] is True
    assert element["source"]["kind"] == expected_source_kind
    if file_exists:
        assert element["path"] == str(local_file)
        assert element["source"] == {"kind": "path", "value": str(local_file)}
    else:
        assert element["source"] == {
            "kind": "url",
            "value": "/easierlit/local/user-1/img.png",
        }


def test_new_thread_creates_when_missing():
    fake = FakeDataLayer()
    app = EasierlitApp(
        data_layer_getter=lambda: fake,
        uuid_factory=lambda: "thread-new",
    )

    thread_id = app.new_thread(name="Created", metadata={"x": 1}, tags=["tag"])

    assert thread_id == "thread-new"
    assert fake.updated_threads[0]["thread_id"] == thread_id
    assert fake.updated_threads[0]["name"] == "Created"
    assert fake.updated_threads[0]["metadata"] == {"x": 1}
    assert fake.updated_threads[0]["tags"] == ["tag"]


def test_new_thread_retries_when_generated_id_exists():
    fake = FakeDataLayer()
    fake._threads.add("thread-collision")

    generated_ids = iter(["thread-collision", "thread-created"])
    app = EasierlitApp(
        data_layer_getter=lambda: fake,
        uuid_factory=lambda: next(generated_ids),
    )

    thread_id = app.new_thread(name="Created")

    assert thread_id == "thread-created"
    assert fake.updated_threads[0]["thread_id"] == "thread-created"
    assert fake.requested_threads[:2] == ["thread-collision", "thread-created"]


def test_new_thread_raises_when_unique_id_allocation_fails():
    fake = FakeDataLayer()
    fake._threads.add("thread-duplicate")
    app = EasierlitApp(
        data_layer_getter=lambda: fake,
        uuid_factory=lambda: "thread-duplicate",
    )

    with pytest.raises(RuntimeError, match="Failed to allocate unique thread_id"):
        app.new_thread(name="Duplicate")

    assert len(fake.requested_threads) == 16
    assert fake.updated_threads == []


def test_update_thread_raises_when_thread_missing():
    fake = FakeDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)

    with pytest.raises(ValueError, match="not found"):
        app.update_thread("missing-thread", name="Renamed")


def test_sqlite_update_thread_serializes_tags():
    fake = FakeSQLiteDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    app.update_thread("thread-1", tags=["run-func-created"])

    assert fake.updated_threads[0]["user_id"] is None
    assert fake.updated_threads[0]["tags"] == '["run-func-created"]'


def test_sqlite_update_thread_serializes_tags_with_engine_drivername():
    fake = FakeSQLiteEngineOnlyDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    app.update_thread("thread-1", tags=["run-func-created"])

    assert fake.updated_threads[0]["user_id"] is None
    assert fake.updated_threads[0]["tags"] == '["run-func-created"]'


def test_update_thread_auto_sets_owner_from_auth_existing_user():
    fake = FakeDataLayer(users={"admin": "user-admin"})
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake)
    client = EasierlitClient(run_funcs=[lambda _app: None])
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(
            username="admin",
            password="admin",
            identifier="admin",
            metadata={"role": "admin"},
        ),
    )

    app.update_thread("thread-1", name="Owned")

    assert fake.updated_threads[0]["thread_id"] == "thread-1"
    assert fake.updated_threads[0]["user_id"] == "user-admin"
    assert fake.created_users == []


def test_update_thread_auto_creates_owner_when_missing():
    fake = FakeDataLayer(users={})
    runtime = RuntimeRegistry(data_layer_getter=lambda: fake)
    app = EasierlitApp(runtime=runtime, data_layer_getter=lambda: fake)
    client = EasierlitClient(run_funcs=[lambda _app: None])
    runtime.bind(
        client=client,
        app=app,
        auth=EasierlitAuthConfig(
            username="admin",
            password="admin",
            identifier="admin",
            metadata={"role": "admin"},
        ),
    )

    app.update_thread("thread-1", name="Owned")

    assert fake.created_users[0]["identifier"] == "admin"
    assert fake.created_users[0]["metadata"] == {"role": "admin"}
    assert fake.updated_threads[0]["user_id"] == "user-created-1"


def test_sqlite_get_thread_normalizes_tags():
    fake = FakeSQLiteDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    thread = app.get_thread("thread-1")

    assert thread["tags"] == ["tag-a"]


def test_sqlite_list_threads_normalizes_tags():
    fake = FakeSQLiteDataLayer()
    app = EasierlitApp(data_layer_getter=lambda: fake)
    threads = app.list_threads(first=5)

    assert threads.data[0]["tags"] == ["tag-a", "tag-b"]


def test_message_crud_falls_back_to_data_layer_when_no_session():
    fake = FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake,
        init_http_context_fn=lambda **_kwargs: None,
    )
    app = EasierlitApp(runtime=runtime)

    message_id = app.add_message("thread-1", "hello", author="Bot")
    _apply_next_outgoing_command(app, runtime)
    assert fake.created_steps[0]["id"] == message_id
    assert fake.created_steps[0]["type"] == "assistant_message"
    assert fake.created_steps[0]["name"] == "Bot"

    app.update_message("thread-1", message_id, "updated")
    _apply_next_outgoing_command(app, runtime)
    assert fake.updated_steps[0]["id"] == message_id
    assert fake.updated_steps[0]["type"] == "assistant_message"

    app.delete_message("thread-1", message_id)
    _apply_next_outgoing_command(app, runtime)
    assert fake.deleted_steps == [message_id]


def test_message_crud_raises_without_session_and_data_layer():
    runtime = RuntimeRegistry(data_layer_getter=lambda: None)
    app = EasierlitApp(runtime=runtime)

    app.add_message("thread-unknown", "hello")
    with pytest.raises(ThreadSessionNotActiveError):
        _apply_next_outgoing_command(app, runtime)


def test_message_crud_fallback_works_with_queue_decorated_data_layer():
    fake = FakeDecoratedDataLayer()
    context_calls: list[tuple[str, str]] = []

    def fake_init_http_context(*, thread_id: str, client_type: str):
        context_calls.append((thread_id, client_type))
        context_var.set(SimpleNamespace(session=object()))

    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake,
        init_http_context_fn=fake_init_http_context,
    )
    app = EasierlitApp(runtime=runtime)

    message_id = app.add_message("thread-queue", "hello", author="Bot")
    _apply_next_outgoing_command(app, runtime)

    app.update_message("thread-queue", message_id, "updated")
    _apply_next_outgoing_command(app, runtime)

    app.delete_message("thread-queue", message_id)
    _apply_next_outgoing_command(app, runtime)

    assert len(context_calls) == 3
    assert context_calls[0] == ("thread-queue", "webapp")
    assert fake.created_steps[0]["id"] == message_id
    assert fake.created_steps[0]["type"] == "assistant_message"
    assert fake.updated_steps[0]["id"] == message_id
    assert fake.updated_steps[0]["type"] == "assistant_message"
    assert fake.deleted_steps == [message_id]


def test_tool_and_thought_crud_falls_back_to_data_layer_when_no_session():
    fake = FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: fake,
        init_http_context_fn=lambda **_kwargs: None,
    )
    app = EasierlitApp(runtime=runtime)

    tool_message_id = app.add_tool(
        thread_id="thread-1",
        tool_name="SearchTool",
        content='{"query":"books"}',
    )
    _apply_next_outgoing_command(app, runtime)
    assert fake.created_steps[0]["id"] == tool_message_id
    assert fake.created_steps[0]["type"] == "tool"
    assert fake.created_steps[0]["name"] == "SearchTool"

    app.update_tool(
        thread_id="thread-1",
        message_id=tool_message_id,
        tool_name="SearchTool",
        content='{"results":2}',
    )
    _apply_next_outgoing_command(app, runtime)
    assert fake.updated_steps[0]["id"] == tool_message_id
    assert fake.updated_steps[0]["type"] == "tool"
    assert fake.updated_steps[0]["name"] == "SearchTool"

    thought_message_id = app.add_thought(
        thread_id="thread-1",
        content="Need one more retrieval pass.",
    )
    _apply_next_outgoing_command(app, runtime)
    assert fake.created_steps[1]["id"] == thought_message_id
    assert fake.created_steps[1]["type"] == "tool"
    assert fake.created_steps[1]["name"] == "Reasoning"

    app.update_thought(
        thread_id="thread-1",
        message_id=thought_message_id,
        content="Enough context collected.",
    )
    _apply_next_outgoing_command(app, runtime)
    assert fake.updated_steps[1]["id"] == thought_message_id
    assert fake.updated_steps[1]["type"] == "tool"
    assert fake.updated_steps[1]["name"] == "Reasoning"

    app.delete_message("thread-1", tool_message_id)
    _apply_next_outgoing_command(app, runtime)
    app.delete_message("thread-1", thought_message_id)
    _apply_next_outgoing_command(app, runtime)
    assert fake.deleted_steps == [tool_message_id, thought_message_id]
