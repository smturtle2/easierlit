from __future__ import annotations

import hashlib
import logging
import os
import signal
import threading
from collections.abc import Callable, MutableMapping
from pathlib import Path

from .app import EasierlitApp
from .client import EasierlitClient
from .jwt_secret import ensure_jwt_secret
from .runtime import get_runtime
from .settings import EasierlitAuthConfig, EasierlitDiscordConfig, EasierlitPersistenceConfig

LOGGER = logging.getLogger(__name__)
_DEFAULT_AUTH_USERNAME = "admin"
_DEFAULT_AUTH_PASSWORD = "admin"
_AUTH_USERNAME_ENV = "EASIERLIT_AUTH_USERNAME"
_AUTH_PASSWORD_ENV = "EASIERLIT_AUTH_PASSWORD"
_CHAINLIT_AUTH_COOKIE_NAME_ENV = "CHAINLIT_AUTH_COOKIE_NAME"
_CHAINLIT_AUTH_SECRET_ENV = "CHAINLIT_AUTH_SECRET"


class EasierlitServer:
    def __init__(
        self,
        client: EasierlitClient,
        host: str = "127.0.0.1",
        port: int = 8000,
        root_path: str = "",
        auth: EasierlitAuthConfig | None = None,
        persistence: EasierlitPersistenceConfig | None = None,
        discord: EasierlitDiscordConfig | None = None,
        run_chainlit_fn: Callable[[str], None] | None = None,
        jwt_secret_provider: Callable[[], str] = ensure_jwt_secret,
        kill_fn: Callable[[int, int], None] = os.kill,
        environ: MutableMapping[str, str] | None = None,
    ):
        self.client = client
        self.host = host
        self.port = port
        self.root_path = root_path
        self.auth = self._resolve_auth(auth)
        self.persistence = (
            persistence if persistence is not None else EasierlitPersistenceConfig()
        )
        self.discord = discord if discord is not None else EasierlitDiscordConfig(enabled=False)

        self._run_chainlit_fn = run_chainlit_fn or self._default_run_chainlit
        self._jwt_secret_provider = jwt_secret_provider
        self._kill_fn = kill_fn
        self._environ = environ if environ is not None else os.environ

    def serve(self) -> None:
        from chainlit.config import config

        runtime = get_runtime()
        app = EasierlitApp(runtime=runtime)
        shutdown_requested = threading.Event()

        previous_discord_token = self._environ.get("DISCORD_BOT_TOKEN")
        previous_auth_cookie_name = self._environ.get(_CHAINLIT_AUTH_COOKIE_NAME_ENV)
        previous_auth_secret = self._environ.get(_CHAINLIT_AUTH_SECRET_ENV)
        resolved_discord_token = self._resolve_discord_token(previous_discord_token)

        runtime.bind(
            client=self.client,
            app=app,
            auth=self.auth,
            persistence=self.persistence,
            discord_token=resolved_discord_token,
        )

        def _handle_worker_crash(traceback_text: str) -> None:
            summary = "Unknown run_func error"
            lines = traceback_text.strip().splitlines()
            if lines:
                summary = lines[-1]

            LOGGER.error("run_func crashed: %s", summary)
            LOGGER.error("run_func traceback:\n%s", traceback_text)

            if shutdown_requested.is_set():
                return
            shutdown_requested.set()

            pid = os.getpid()
            try:
                self._kill_fn(pid, signal.SIGINT)
            except Exception:
                LOGGER.exception("Failed to send SIGINT to stop server; retrying with SIGTERM.")
                try:
                    self._kill_fn(pid, signal.SIGTERM)
                except Exception:
                    LOGGER.exception("Failed to send SIGTERM after SIGINT failure.")

        self.client.set_worker_crash_handler(_handle_worker_crash)
        self.client.run(app)

        try:
            # Disable Chainlit built-in Discord bootstrapping. Easierlit runs its
            # own bridge through runtime.get_discord_token().
            self._environ.pop("DISCORD_BOT_TOKEN", None)

            self._environ["CHAINLIT_HOST"] = self.host
            self._environ["CHAINLIT_PORT"] = str(self.port)
            self._environ["CHAINLIT_ROOT_PATH"] = self.root_path
            self._environ[_CHAINLIT_AUTH_COOKIE_NAME_ENV] = self._resolve_chainlit_auth_cookie_name(
                previous_auth_cookie_name
            )
            self._environ[_CHAINLIT_AUTH_SECRET_ENV] = self._resolve_chainlit_auth_secret(
                previous_auth_secret
            )

            # Easierlit policy: always run headless, always keep sidebar open, and show full CoT.
            config.run.headless = True
            config.ui.default_sidebar_state = "open"
            config.ui.cot = "full"

            entrypoint = self._entrypoint_path()
            self._run_chainlit_fn(str(entrypoint))
        finally:
            self._restore_env_var("DISCORD_BOT_TOKEN", previous_discord_token)
            self._restore_env_var(_CHAINLIT_AUTH_COOKIE_NAME_ENV, previous_auth_cookie_name)
            self._restore_env_var(_CHAINLIT_AUTH_SECRET_ENV, previous_auth_secret)

            self.client.set_worker_crash_handler(None)
            self.client.stop()
            runtime.unbind()

    @staticmethod
    def _default_run_chainlit(target: str) -> None:
        from chainlit.cli import run_chainlit

        run_chainlit(target)

    def _entrypoint_path(self) -> Path:
        return Path(__file__).resolve().parent / "chainlit_entry.py"

    def _resolve_chainlit_auth_cookie_name(self, current_value: str | None) -> str:
        if current_value is not None and current_value.strip():
            return current_value

        scope_text = "|".join(
            [
                str(Path.cwd().resolve()),
                str(self.host),
                str(self.port),
                str(self.root_path),
            ]
        )
        scope_hash = hashlib.sha256(scope_text.encode("utf-8")).hexdigest()[:16]
        return f"easierlit_access_token_{scope_hash}"

    def _resolve_chainlit_auth_secret(self, current_value: str | None) -> str:
        if current_value is not None and current_value.strip():
            return current_value
        return self._jwt_secret_provider()

    def _restore_env_var(self, key: str, previous_value: str | None) -> None:
        if previous_value is None:
            self._environ.pop(key, None)
            return
        self._environ[key] = previous_value

    def _resolve_auth(self, auth: EasierlitAuthConfig | None) -> EasierlitAuthConfig:
        if auth is not None:
            return auth

        env_username = os.getenv(_AUTH_USERNAME_ENV)
        env_password = os.getenv(_AUTH_PASSWORD_ENV)
        if (env_username is None) != (env_password is None):
            raise ValueError(
                f"{_AUTH_USERNAME_ENV} and {_AUTH_PASSWORD_ENV} must be set together."
            )

        if env_username is not None and env_password is not None:
            return EasierlitAuthConfig(username=env_username, password=env_password)

        LOGGER.warning(
            "auth=None detected. Falling back to default credentials "
            "'%s/%s'. Set %s/%s or pass auth=EasierlitAuthConfig(...) for production.",
            _DEFAULT_AUTH_USERNAME,
            _DEFAULT_AUTH_PASSWORD,
            _AUTH_USERNAME_ENV,
            _AUTH_PASSWORD_ENV,
        )
        return EasierlitAuthConfig(
            username=_DEFAULT_AUTH_USERNAME,
            password=_DEFAULT_AUTH_PASSWORD,
        )

    def _resolve_discord_token(self, current_env_token: str | None) -> str | None:
        if not self.discord.enabled:
            return None

        resolved = self.discord.bot_token
        if resolved is None:
            resolved = current_env_token

        if resolved is None or not resolved.strip():
            raise ValueError(
                "Discord integration requires a bot token. Set "
                "EasierlitDiscordConfig(bot_token=...) or DISCORD_BOT_TOKEN."
            )

        return resolved
