import os
import signal

import pytest
from chainlit.config import config

from easierlit import (
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitDiscordConfig,
    EasierlitPersistenceConfig,
    EasierlitServer,
)
from easierlit.runtime import get_runtime


@pytest.fixture(autouse=True)
def _clear_default_auth_env():
    previous_username = os.environ.get("EASIERLIT_AUTH_USERNAME")
    previous_password = os.environ.get("EASIERLIT_AUTH_PASSWORD")

    os.environ.pop("EASIERLIT_AUTH_USERNAME", None)
    os.environ.pop("EASIERLIT_AUTH_PASSWORD", None)

    yield

    if previous_username is None:
        os.environ.pop("EASIERLIT_AUTH_USERNAME", None)
    else:
        os.environ["EASIERLIT_AUTH_USERNAME"] = previous_username

    if previous_password is None:
        os.environ.pop("EASIERLIT_AUTH_PASSWORD", None)
    else:
        os.environ["EASIERLIT_AUTH_PASSWORD"] = previous_password


def test_serve_forces_headless_and_sidebar():
    called = {"count": 0, "target": None}
    generated_secret = "s" * 64
    fake_env: dict[str, str] = {}

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
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: generated_secret,
        environ=fake_env,
    )
    server.serve()

    assert called["count"] == 1
    assert called["target"].endswith("chainlit_entry.py")
    assert fake_env["CHAINLIT_HOST"] == "0.0.0.0"
    assert fake_env["CHAINLIT_PORT"] == "9000"
    assert fake_env["CHAINLIT_ROOT_PATH"] == "/custom"
    assert fake_env["CHAINLIT_AUTH_COOKIE_NAME"] == "easierlit_access_token"
    assert fake_env["CHAINLIT_AUTH_SECRET"] == generated_secret
    assert len(fake_env["CHAINLIT_AUTH_SECRET"].encode("utf-8")) >= 32


def test_runtime_is_unbound_after_serve():
    fake_env: dict[str, str] = {}
    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        run_chainlit_fn=lambda _target: None,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    runtime = get_runtime()
    assert runtime.get_client() is None
    assert runtime.get_app() is None
    assert runtime.get_auth() is None
    assert runtime.get_persistence() is None
    assert runtime.get_discord_token() is None


def test_default_auth_and_persistence_are_enabled_when_omitted(caplog):
    observed = {"username": None, "password": None, "sqlite_path": None}
    fake_env: dict[str, str] = {}

    def fake_run_chainlit(_target: str):
        runtime = get_runtime()
        auth = runtime.get_auth()
        persistence = runtime.get_persistence()
        assert auth is not None
        assert persistence is not None

        observed["username"] = auth.username
        observed["password"] = auth.password
        observed["sqlite_path"] = persistence.sqlite_path

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    with caplog.at_level("WARNING", logger="easierlit.server"):
        server = EasierlitServer(
            client=client,
            run_chainlit_fn=fake_run_chainlit,
            jwt_secret_provider=lambda: "x" * 64,
            environ=fake_env,
        )
        server.serve()

    assert observed["username"] == "admin"
    assert observed["password"] == "admin"
    assert observed["sqlite_path"] == ".chainlit/easierlit.db"
    assert "Falling back to default credentials" in caplog.text


def test_default_auth_prefers_env_credentials():
    observed = {"username": None, "password": None}
    fake_env: dict[str, str] = {}

    os.environ["EASIERLIT_AUTH_USERNAME"] = "env-admin"
    os.environ["EASIERLIT_AUTH_PASSWORD"] = "env-secret"

    def fake_run_chainlit(_target: str):
        runtime = get_runtime()
        auth = runtime.get_auth()
        assert auth is not None
        observed["username"] = auth.username
        observed["password"] = auth.password

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    assert observed["username"] == "env-admin"
    assert observed["password"] == "env-secret"


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("env-admin", None),
        (None, "env-secret"),
    ],
)
def test_default_auth_requires_both_env_values(username, password):
    if username is None:
        os.environ.pop("EASIERLIT_AUTH_USERNAME", None)
    else:
        os.environ["EASIERLIT_AUTH_USERNAME"] = username

    if password is None:
        os.environ.pop("EASIERLIT_AUTH_PASSWORD", None)
    else:
        os.environ["EASIERLIT_AUTH_PASSWORD"] = password

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    with pytest.raises(ValueError, match="must be set together"):
        EasierlitServer(client=client)


def test_discord_enabled_prefers_config_token():
    observed = {"token": None, "runtime_token": None}
    fake_env: dict[str, str] = {"DISCORD_BOT_TOKEN": "env-token"}

    def fake_run_chainlit(_target: str):
        observed["token"] = fake_env.get("DISCORD_BOT_TOKEN")
        observed["runtime_token"] = get_runtime().get_discord_token()

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        discord=EasierlitDiscordConfig(enabled=True, bot_token="config-token"),
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    assert observed["token"] is None
    assert observed["runtime_token"] == "config-token"
    assert fake_env["DISCORD_BOT_TOKEN"] == "env-token"


def test_discord_config_default_enabled_when_passed():
    observed = {"runtime_token": None}
    fake_env: dict[str, str] = {"DISCORD_BOT_TOKEN": "env-token"}

    def fake_run_chainlit(_target: str):
        observed["runtime_token"] = get_runtime().get_discord_token()

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        discord=EasierlitDiscordConfig(),
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    assert observed["runtime_token"] == "env-token"
    assert fake_env["DISCORD_BOT_TOKEN"] == "env-token"


def test_discord_enabled_falls_back_to_env_token():
    observed = {"runtime_token": None}
    fake_env: dict[str, str] = {"DISCORD_BOT_TOKEN": "env-token"}

    def fake_run_chainlit(_target: str):
        observed["runtime_token"] = get_runtime().get_discord_token()

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        discord=EasierlitDiscordConfig(enabled=True),
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    assert observed["runtime_token"] == "env-token"
    assert fake_env["DISCORD_BOT_TOKEN"] == "env-token"


def test_discord_enabled_without_any_token_raises():
    fake_env: dict[str, str] = {}

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        discord=EasierlitDiscordConfig(enabled=True),
        run_chainlit_fn=lambda _target: None,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )

    with pytest.raises(ValueError, match="Discord integration requires a bot token"):
        server.serve()

    runtime = get_runtime()
    assert runtime.get_client() is None
    assert runtime.get_app() is None
    assert runtime.get_auth() is None
    assert runtime.get_persistence() is None
    assert runtime.get_discord_token() is None


def test_discord_default_is_disabled_even_if_env_exists():
    observed = {"runtime_token": "unset", "env_during_serve": "unset"}
    fake_env: dict[str, str] = {"DISCORD_BOT_TOKEN": "env-token"}

    def fake_run_chainlit(_target: str):
        observed["runtime_token"] = get_runtime().get_discord_token()
        observed["env_during_serve"] = fake_env.get("DISCORD_BOT_TOKEN")

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")
    server = EasierlitServer(
        client=client,
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        environ=fake_env,
    )
    server.serve()

    assert observed["runtime_token"] is None
    assert observed["env_during_serve"] is None
    assert fake_env["DISCORD_BOT_TOKEN"] == "env-token"


def test_worker_crash_triggers_single_sigint():
    kill_calls: list[tuple[int, int]] = []
    fake_env: dict[str, str] = {}

    def fake_kill(pid: int, sig: int):
        kill_calls.append((pid, sig))

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")

    def fake_run_chainlit(_target: str):
        handler = client._worker_crash_handler
        assert handler is not None
        handler("Traceback (most recent call last):\nRuntimeError: first")
        handler("Traceback (most recent call last):\nRuntimeError: second")

    server = EasierlitServer(
        client=client,
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        kill_fn=fake_kill,
        environ=fake_env,
    )
    server.serve()

    assert len(kill_calls) == 1
    assert kill_calls[0][0] == os.getpid()
    assert kill_calls[0][1] == signal.SIGINT


def test_worker_crash_falls_back_to_sigterm():
    kill_calls: list[tuple[int, int]] = []
    fake_env: dict[str, str] = {}

    def fake_kill(pid: int, sig: int):
        kill_calls.append((pid, sig))
        if sig == signal.SIGINT:
            raise OSError("SIGINT failed")

    client = EasierlitClient(run_func=lambda _app: None, worker_mode="thread")

    def fake_run_chainlit(_target: str):
        handler = client._worker_crash_handler
        assert handler is not None
        handler("Traceback (most recent call last):\nRuntimeError: fail")

    server = EasierlitServer(
        client=client,
        run_chainlit_fn=fake_run_chainlit,
        jwt_secret_provider=lambda: "x" * 64,
        kill_fn=fake_kill,
        environ=fake_env,
    )
    server.serve()

    assert len(kill_calls) == 2
    assert kill_calls[0][1] == signal.SIGINT
    assert kill_calls[1][1] == signal.SIGTERM
