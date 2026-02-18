import asyncio
from types import SimpleNamespace

from easierlit.models import OutgoingCommand
from easierlit.runtime import RuntimeRegistry


class _FakeDataLayer:
    def __init__(self):
        self.created_steps = []
        self.updated_steps = []
        self.created_elements = []

    async def create_step(self, step_dict):
        self.created_steps.append(step_dict)

    async def update_step(self, step_dict):
        self.updated_steps.append(step_dict)

    async def create_element(self, element):
        self.created_elements.append(element)


def test_apply_outgoing_command_initializes_http_context():
    data_layer = _FakeDataLayer()
    context_calls = []

    def fake_init_http_context(*, thread_id: str, client_type: str):
        context_calls.append((thread_id, client_type))

    runtime = RuntimeRegistry(
        data_layer_getter=lambda: data_layer,
        init_http_context_fn=fake_init_http_context,
    )

    command = OutgoingCommand(
        command="add_message",
        thread_id="thread-ctx",
        message_id="msg-ctx",
        content="hello",
        author="Bot",
        metadata={},
    )

    asyncio.run(runtime.apply_outgoing_command(command))

    assert context_calls == [("thread-ctx", "webapp")]
    assert data_layer.created_steps[0]["id"] == "msg-ctx"


def test_apply_outgoing_command_persists_elements_when_supported():
    data_layer = _FakeDataLayer()
    runtime = RuntimeRegistry(
        data_layer_getter=lambda: data_layer,
        init_http_context_fn=lambda **_kwargs: None,
    )

    add_element = SimpleNamespace(for_id=None, thread_id=None)
    add_command = OutgoingCommand(
        command="add_message",
        thread_id="thread-ctx",
        message_id="msg-ctx",
        content="hello",
        author="Bot",
        elements=[add_element],
        metadata={},
    )
    asyncio.run(runtime.apply_outgoing_command(add_command))

    update_element = SimpleNamespace(for_id=None, thread_id=None)
    update_command = OutgoingCommand(
        command="update_message",
        thread_id="thread-ctx",
        message_id="msg-ctx",
        content="updated",
        author="Bot",
        elements=[update_element],
        metadata={},
    )
    asyncio.run(runtime.apply_outgoing_command(update_command))

    assert [item["id"] for item in data_layer.created_steps] == ["msg-ctx"]
    assert [item["id"] for item in data_layer.updated_steps] == ["msg-ctx"]
    assert len(data_layer.created_elements) == 2
    assert add_element.for_id == "msg-ctx"
    assert add_element.thread_id == "thread-ctx"
    assert update_element.for_id == "msg-ctx"
    assert update_element.thread_id == "thread-ctx"
