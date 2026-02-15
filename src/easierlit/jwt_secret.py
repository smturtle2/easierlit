from __future__ import annotations

import os
import secrets
from pathlib import Path

DEFAULT_JWT_SECRET_PATH = ".chainlit/jwt.secret"
DEFAULT_MIN_BYTES = 32


def _generate_secret(min_bytes: int) -> str:
    # token_hex(n) creates a 2n-length ASCII secret.
    token_bytes = max(DEFAULT_MIN_BYTES, (min_bytes + 1) // 2)
    return secrets.token_hex(token_bytes)


def _apply_secure_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Best effort only: permission changes can fail on some filesystems.
        pass


def ensure_jwt_secret(
    secret_path: str | Path = DEFAULT_JWT_SECRET_PATH,
    min_bytes: int = DEFAULT_MIN_BYTES,
) -> str:
    path = Path(secret_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)

    secret: str | None = None
    if path.exists():
        secret = path.read_text(encoding="utf-8").strip()
        if len(secret.encode("utf-8")) < min_bytes:
            secret = None

    if secret is None:
        secret = _generate_secret(min_bytes=min_bytes)
        path.write_text(secret + "\n", encoding="utf-8")

    _apply_secure_permissions(path)
    return secret
