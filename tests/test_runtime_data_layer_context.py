import asyncio

from easierlit.models import OutgoingCommand
from easierlit.runtime import RuntimeRegistry


class _FakeDataLayer:
    def __init__(self):
        self.created_steps = []

    async def create_step(self, step_dict):
        self.created_steps.append(step_dict)


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
