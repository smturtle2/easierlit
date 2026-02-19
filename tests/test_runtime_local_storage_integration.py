import asyncio
from pathlib import Path

from easierlit import EasierlitApp, EasierlitClient, EasierlitPersistenceConfig, OutgoingCommand
from easierlit.runtime import RuntimeRegistry
from easierlit.settings import _resolve_local_storage_provider


class _FakeSQLAlchemyLikeDataLayer:
    def __init__(self):
        self.created_steps = []
        self.updated_steps = []
        self.deleted_steps = []
        self.element_rows = []

    async def create_step(self, step_dict):
        self.created_steps.append(step_dict)

    async def update_step(self, step_dict):
        self.updated_steps.append(step_dict)

    async def delete_step(self, step_id: str):
        self.deleted_steps.append(step_id)

    async def execute_sql(self, query: str, parameters=None):
        if query.lstrip().startswith("INSERT INTO elements"):
            self.element_rows.append(dict(parameters or {}))
        return []


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _build_runtime(tmp_path: Path, data_layer: _FakeSQLAlchemyLikeDataLayer) -> RuntimeRegistry:
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: data_layer,
        init_http_context_fn=lambda **_kwargs: None,
    )
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=str(tmp_path / "runtime-test.db"),
        local_storage_dir=tmp_path / "images",
    )
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None], worker_mode="thread"),
        app=EasierlitApp(runtime=runtime),
        persistence=persistence,
    )
    return runtime


def test_apply_outgoing_command_prepersists_elements_to_local_storage(tmp_path):
    data_layer = _FakeSQLAlchemyLikeDataLayer()
    runtime = _build_runtime(tmp_path, data_layer)

    source_file = tmp_path / "input" / "random.jpg"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_payload = b"binary-image-payload"
    source_file.write_bytes(source_payload)

    files_dir = Path.cwd() / ".files"
    before_count = _count_files(files_dir)

    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello",
        author="Assistant",
        elements=[
            {
                "id": "el-1",
                "type": "image",
                "name": "random.jpg",
                "path": str(source_file),
            }
        ],
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert len(data_layer.created_steps) == 1
    assert len(data_layer.element_rows) == 1

    element_row = data_layer.element_rows[0]
    object_key = element_row["objectKey"]
    assert element_row["url"] == f"/easierlit/local/{object_key}"

    storage_provider = _resolve_local_storage_provider(runtime.get_persistence())  # type: ignore[arg-type]
    persisted_path = storage_provider.base_dir / object_key
    assert persisted_path.is_file()
    assert persisted_path.read_bytes() == source_payload

    after_count = _count_files(files_dir)
    assert after_count == before_count


def test_existing_object_key_persists_file_into_local_storage_when_missing(tmp_path):
    data_layer = _FakeSQLAlchemyLikeDataLayer()
    runtime = _build_runtime(tmp_path, data_layer)

    source_file = tmp_path / ".files" / "uploads" / "random.jpg"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_payload = b"uploaded-image-payload"
    source_file.write_bytes(source_payload)

    object_key = (
        "bc3bce99-1c12-4383-8480-4682f76d09bc/"
        "829c0de1-2fe7-4e0d-8e96-ff2d3c62e373/random.jpg"
    )
    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello",
        author="Assistant",
        elements=[
            {
                "id": "el-1",
                "type": "image",
                "name": "random.jpg",
                "objectKey": object_key,
                "url": "/stale/.files/uploads/random.jpg",
                "path": str(source_file),
            }
        ],
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert len(data_layer.element_rows) == 1
    element_row = data_layer.element_rows[0]
    assert element_row["objectKey"] == object_key
    assert element_row["url"] == f"/easierlit/local/{object_key}"

    storage_provider = _resolve_local_storage_provider(runtime.get_persistence())  # type: ignore[arg-type]
    persisted_path = storage_provider.base_dir / object_key
    assert persisted_path.is_file()
    assert persisted_path.read_bytes() == source_payload


def test_existing_object_key_is_preserved_when_local_recovery_fails(tmp_path):
    data_layer = _FakeSQLAlchemyLikeDataLayer()
    runtime = _build_runtime(tmp_path, data_layer)

    object_key = "thread-1/msg-1/el-1/random.jpg"
    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-1",
        message_id="msg-1",
        content="hello",
        author="Assistant",
        elements=[
            {
                "id": "el-1",
                "type": "image",
                "name": "random.jpg",
                "objectKey": object_key,
                "url": "file:///cannot-download-random.jpg",
            }
        ],
    )
    asyncio.run(runtime.apply_outgoing_command(command))

    assert len(data_layer.element_rows) == 1
    element_row = data_layer.element_rows[0]
    assert element_row["objectKey"] == object_key
    assert "url" not in element_row


def test_realtime_and_sessionless_paths_share_same_element_reference(tmp_path):
    data_layer = _FakeSQLAlchemyLikeDataLayer()
    runtime = _build_runtime(tmp_path, data_layer)

    source_file = tmp_path / "input" / "same.jpg"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"same-payload")

    base_elements = [
        {
            "id": "el-fixed",
            "type": "image",
            "name": "same.jpg",
            "path": str(source_file),
        }
    ]

    command_without_session = OutgoingCommand(
        command="add_message",
        thread_id="thread-fixed",
        message_id="msg-fixed",
        content="first",
        author="Assistant",
        elements=base_elements,
    )
    asyncio.run(runtime.apply_outgoing_command(command_without_session))
    sessionless_element = data_layer.element_rows[-1]

    captured_realtime_commands = []

    async def _fake_apply_realtime(_session, command: OutgoingCommand):
        captured_realtime_commands.append(command)

    runtime._resolve_session = lambda _thread_id: object()  # type: ignore[method-assign]
    runtime._apply_realtime_command = _fake_apply_realtime  # type: ignore[method-assign]

    command_with_session = OutgoingCommand(
        command="add_message",
        thread_id="thread-fixed",
        message_id="msg-fixed",
        content="second",
        author="Assistant",
        elements=base_elements,
    )
    asyncio.run(runtime.apply_outgoing_command(command_with_session))
    realtime_element = data_layer.element_rows[-1]

    assert sessionless_element["objectKey"] == realtime_element["objectKey"]
    assert sessionless_element["url"] == realtime_element["url"]

    assert len(captured_realtime_commands) == 1
    realtime_command_element = captured_realtime_commands[0].elements[0]
    assert isinstance(realtime_command_element, dict)
    assert "path" not in realtime_command_element
    assert "content" not in realtime_command_element
    assert realtime_command_element["objectKey"] == sessionless_element["objectKey"]
    assert realtime_command_element["url"] == sessionless_element["url"]
