import asyncio

import pytest

from easierlit import EasierlitApp, EasierlitClient
from easierlit.runtime import get_runtime


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime = get_runtime()
    runtime.unbind()
    yield
    runtime.unbind()


def test_register_and_unregister_session_mapping():
    runtime = get_runtime()
    runtime.register_session(thread_id="thread-1", session_id="session-1")
    assert runtime.get_session_id_for_thread("thread-1") == "session-1"

    runtime.unregister_session("session-1")
    assert runtime.get_session_id_for_thread("thread-1") is None


def test_dispatcher_consumes_outgoing_queue(monkeypatch):
    runtime = get_runtime()
    app = EasierlitApp()
    client = EasierlitClient(lambda _app: None)
    runtime.bind(client=client, app=app)

    async def scenario():
        runtime.set_main_loop(asyncio.get_running_loop())
        seen = []

        async def fake_apply(command):
            seen.append(command)

        monkeypatch.setattr(runtime, "apply_outgoing_command", fake_apply)

        await runtime.start_dispatcher()
        app.send(thread_id="thread-1", content="hello")
        await asyncio.sleep(0.3)
        await runtime.stop_dispatcher()

        assert len(seen) >= 1
        assert seen[0].command == "send"
        assert seen[0].thread_id == "thread-1"

    asyncio.run(scenario())
