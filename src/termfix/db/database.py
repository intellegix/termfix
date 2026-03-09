"""SQLite database manager with WAL mode and cloud-drive detection."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS directories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    frecency_score REAL NOT NULL DEFAULT 0.0,
    last_visit_ts REAL NOT NULL DEFAULT 0.0,
    visit_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS directory_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directory_id INTEGER NOT NULL REFERENCES directories(id),
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dir_visits_dir_id ON directory_visits(directory_id);
CREATE INDEX IF NOT EXISTS idx_dir_visits_ts ON directory_visits(timestamp);
CREATE INDEX IF NOT EXISTS idx_directories_score ON directories(frecency_score DESC);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    cwd TEXT,
    exit_code INTEGER,
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_commands_cmd ON commands(command);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    typo TEXT NOT NULL,
    correction TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL
);
"""


def _is_cloud_or_network_path(path: Path) -> bool:
    """Detect if path is on OneDrive, Dropbox, Google Drive, or a network share."""
    path_str = str(path).lower()
    cloud_markers = ["onedrive", "dropbox", "google drive", "icloud"]
    if any(marker in path_str for marker in cloud_markers):
        return True
    # UNC paths (network shares)
    if path_str.startswith("\\\\"):
        return True
    # Check if drive is a network drive (Windows-specific)
    try:
        drive = os.path.splitdrive(str(path))[0]
        if drive and len(drive) == 2:
            import ctypes

            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")  # type: ignore[attr-defined]
            if drive_type == 4:  # DRIVE_REMOTE
                return True
    except Exception:
        pass
    return False


class Database:
    """SQLite database manager for termfix persistent storage."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._use_wal = not _is_cloud_or_network_path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self._use_wal:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                logger.debug("SQLite using WAL mode")
            else:
                self._conn.execute("PRAGMA journal_mode=DELETE")
                logger.info("SQLite using rollback journal (cloud/network drive detected)")
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_connection()

    def initialize(self) -> None:
        """Create schema if needed, run migrations."""
        conn = self.conn
        conn.executescript(SCHEMA_SQL)
        # Check/set schema version
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
        else:
            current = row["version"]
            if current < SCHEMA_VERSION:
                self._migrate(current, SCHEMA_VERSION)

    def _migrate(self, from_version: int, to_version: int) -> None:
        """Run schema migrations. Placeholder for future versions."""
        logger.info("Migrating schema from v%d to v%d", from_version, to_version)
        self.conn.execute(
            "UPDATE schema_version SET version = ?", (to_version,)
        )
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for atomic transactions."""
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
