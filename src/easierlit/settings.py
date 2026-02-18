from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from chainlit.data.storage_clients.base import BaseStorageClient

LOGGER = logging.getLogger(__name__)

_S3_BUCKET_ENV_KEYS = ("EASIERLIT_S3_BUCKET", "BUCKET_NAME")
_S3_REGION_ENV_KEYS = ("APP_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")
_S3_ACCESS_KEY_ENV_KEYS = ("APP_AWS_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
_S3_SECRET_KEY_ENV_KEYS = ("APP_AWS_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
_S3_SESSION_TOKEN_ENV_KEYS = ("APP_AWS_SESSION_TOKEN", "AWS_SESSION_TOKEN")
_S3_ENDPOINT_ENV_KEYS = ("APP_AWS_ENDPOINT_URL", "DEV_AWS_ENDPOINT")
_DEFAULT_S3_BUCKET = "easierlit-default"


def _first_non_empty_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value is not None and value.strip():
            return value.strip()
    return None


def _default_storage_provider() -> Any:
    bucket = _first_non_empty_env(*_S3_BUCKET_ENV_KEYS) or _DEFAULT_S3_BUCKET

    s3_kwargs: dict[str, Any] = {}

    region = _first_non_empty_env(*_S3_REGION_ENV_KEYS)
    if region is not None:
        s3_kwargs["region_name"] = region

    access_key = _first_non_empty_env(*_S3_ACCESS_KEY_ENV_KEYS)
    if access_key is not None:
        s3_kwargs["aws_access_key_id"] = access_key

    secret_key = _first_non_empty_env(*_S3_SECRET_KEY_ENV_KEYS)
    if secret_key is not None:
        s3_kwargs["aws_secret_access_key"] = secret_key

    session_token = _first_non_empty_env(*_S3_SESSION_TOKEN_ENV_KEYS)
    if session_token is not None:
        s3_kwargs["aws_session_token"] = session_token

    endpoint = _first_non_empty_env(*_S3_ENDPOINT_ENV_KEYS)
    if endpoint is not None:
        s3_kwargs["endpoint_url"] = endpoint

    try:
        from chainlit.data.storage_clients.s3 import S3StorageClient
    except Exception as exc:  # pragma: no cover - import failure is environment specific.
        raise RuntimeError("Failed to import Chainlit S3StorageClient.") from exc

    provider = S3StorageClient(bucket=bucket, **s3_kwargs)
    return ensure_s3_storage_provider(provider)


def _patch_upload_file_contract(storage_provider: Any) -> None:
    if getattr(storage_provider, "_easierlit_upload_contract_patched", False):
        return

    original_upload_file = storage_provider.upload_file

    async def _strict_upload_file(
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> dict[str, Any]:
        uploaded_file = await original_upload_file(
            object_key,
            data,
            mime,
            overwrite,
            content_disposition,
        )
        if not isinstance(uploaded_file, dict):
            raise RuntimeError(
                "S3 upload failed: upload_file must return a dict with object_key and url."
            )

        uploaded_key = uploaded_file.get("object_key")
        uploaded_url = uploaded_file.get("url")
        if not isinstance(uploaded_key, str) or not uploaded_key.strip():
            raise RuntimeError("S3 upload failed: missing object_key in upload response.")
        if not isinstance(uploaded_url, str) or not uploaded_url.strip():
            raise RuntimeError("S3 upload failed: missing url in upload response.")
        return uploaded_file

    setattr(storage_provider, "upload_file", _strict_upload_file)
    setattr(storage_provider, "_easierlit_upload_contract_patched", True)


def ensure_s3_storage_provider(storage_provider: BaseStorageClient | Any | None) -> Any:
    from chainlit.data.storage_clients.s3 import S3StorageClient

    if storage_provider is None:
        raise ValueError("EasierlitPersistenceConfig.storage_provider must be an S3StorageClient.")

    if not isinstance(storage_provider, S3StorageClient):
        raise TypeError(
            "EasierlitPersistenceConfig.storage_provider must be an instance of "
            "chainlit.data.storage_clients.s3.S3StorageClient."
        )

    if getattr(storage_provider, "client", None) is None:
        raise RuntimeError(
            "Configured S3StorageClient is not initialized. "
            "Check S3 credentials, region, endpoint, and bucket settings."
        )

    _patch_upload_file_contract(storage_provider)
    return storage_provider


async def assert_s3_storage_operational(storage_provider: BaseStorageClient | Any | None) -> None:
    provider = ensure_s3_storage_provider(storage_provider)
    probe_key = f"easierlit-preflight/{uuid4().hex}.txt"
    uploaded_file = await provider.upload_file(
        probe_key,
        b"easierlit-s3-preflight",
        mime="text/plain",
        overwrite=True,
    )
    uploaded_key = uploaded_file.get("object_key")
    if not isinstance(uploaded_key, str) or not uploaded_key.strip():
        raise RuntimeError("S3 preflight failed: missing object_key in upload response.")

    try:
        deleted = await provider.delete_file(uploaded_key)
    except Exception as exc:
        raise RuntimeError("S3 preflight failed: uploaded probe file could not be deleted.") from exc

    if deleted is False:
        LOGGER.warning("S3 preflight probe deletion returned False for key '%s'.", uploaded_key)


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
        default_factory=_default_storage_provider
    )

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        self.storage_provider = ensure_s3_storage_provider(self.storage_provider)


@dataclass(slots=True)
class EasierlitDiscordConfig:
    enabled: bool = True
    bot_token: str | None = None

    def __post_init__(self) -> None:
        if self.bot_token is not None and not self.bot_token.strip():
            raise ValueError("EasierlitDiscordConfig.bot_token must not be empty.")
