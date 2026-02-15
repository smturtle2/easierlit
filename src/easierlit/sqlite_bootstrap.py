from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

LOGGER = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    "id" TEXT PRIMARY KEY,
    "identifier" TEXT NOT NULL UNIQUE,
    "createdAt" TEXT NOT NULL,
    "metadata" TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    "id" TEXT PRIMARY KEY,
    "createdAt" TEXT,
    "name" TEXT,
    "userId" TEXT,
    "userIdentifier" TEXT,
    "tags" TEXT,
    "metadata" TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    "id" TEXT PRIMARY KEY,
    "name" TEXT,
    "type" TEXT,
    "threadId" TEXT,
    "parentId" TEXT,
    "command" TEXT,
    "modes" TEXT,
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
    "defaultOpen" INTEGER,
    "showInput" TEXT,
    "language" TEXT
);

CREATE TABLE IF NOT EXISTS elements (
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

CREATE TABLE IF NOT EXISTS feedbacks (
    "id" TEXT PRIMARY KEY,
    "forId" TEXT,
    "threadId" TEXT,
    "value" REAL,
    "comment" TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_thread_id ON steps("threadId");
CREATE INDEX IF NOT EXISTS idx_elements_thread_id ON elements("threadId");
CREATE INDEX IF NOT EXISTS idx_feedbacks_for_id ON feedbacks("forId");
CREATE INDEX IF NOT EXISTS idx_threads_user_id ON threads("userId");
"""

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "users": {"id", "identifier", "createdAt", "metadata"},
    "threads": {"id", "createdAt", "name", "userId", "userIdentifier", "tags", "metadata"},
    "steps": {
        "id",
        "name",
        "type",
        "threadId",
        "parentId",
        "command",
        "modes",
        "streaming",
        "waitForAnswer",
        "isError",
        "metadata",
        "tags",
        "input",
        "output",
        "createdAt",
        "start",
        "end",
        "generation",
        "defaultOpen",
        "showInput",
        "language",
    },
    "elements": {
        "id",
        "threadId",
        "type",
        "chainlitKey",
        "url",
        "objectKey",
        "name",
        "display",
        "size",
        "language",
        "page",
        "autoPlay",
        "playerConfig",
        "forId",
        "mime",
        "props",
    },
    "feedbacks": {"id", "forId", "threadId", "value", "comment"},
}


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {str(row[1]) for row in rows}


def _is_schema_compatible(connection: sqlite3.Connection) -> bool:
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        columns = _table_columns(connection, table_name)
        if not columns:
            return False
        if not required_columns.issubset(columns):
            return False
    return True


def _next_backup_path(path: Path) -> Path:
    candidate = path.with_name(f"{path.name}.bak")
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.bak{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _recreate_if_incompatible(path: Path) -> None:
    if not path.exists():
        return

    needs_recreate = False
    try:
        connection = sqlite3.connect(path)
        try:
            needs_recreate = not _is_schema_compatible(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        needs_recreate = True

    if not needs_recreate:
        return

    backup_path = _next_backup_path(path)
    path.replace(backup_path)
    LOGGER.warning(
        "Detected incompatible SQLite schema at %s. Recreated database; backup saved to %s.",
        path,
        backup_path,
    )


def ensure_sqlite_schema(sqlite_path: str | Path) -> Path:
    path = Path(sqlite_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    path.parent.mkdir(parents=True, exist_ok=True)
    _recreate_if_incompatible(path)

    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_SQL)
        connection.commit()
    finally:
        connection.close()

    return path
