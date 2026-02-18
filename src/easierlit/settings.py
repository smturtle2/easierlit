from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .storage.local import LocalFileStorageClient

if TYPE_CHECKING:
    from chainlit.data.storage_clients.base import BaseStorageClient

LOGGER = logging.getLogger(__name__)


def _default_local_storage_provider() -> LocalFileStorageClient:
    return ensure_local_storage_provider(LocalFileStorageClient())


def ensure_local_storage_provider(storage_provider: BaseStorageClient | Any | None) -> LocalFileStorageClient:
    if storage_provider is None:
        raise ValueError(
            "EasierlitPersistenceConfig.storage_provider must be a LocalFileStorageClient."
        )

    if not isinstance(storage_provider, LocalFileStorageClient):
        raise TypeError(
            "EasierlitPersistenceConfig.storage_provider must be an instance of "
            "easierlit.storage.local.LocalFileStorageClient."
        )

    return storage_provider


async def assert_local_storage_operational(
    storage_provider: BaseStorageClient | Any | None,
) -> None:
    provider = ensure_local_storage_provider(storage_provider)
    probe_key = f"easierlit-preflight/{uuid4().hex}.txt"
    uploaded_file = await provider.upload_file(
        probe_key,
        b"easierlit-local-preflight",
        mime="text/plain",
        overwrite=True,
    )
    if not isinstance(uploaded_file, dict):
        raise RuntimeError(
            "Local storage preflight failed: upload_file must return a dict with object_key and url."
        )

    uploaded_key = uploaded_file.get("object_key")
    uploaded_url = uploaded_file.get("url")
    if not isinstance(uploaded_key, str) or not uploaded_key.strip():
        raise RuntimeError("Local storage preflight failed: missing object_key in upload response.")
    if not isinstance(uploaded_url, str) or not uploaded_url.strip():
        raise RuntimeError("Local storage preflight failed: missing url in upload response.")

    read_url = await provider.get_read_url(uploaded_key)
    if not isinstance(read_url, str) or not read_url.strip():
        raise RuntimeError("Local storage preflight failed: get_read_url returned empty url.")

    try:
        deleted = await provider.delete_file(uploaded_key)
    except Exception as exc:
        raise RuntimeError(
            "Local storage preflight failed: uploaded probe file could not be deleted."
        ) from exc

    if deleted is False:
        LOGGER.warning(
            "Local storage preflight probe deletion returned False for key '%s'.",
            uploaded_key,
        )


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
    storage_provider: BaseStorageClient | Any = field(
        default_factory=_default_local_storage_provider
    )

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        self.storage_provider = ensure_local_storage_provider(self.storage_provider)


@dataclass(slots=True)
class EasierlitDiscordConfig:
    enabled: bool = True
    bot_token: str | None = None

    def __post_init__(self) -> None:
        if self.bot_token is not None and not self.bot_token.strip():
            raise ValueError("EasierlitDiscordConfig.bot_token must not be empty.")
