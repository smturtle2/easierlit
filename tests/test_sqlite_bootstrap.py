import sqlite3

from easierlit.sqlite_bootstrap import ensure_sqlite_schema


def test_ensure_sqlite_schema_creates_tables_indexes_and_required_columns(tmp_path):
    db_path = tmp_path / "easierlit.db"

    first = ensure_sqlite_schema(db_path)
    second = ensure_sqlite_schema(db_path)

    assert first == second
    assert first.exists()

    connection = sqlite3.connect(first)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"users", "threads", "steps", "elements", "feedbacks"}.issubset(tables)

        step_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('steps')").fetchall()
        }
        assert {"command", "modes", "defaultOpen"}.issubset(step_columns)

        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {
            "idx_steps_thread_id",
            "idx_elements_thread_id",
            "idx_feedbacks_for_id",
            "idx_threads_user_id",
        }.issubset(indexes)
    finally:
        connection.close()


def test_ensure_sqlite_schema_recreates_legacy_database_and_keeps_backup(tmp_path):
    db_path = tmp_path / "easierlit-legacy.db"

    legacy_schema = """
    CREATE TABLE users (
        "id" TEXT PRIMARY KEY,
        "identifier" TEXT NOT NULL UNIQUE,
        "createdAt" TEXT NOT NULL,
        "metadata" TEXT
    );
    CREATE TABLE threads (
        "id" TEXT PRIMARY KEY,
        "createdAt" TEXT,
        "name" TEXT,
        "userId" TEXT,
        "userIdentifier" TEXT,
        "tags" TEXT,
        "metadata" TEXT
    );
    CREATE TABLE steps (
        "id" TEXT PRIMARY KEY,
        "name" TEXT,
        "type" TEXT,
        "threadId" TEXT,
        "parentId" TEXT,
        "streaming" INTEGER,
        "waitForAnswer" INTEGER,
        "isError" INTEGER,
        "metadata" TEXT,
        "tags" TEXT,
        "input" TEXT,
        "output" TEXT,
        "createdAt" TEXT,
        "start" TEXT,
        "end" TEXT,
        "generation" TEXT,
        "showInput" TEXT,
        "language" TEXT
    );
    CREATE TABLE elements (
        "id" TEXT PRIMARY KEY,
        "threadId" TEXT,
        "type" TEXT,
        "chainlitKey" TEXT,
        "url" TEXT,
        "objectKey" TEXT,
        "name" TEXT,
        "display" TEXT,
        "size" INTEGER,
        "language" TEXT,
        "page" INTEGER,
        "autoPlay" INTEGER,
        "playerConfig" TEXT,
        "forId" TEXT,
        "mime" TEXT,
        "props" TEXT
    );
    CREATE TABLE feedbacks (
        "id" TEXT PRIMARY KEY,
        "forId" TEXT,
        "threadId" TEXT,
        "value" REAL,
        "comment" TEXT
    );
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(legacy_schema)
        connection.commit()
    finally:
        connection.close()

    ensure_sqlite_schema(db_path)
    backups = sorted(tmp_path.glob("easierlit-legacy.db.bak*"))
    assert len(backups) == 1

    backup_connection = sqlite3.connect(backups[0])
    try:
        backup_step_columns = {
            row[1]
            for row in backup_connection.execute("PRAGMA table_info('steps')").fetchall()
        }
        assert "defaultOpen" not in backup_step_columns
    finally:
        backup_connection.close()

    current_connection = sqlite3.connect(db_path)
    try:
        current_step_columns = {
            row[1]
            for row in current_connection.execute("PRAGMA table_info('steps')").fetchall()
        }
        assert {"command", "modes", "defaultOpen"}.issubset(current_step_columns)
    finally:
        current_connection.close()

    ensure_sqlite_schema(db_path)
    backups_after = sorted(tmp_path.glob("easierlit-legacy.db.bak*"))
    assert len(backups_after) == 1
