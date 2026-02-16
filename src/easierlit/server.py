from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from .app import EasierlitApp
from .client import EasierlitClient
from .jwt_secret import ensure_jwt_secret
from .runtime import get_runtime
from .settings import EasierlitAuthConfig, EasierlitPersistenceConfig

LOGGER = logging.getLogger(__name__)


class EasierlitServer:
    def __init__(
        self,
        client: EasierlitClient,
        host: str = "127.0.0.1",
        port: int = 8000,
        root_path: str = "",
        auth: EasierlitAuthConfig | None = None,
        persistence: EasierlitPersistenceConfig | None = None,
    ):
        self.client = client
        self.host = host
        self.port = port
        self.root_path = root_path
        self.auth = auth
        self.persistence = persistence or EasierlitPersistenceConfig()

    def serve(self) -> None:
        from chainlit.cli import run_chainlit
        from chainlit.config import config

        app = EasierlitApp()
        runtime = get_runtime()
        shutdown_requested = threading.Event()

        runtime.bind(
            client=self.client,
            app=app,
            auth=self.auth,
            persistence=self.persistence,
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
                os.kill(pid, signal.SIGINT)
            except Exception:
                LOGGER.exception("Failed to send SIGINT to stop server; retrying with SIGTERM.")
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    LOGGER.exception("Failed to send SIGTERM after SIGINT failure.")

        self.client.set_worker_crash_handler(_handle_worker_crash)
        self.client.run(app)

        os.environ["CHAINLIT_HOST"] = self.host
        os.environ["CHAINLIT_PORT"] = str(self.port)
        os.environ["CHAINLIT_ROOT_PATH"] = self.root_path
        os.environ["CHAINLIT_AUTH_COOKIE_NAME"] = "easierlit_access_token"
        os.environ["CHAINLIT_AUTH_SECRET"] = ensure_jwt_secret()

        # Easierlit policy: always run headless, always keep sidebar open, and show full CoT.
        config.run.headless = True
        config.ui.default_sidebar_state = "open"
        config.ui.cot = "full"

        entrypoint = self._entrypoint_path()

        try:
            run_chainlit(str(entrypoint))
        finally:
            self.client.set_worker_crash_handler(None)
            self.client.stop()
            runtime.unbind()

    def _entrypoint_path(self) -> Path:
        return Path(__file__).resolve().parent / "chainlit_entry.py"
