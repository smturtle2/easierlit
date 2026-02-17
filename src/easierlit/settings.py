from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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


def _default_storage_provider() -> Any | None:
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

        return S3StorageClient(bucket=bucket, **s3_kwargs)
    except Exception:
        LOGGER.exception(
            "Failed to initialize default S3StorageClient; continuing without storage provider."
        )
        return None


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
    storage_provider: BaseStorageClient | Any | None = field(
        default_factory=_default_storage_provider
    )


@dataclass(slots=True)
class EasierlitDiscordConfig:
    enabled: bool = True
    bot_token: str | None = None

    def __post_init__(self) -> None:
        if self.bot_token is not None and not self.bot_token.strip():
            raise ValueError("EasierlitDiscordConfig.bot_token must not be empty.")
