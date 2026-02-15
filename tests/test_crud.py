import pytest
from chainlit.data.utils import queue_until_user_message
from chainlit.types import PageInfo, PaginatedResponse

from easierlit import (
    DataPersistenceNotEnabledError,
    EasierlitApp,
    EasierlitAuthConfig,
    EasierlitClient,
    ThreadSessionNotActiveError,
)
from easierlit.runtime import get_runtime


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


def _apply_next_outgoing_command(app: EasierlitApp):
    runtime = get_runtime()
    command = app._pop_outgoing(timeout=1.0)
    runtime.run_coroutine_sync(runtime.apply_outgoing_command(command))
    return command


def test_thread_crud_requires_data_layer(monkeypatch):
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: None)
    app = EasierlitApp()

    with pytest.raises(DataPersistenceNotEnabledError):
        app.list_threads()


def test_thread_crud_with_data_layer(monkeypatch):
    fake = FakeDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
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


def test_new_thread_creates_when_missing(monkeypatch):
    fake = FakeDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)
    monkeypatch.setattr("easierlit.app.uuid4", lambda: "thread-new")

    app = EasierlitApp()
    thread_id = app.new_thread(name="Created", metadata={"x": 1}, tags=["tag"])

    assert thread_id == "thread-new"
    assert fake.updated_threads[0]["thread_id"] == thread_id
    assert fake.updated_threads[0]["name"] == "Created"
    assert fake.updated_threads[0]["metadata"] == {"x": 1}
    assert fake.updated_threads[0]["tags"] == ["tag"]


def test_new_thread_retries_when_generated_id_exists(monkeypatch):
    fake = FakeDataLayer()
    fake._threads.add("thread-collision")
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    generated_ids = iter(["thread-collision", "thread-created"])
    monkeypatch.setattr("easierlit.app.uuid4", lambda: next(generated_ids))

    app = EasierlitApp()
    thread_id = app.new_thread(name="Created")

    assert thread_id == "thread-created"
    assert fake.updated_threads[0]["thread_id"] == "thread-created"
    assert fake.requested_threads[:2] == ["thread-collision", "thread-created"]


def test_new_thread_raises_when_unique_id_allocation_fails(monkeypatch):
    fake = FakeDataLayer()
    fake._threads.add("thread-duplicate")
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)
    monkeypatch.setattr("easierlit.app.uuid4", lambda: "thread-duplicate")

    app = EasierlitApp()
    with pytest.raises(RuntimeError, match="Failed to allocate unique thread_id"):
        app.new_thread(name="Duplicate")

    assert len(fake.requested_threads) == 16
    assert fake.updated_threads == []


def test_update_thread_raises_when_thread_missing(monkeypatch):
    fake = FakeDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
    with pytest.raises(ValueError, match="not found"):
        app.update_thread("missing-thread", name="Renamed")


def test_sqlite_update_thread_serializes_tags(monkeypatch):
    fake = FakeSQLiteDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
    app.update_thread("thread-1", tags=["run-func-created"])

    assert fake.updated_threads[0]["user_id"] is None
    assert fake.updated_threads[0]["tags"] == '["run-func-created"]'


def test_sqlite_update_thread_serializes_tags_with_engine_drivername(monkeypatch):
    fake = FakeSQLiteEngineOnlyDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
    app.update_thread("thread-1", tags=["run-func-created"])

    assert fake.updated_threads[0]["user_id"] is None
    assert fake.updated_threads[0]["tags"] == '["run-func-created"]'


def test_update_thread_auto_sets_owner_from_auth_existing_user(monkeypatch):
    fake = FakeDataLayer(users={"admin": "user-admin"})
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None)
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


def test_update_thread_auto_creates_owner_when_missing(monkeypatch):
    fake = FakeDataLayer(users={})
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(run_func=lambda _app: None)
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


def test_sqlite_get_thread_normalizes_tags(monkeypatch):
    fake = FakeSQLiteDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
    thread = app.get_thread("thread-1")

    assert thread["tags"] == ["tag-a"]


def test_sqlite_list_threads_normalizes_tags(monkeypatch):
    fake = FakeSQLiteDataLayer()
    monkeypatch.setattr("easierlit.app.get_data_layer", lambda: fake)

    app = EasierlitApp()
    threads = app.list_threads(first=5)

    assert threads.data[0]["tags"] == ["tag-a", "tag-b"]


def test_message_crud_falls_back_to_data_layer_when_no_session(monkeypatch):
    fake = FakeDataLayer()
    monkeypatch.setattr("easierlit.runtime.get_data_layer", lambda: fake)

    app = EasierlitApp()
    message_id = app.add_message("thread-1", "hello", author="Bot")
    _apply_next_outgoing_command(app)
    assert fake.created_steps[0]["id"] == message_id

    app.update_message("thread-1", message_id, "updated")
    _apply_next_outgoing_command(app)
    assert fake.updated_steps[0]["id"] == message_id

    app.delete_message("thread-1", message_id)
    _apply_next_outgoing_command(app)
    assert fake.deleted_steps == [message_id]


def test_message_crud_raises_without_session_and_data_layer(monkeypatch):
    monkeypatch.setattr("easierlit.runtime.get_data_layer", lambda: None)
    app = EasierlitApp()

    app.add_message("thread-unknown", "hello")
    with pytest.raises(ThreadSessionNotActiveError):
        _apply_next_outgoing_command(app)


def test_message_crud_fallback_works_with_queue_decorated_data_layer(monkeypatch):
    from types import SimpleNamespace

    from chainlit.context import context_var

    fake = FakeDecoratedDataLayer()
    context_calls: list[tuple[str, str]] = []

    def fake_init_http_context(*, thread_id: str, client_type: str):
        context_calls.append((thread_id, client_type))
        context_var.set(SimpleNamespace(session=object()))

    monkeypatch.setattr("easierlit.runtime.get_data_layer", lambda: fake)
    monkeypatch.setattr("easierlit.runtime.init_http_context", fake_init_http_context)

    app = EasierlitApp()
    message_id = app.add_message("thread-queue", "hello", author="Bot")
    _apply_next_outgoing_command(app)

    app.update_message("thread-queue", message_id, "updated")
    _apply_next_outgoing_command(app)

    app.delete_message("thread-queue", message_id)
    _apply_next_outgoing_command(app)

    assert len(context_calls) == 3
    assert context_calls[0] == ("thread-queue", "webapp")
    assert fake.created_steps[0]["id"] == message_id
    assert fake.updated_steps[0]["id"] == message_id
    assert fake.deleted_steps == [message_id]
