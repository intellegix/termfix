"""Fuzzy command suggestion engine with LRU cache backed by SQLite."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

from termfix.db.database import Database

logger = logging.getLogger(__name__)

# Try rapidfuzz for fuzzy matching
try:
    from rapidfuzz import process as rf_process
    from rapidfuzz.fuzz import WRatio

    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    logger.info("rapidfuzz not available — command suggestions will use basic matching")


class SuggestEngine:
    """Fuzzy command suggestion engine with in-memory LRU cache."""

    def __init__(
        self,
        db: Database,
        cache_size: int = 5000,
        cache_ttl: int = 300,
        min_score: float = 60.0,
    ) -> None:
        self.db = db
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl
        self.min_score = min_score
        self._cache: OrderedDict[str, None] = OrderedDict()
        self._cache_loaded_at: float = 0.0

    def _ensure_cache(self) -> None:
        """Load or refresh the in-memory command cache from SQLite."""
        now = time.time()
        if self._cache and (now - self._cache_loaded_at) < self.cache_ttl:
            return

        rows = self.db.conn.execute(
            "SELECT DISTINCT command FROM commands ORDER BY timestamp DESC LIMIT ?",
            (self.cache_size,),
        ).fetchall()

        self._cache.clear()
        for row in rows:
            cmd = row["command"]
            if cmd not in self._cache:
                self._cache[cmd] = None

        self._cache_loaded_at = now
        logger.debug("Suggest cache refreshed: %d unique commands", len(self._cache))

    def record(self, command: str, cwd: str | None = None, exit_code: int | None = None) -> None:
        """Record a command execution."""
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO commands (command, cwd, exit_code, timestamp) VALUES (?, ?, ?, ?)",
                (command, cwd, exit_code, now),
            )

        # Update cache (add to front)
        if command in self._cache:
            self._cache.move_to_end(command, last=False)
        else:
            self._cache[command] = None
            self._cache.move_to_end(command, last=False)
            # Evict oldest if over capacity
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=True)

    def suggest(self, partial: str, limit: int = 5) -> list[tuple[str, float]]:
        """Suggest commands matching the partial input.

        Returns list of (command, score) sorted by match score.
        """
        self._ensure_cache()

        if not self._cache:
            return []

        if _HAS_RAPIDFUZZ:
            return self._suggest_rapidfuzz(partial, limit)
        return self._suggest_basic(partial, limit)

    def _suggest_rapidfuzz(self, partial: str, limit: int) -> list[tuple[str, float]]:
        """Use rapidfuzz WRatio for fuzzy matching."""
        choices = list(self._cache.keys())
        results = rf_process.extract(
            partial,
            choices,
            scorer=WRatio,
            limit=limit,
            score_cutoff=self.min_score,
        )
        return [(match, score) for match, score, _idx in results]

    def _suggest_basic(self, partial: str, limit: int) -> list[tuple[str, float]]:
        """Basic prefix + substring matching fallback."""
        partial_lower = partial.lower()
        prefix_matches: list[tuple[str, float]] = []
        contains_matches: list[tuple[str, float]] = []

        for cmd in self._cache:
            cmd_lower = cmd.lower()
            if cmd_lower.startswith(partial_lower):
                # Score based on length similarity
                score = (len(partial) / len(cmd)) * 100
                prefix_matches.append((cmd, score))
            elif partial_lower in cmd_lower:
                score = (len(partial) / len(cmd)) * 80
                contains_matches.append((cmd, score))

        # Prefix matches first, then substring
        results = sorted(prefix_matches, key=lambda x: -x[1])
        results.extend(sorted(contains_matches, key=lambda x: -x[1]))
        return results[:limit]

    def flush_to_db(self) -> None:
        """Flush any pending cache state. Called on daemon shutdown."""
        # Currently all writes go directly to DB, so this is a no-op.
        # Kept as a hook for future batched writes.
        logger.debug("Suggest cache flush complete (no-op)")
