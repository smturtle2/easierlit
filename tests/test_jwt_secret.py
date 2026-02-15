from easierlit.jwt_secret import ensure_jwt_secret


def test_ensure_jwt_secret_creates_secret_file(tmp_path):
    secret_path = tmp_path / ".chainlit" / "jwt.secret"

    secret = ensure_jwt_secret(secret_path=secret_path)

    assert secret_path.exists()
    assert secret_path.read_text(encoding="utf-8").strip() == secret
    assert len(secret.encode("utf-8")) >= 32


def test_ensure_jwt_secret_reuses_existing_valid_secret(tmp_path):
    secret_path = tmp_path / ".chainlit" / "jwt.secret"
    existing = "a" * 64
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(existing + "\n", encoding="utf-8")

    secret = ensure_jwt_secret(secret_path=secret_path)

    assert secret == existing
    assert secret_path.read_text(encoding="utf-8").strip() == existing


def test_ensure_jwt_secret_regenerates_short_secret(tmp_path):
    secret_path = tmp_path / ".chainlit" / "jwt.secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text("short\n", encoding="utf-8")

    secret = ensure_jwt_secret(secret_path=secret_path)

    assert secret != "short"
    assert len(secret.encode("utf-8")) >= 32
    assert secret_path.read_text(encoding="utf-8").strip() == secret


def test_ensure_jwt_secret_respects_custom_min_bytes(tmp_path):
    secret_path = tmp_path / ".chainlit" / "jwt.secret"

    secret = ensure_jwt_secret(secret_path=secret_path, min_bytes=80)

    assert len(secret.encode("utf-8")) >= 80
