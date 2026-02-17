from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class EasierlitAuthConfig:
    username: str
    password: str
    identifier: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.username or not self.username.strip():
            raise ValueError("EasierlitAuthConfig.username must not be empty.")
        if not self.password or not self.password.strip():
            raise ValueError("EasierlitAuthConfig.password must not be empty.")


@dataclass(slots=True)
class EasierlitPersistenceConfig:
    enabled: bool = True
    sqlite_path: str = ".chainlit/easierlit.db"


@dataclass(slots=True)
class EasierlitDiscordConfig:
    enabled: bool = True
    bot_token: str | None = None

    def __post_init__(self) -> None:
        if self.bot_token is not None and not self.bot_token.strip():
            raise ValueError("EasierlitDiscordConfig.bot_token must not be empty.")
