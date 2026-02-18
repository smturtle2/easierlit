from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from chainlit.data.storage_clients.base import BaseStorageClient

_DEFAULT_LOCAL_STORAGE_DIR = Path("public") / "easierlit"


class LocalFileStorageClient(BaseStorageClient):
    def __init__(self, base_dir: str | Path = _DEFAULT_LOCAL_STORAGE_DIR):
        resolved_base = Path(base_dir)
        if not resolved_base.is_absolute():
            resolved_base = Path.cwd() / resolved_base
        self.base_dir = resolved_base.resolve()
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
            "url": self._build_public_url(normalized_key),
        }

    async def get_read_url(self, object_key: str) -> str:
        normalized_key, file_path = self._resolve_path(object_key)
        if not file_path.is_file():
            raise FileNotFoundError(
                f"Local file not found for object_key '{normalized_key}'."
            )
        return self._build_public_url(normalized_key)

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

    def _build_public_url(self, object_key: str) -> str:
        root_path = os.getenv("CHAINLIT_ROOT_PATH", "").strip()
        if root_path:
            if not root_path.startswith("/"):
                root_path = f"/{root_path}"
            root_path = root_path.rstrip("/")
        encoded_key = quote(object_key, safe="/")
        if root_path:
            return f"{root_path}/public/easierlit/{encoded_key}"
        return f"/public/easierlit/{encoded_key}"

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
