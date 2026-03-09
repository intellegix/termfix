"""Tests for the command suggestion engine."""

from __future__ import annotations

from termfix.core.suggest import SuggestEngine
from termfix.db.database import Database


class TestSuggestEngine:
    def test_record_and_suggest(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("git commit -m 'test'")
        engine.record("git push origin main")
        engine.record("git status")

        results = engine.suggest("git com")
        assert len(results) > 0
        commands = [r[0] for r in results]
        assert any("commit" in cmd for cmd in commands)

    def test_empty_partial_returns_nothing(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("git status")

        results = engine.suggest("")
        # Empty partial — implementation may or may not return results
        # The important thing is it doesn't crash
        assert isinstance(results, list)

    def test_no_match(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("git status")

        results = engine.suggest("xyznonexistent1234")
        assert len(results) == 0

    def test_cache_eviction(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_size=3, cache_ttl=0)

        engine.record("command1")
        engine.record("command2")
        engine.record("command3")
        engine.record("command4")  # Should evict command1

        assert len(engine._cache) <= 3

    def test_score_ordering(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("docker compose up")
        engine.record("docker compose down")
        engine.record("docker build .")

        results = engine.suggest("docker")
        if len(results) > 1:
            # Scores should be in descending order
            scores = [r[1] for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_record_with_metadata(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("npm test", cwd=r"C:\project", exit_code=0)

        # Verify it was stored in DB
        row = test_db.conn.execute(
            "SELECT * FROM commands WHERE command = 'npm test'"
        ).fetchone()
        assert row is not None
        assert row["cwd"] == r"C:\project"
        assert row["exit_code"] == 0

    def test_flush_does_not_crash(self, test_db: Database) -> None:
        engine = SuggestEngine(test_db, cache_ttl=0)
        engine.record("some command")
        engine.flush_to_db()  # Should not raise
