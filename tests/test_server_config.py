import importlib
import os
import signal

from chainlit.config import config

from easierlit import (
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
)
from easierlit.runtime import get_runtime


def test_serve_forces_headless_and_sidebar(monkeypatch):
    called = {"count": 0, "target": None}
    generated_secret = "s" * 64

    def fake_run_chainlit(target: str):
        called["count"] += 1
        called["target"] = target
        assert config.run.headless is True
        assert config.ui.default_sidebar_state == "open"
        assert config.ui.cot == "full"

        runtime = get_runtime()
        assert runtime.get_auth() is not None
        assert runtime.get_persistence() is not None
        assert runtime.get_persistence().sqlite_path == ".chainlit/test.db"

    chainlit_cli = importlib.import_module("chainlit.cli")
    monkeypatch.setattr(chainlit_cli, "run_chainlit", fake_run_chainlit)
    monkeypatch.setattr("easierlit.server.ensure_jwt_secret", lambda: generated_secret)

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
    )
    persistence = EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/test.db")
    server = EasierlitServer(
        client=client,
        host="0.0.0.0",
        port=9000,
        root_path="/custom",
        auth=auth,
        persistence=persistence,
    )
    server.serve()

    assert called["count"] == 1
    assert called["target"].endswith("chainlit_entry.py")
    assert os.environ["CHAINLIT_HOST"] == "0.0.0.0"
    assert os.environ["CHAINLIT_PORT"] == "9000"
    assert os.environ["CHAINLIT_ROOT_PATH"] == "/custom"
    assert os.environ["CHAINLIT_AUTH_COOKIE_NAME"] == "easierlit_access_token"
    assert os.environ["CHAINLIT_AUTH_SECRET"] == generated_secret
    assert len(os.environ["CHAINLIT_AUTH_SECRET"].encode("utf-8")) >= 32


def test_runtime_is_unbound_after_serve(monkeypatch):
    chainlit_cli = importlib.import_module("chainlit.cli")
    monkeypatch.setattr(chainlit_cli, "run_chainlit", lambda _target: None)
    monkeypatch.setattr("easierlit.server.ensure_jwt_secret", lambda: "x" * 64)

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(client=client)
    server.serve()

    runtime = get_runtime()
    assert runtime.get_client() is None
    assert runtime.get_app() is None
    assert runtime.get_auth() is None
    assert runtime.get_persistence() is None


def test_worker_crash_triggers_single_sigint(monkeypatch):
    kill_calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int):
        kill_calls.append((pid, sig))

    chainlit_cli = importlib.import_module("chainlit.cli")
    monkeypatch.setattr(chainlit_cli, "run_chainlit", lambda _target: None)
    monkeypatch.setattr("easierlit.server.ensure_jwt_secret", lambda: "x" * 64)
    monkeypatch.setattr(os, "kill", fake_kill)

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(client=client)

    def fake_run_chainlit(_target: str):
        handler = client._worker_crash_handler
        assert handler is not None
        handler("Traceback (most recent call last):\nRuntimeError: first")
        handler("Traceback (most recent call last):\nRuntimeError: second")

    monkeypatch.setattr(chainlit_cli, "run_chainlit", fake_run_chainlit)
    server.serve()

    assert len(kill_calls) == 1
    assert kill_calls[0][0] == os.getpid()
    assert kill_calls[0][1] == signal.SIGINT


def test_worker_crash_falls_back_to_sigterm(monkeypatch):
    kill_calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int):
        kill_calls.append((pid, sig))
        if sig == signal.SIGINT:
            raise OSError("SIGINT failed")

    chainlit_cli = importlib.import_module("chainlit.cli")
    monkeypatch.setattr(chainlit_cli, "run_chainlit", lambda _target: None)
    monkeypatch.setattr("easierlit.server.ensure_jwt_secret", lambda: "x" * 64)
    monkeypatch.setattr(os, "kill", fake_kill)

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(client=client)

    def fake_run_chainlit(_target: str):
        handler = client._worker_crash_handler
        assert handler is not None
        handler("Traceback (most recent call last):\nRuntimeError: fail")

    monkeypatch.setattr(chainlit_cli, "run_chainlit", fake_run_chainlit)
    server.serve()

    assert len(kill_calls) == 2
    assert kill_calls[0][1] == signal.SIGINT
    assert kill_calls[1][1] == signal.SIGTERM
