"""Tests for the SQLite database manager."""

from __future__ import annotations

from pathlib import Path

from termfix.db.database import Database, _is_cloud_or_network_path


class TestCloudDriveDetection:
    def test_onedrive_detected(self) -> None:
        assert _is_cloud_or_network_path(Path(r"C:\Users\test\OneDrive\data.db"))

    def test_dropbox_detected(self) -> None:
        assert _is_cloud_or_network_path(Path(r"C:\Users\test\Dropbox\data.db"))

    def test_google_drive_detected(self) -> None:
        assert _is_cloud_or_network_path(Path(r"C:\Users\test\Google Drive\data.db"))

    def test_unc_detected(self) -> None:
        assert _is_cloud_or_network_path(Path(r"\\server\share\data.db"))

    def test_local_not_detected(self) -> None:
        assert not _is_cloud_or_network_path(Path(r"C:\Users\test\.termfix\data.db"))


class TestDatabase:
    def test_initialize_creates_tables(self, test_db: Database) -> None:
        tables = test_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "directories" in table_names
        assert "directory_visits" in table_names
        assert "commands" in table_names
        assert "corrections" in table_names
        assert "schema_version" in table_names

    def test_schema_version_set(self, test_db: Database) -> None:
        row = test_db.conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None
        assert row["version"] == 1

    def test_wal_mode_on_local(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "local.db")
        db.initialize()
        row = db.conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        db.close()

    def test_transaction_commit(self, test_db: Database) -> None:
        with test_db.transaction() as conn:
            conn.execute(
                "INSERT INTO directories (path, frecency_score, last_visit_ts, visit_count) "
                "VALUES (?, ?, ?, ?)",
                (r"C:\test", 10.0, 0.0, 1),
            )

        row = test_db.conn.execute(
            "SELECT * FROM directories WHERE path = ?", (r"C:\test",)
        ).fetchone()
        assert row is not None

    def test_transaction_rollback(self, test_db: Database) -> None:
        try:
            with test_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO directories (path, frecency_score, last_visit_ts, visit_count) "
                    "VALUES (?, ?, ?, ?)",
                    (r"C:\rollback_test", 10.0, 0.0, 1),
                )
                raise ValueError("Force rollback")
        except ValueError:
            pass

        row = test_db.conn.execute(
            "SELECT * FROM directories WHERE path = ?", (r"C:\rollback_test",)
        ).fetchone()
        assert row is None

    def test_double_initialize_is_safe(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "double.db")
        db.initialize()
        db.initialize()  # Should not raise
        db.close()

    def test_close_and_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "reopen.db"
        db = Database(db_path)
        db.initialize()

        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO directories (path, frecency_score, last_visit_ts, visit_count) "
                "VALUES (?, ?, ?, ?)",
                (r"C:\persist", 5.0, 0.0, 1),
            )

        db.close()

        db2 = Database(db_path)
        db2.initialize()
        row = db2.conn.execute(
            "SELECT * FROM directories WHERE path = ?", (r"C:\persist",)
        ).fetchone()
        assert row is not None
        db2.close()
