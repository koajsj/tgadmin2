from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.storage.repository import SCHEMA_SQL

DATABASE_SCHEMA_VERSION = 2


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    _ensure_group_settings_columns(connection)
    connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    connection.commit()


def _ensure_group_settings_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(group_settings)").fetchall()
    }
    if "auto_delete_seconds" not in columns:
        connection.execute(
            "ALTER TABLE group_settings ADD COLUMN auto_delete_seconds INTEGER NOT NULL DEFAULT 0"
        )
