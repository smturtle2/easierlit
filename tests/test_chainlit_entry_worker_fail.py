import asyncio
from types import SimpleNamespace

import pytest

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitClient
from easierlit.errors import AppClosedError, RunFuncExecutionError
from easierlit.runtime import get_runtime


class _FakeUIMessage:
    sent: list[tuple[str, str | None]] = []

    def __init__(self, content: str, author: str | None = None):
        self.content = content
        self.author = author

    async def send(self):
        self.__class__.sent.append((self.content, self.author))
        return self


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch):
    runtime = get_runtime()
    runtime.unbind()
    monkeypatch.setattr(chainlit_entry, "_APP_CLOSED_WARNING_EMITTED", False)
    monkeypatch.setattr(chainlit_entry, "_WORKER_FAILURE_UI_NOTIFIED", False)
    yield
    runtime.unbind()
    monkeypatch.setattr(chainlit_entry, "_APP_CLOSED_WARNING_EMITTED", False)
    monkeypatch.setattr(chainlit_entry, "_WORKER_FAILURE_UI_NOTIFIED", False)


def _patch_chainlit_context(monkeypatch):
    fake_session = SimpleNamespace(thread_id="thread-1", id="session-1")
    monkeypatch.setattr(
        chainlit_entry.cl,
        "context",
        SimpleNamespace(session=fake_session),
    )


def _sample_message():
    return SimpleNamespace(
        id="msg-1",
        content="/help",
        author="User",
        created_at=None,
        metadata=None,
    )


def test_on_message_swallows_closed_app_with_worker_error(monkeypatch, caplog):
    runtime = get_runtime()
    app = EasierlitApp()
    app.close()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)
    client._record_worker_error("Traceback (most recent call last):\nRuntimeError: boom")

    _FakeUIMessage.sent = []
    _patch_chainlit_context(monkeypatch)
    monkeypatch.setattr(chainlit_entry.cl, "Message", _FakeUIMessage)

    caplog.set_level("WARNING")
    asyncio.run(chainlit_entry._on_message(_sample_message()))
    asyncio.run(chainlit_entry._on_message(_sample_message()))

    assert len(_FakeUIMessage.sent) == 1
    assert "Server is shutting down" in _FakeUIMessage.sent[0][0]
    assert "server shutdown in progress" in caplog.text.lower()


def test_on_message_raises_when_closed_without_worker_error(monkeypatch):
    runtime = get_runtime()
    app = EasierlitApp()
    app.close()
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    runtime.bind(client=client, app=app)

    _patch_chainlit_context(monkeypatch)

    with pytest.raises(AppClosedError):
        asyncio.run(chainlit_entry._on_message(_sample_message()))


def test_on_app_shutdown_logs_summary_without_traceback(monkeypatch, caplog):
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

    monkeypatch.setattr(client, "stop", fake_stop)

    caplog.set_level("WARNING")
    asyncio.run(chainlit_entry._on_app_shutdown())

    assert "run_func crash acknowledged during shutdown" in caplog.text
    assert "command '/new' failed: Chainlit context not found" in caplog.text
    assert "Traceback (most recent call last)" not in caplog.text
