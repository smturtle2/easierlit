from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from chainlit.data.storage_clients.base import BaseStorageClient

_APP_ROOT_ENV = "CHAINLIT_APP_ROOT"
_PARENT_ROOT_PATH_ENV = "CHAINLIT_PARENT_ROOT_PATH"
_ROOT_PATH_ENV = "CHAINLIT_ROOT_PATH"
_DEFAULT_LOCAL_STORAGE_SUBDIR = Path("easierlit")
LOCAL_STORAGE_ROUTE_PREFIX = "/easierlit/local"


class LocalFileStorageClient(BaseStorageClient):
    def __init__(self, base_dir: str | Path | None = None):
        self.public_root = self._resolve_public_root()
        self.public_root.mkdir(parents=True, exist_ok=True)

        self.base_dir = self._resolve_base_dir(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def upload_file(
        self,
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> dict[str, Any]:
        del mime, content_disposition

        normalized_key, file_path = self._resolve_path(object_key)
        if file_path.exists() and not overwrite:
            raise FileExistsError(
                f"Local file already exists for object_key '{normalized_key}' and overwrite=False."
            )

        if isinstance(data, str):
            payload = data.encode("utf-8")
        elif isinstance(data, bytes):
            payload = data
        else:
            raise TypeError("Local upload data must be bytes or str.")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        return {
            "object_key": normalized_key,
            "url": self._build_local_url(normalized_key),
        }

    async def get_read_url(self, object_key: str) -> str:
        normalized_key, file_path = self._resolve_path(object_key)
        if not file_path.is_file():
            raise FileNotFoundError(
                f"Local file not found for object_key '{normalized_key}'."
            )
        return self._build_local_url(normalized_key)

    async def delete_file(self, object_key: str) -> bool:
        _, file_path = self._resolve_path(object_key)
        if not file_path.exists():
            return False
        if not file_path.is_file():
            return False

        file_path.unlink()
        current_dir = file_path.parent
        while current_dir != self.base_dir:
            try:
                current_dir.rmdir()
            except OSError:
                break
            current_dir = current_dir.parent
        return True

    async def close(self) -> None:
        return None

    def _resolve_public_root(self) -> Path:
        app_root_raw = os.getenv(_APP_ROOT_ENV, "").strip()
        if app_root_raw:
            app_root = Path(app_root_raw).expanduser()
            if not app_root.is_absolute():
                app_root = Path.cwd() / app_root
        else:
            app_root = Path.cwd()
        return (app_root.resolve() / "public").resolve()

    def _resolve_base_dir(self, base_dir: str | Path | None) -> Path:
        if base_dir is None:
            resolved_base = self.public_root / _DEFAULT_LOCAL_STORAGE_SUBDIR
        else:
            candidate = Path(base_dir).expanduser()
            if candidate.is_absolute():
                resolved_base = candidate
            else:
                resolved_base = self.public_root / candidate

        return resolved_base.resolve()

    def _build_local_url(self, object_key: str) -> str:
        encoded_path = quote(object_key, safe="/")
        prefix = self._build_url_prefix()
        if prefix:
            return f"{prefix}{LOCAL_STORAGE_ROUTE_PREFIX}/{encoded_path}"
        return f"{LOCAL_STORAGE_ROUTE_PREFIX}/{encoded_path}"

    def resolve_file_path(self, object_key: str) -> Path:
        _, file_path = self._resolve_path(object_key)
        return file_path

    def _build_url_prefix(self) -> str:
        components = [
            self._normalize_url_component(os.getenv(_PARENT_ROOT_PATH_ENV, "")),
            self._normalize_url_component(os.getenv(_ROOT_PATH_ENV, "")),
        ]
        non_empty = [item.strip("/") for item in components if item]
        if not non_empty:
            return ""
        return "/" + "/".join(non_empty)

    def _normalize_url_component(self, raw_path: str) -> str:
        cleaned = raw_path.strip()
        if not cleaned or cleaned == "/":
            return ""
        if not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"
        return cleaned.rstrip("/")

    def _resolve_path(self, object_key: str) -> tuple[str, Path]:
        normalized_key = self._normalize_object_key(object_key)
        resolved_path = (self.base_dir / normalized_key).resolve()
        try:
            resolved_path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError(f"Invalid object_key path '{object_key}'.") from exc
        return normalized_key, resolved_path

    def _normalize_object_key(self, object_key: str) -> str:
        if not isinstance(object_key, str):
            raise TypeError("object_key must be a string.")

        normalized_raw = object_key.replace("\\", "/").strip("/")
        if not normalized_raw:
            raise ValueError("object_key must not be empty.")

        parts = PurePosixPath(normalized_raw).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"Invalid object_key '{object_key}'.")

        return "/".join(parts)
