"""Mozilla-style frecency scoring for directory navigation."""

from __future__ import annotations

import logging
import time

from termfix.core.path_utils import normalize_path, path_basename_match, path_contains
from termfix.db.database import Database

logger = logging.getLogger(__name__)

# Mozilla frecency bucket weights (days -> weight)
FRECENCY_BUCKETS = [
    (4, 100),    # visited in last 4 days
    (14, 70),    # visited in last 14 days
    (31, 50),    # visited in last month
    (90, 30),    # visited in last 3 months
    (None, 10),  # older
]

SECONDS_PER_DAY = 86400.0


def _bucket_weight(age_seconds: float) -> int:
    """Get the frecency weight for a visit based on its age."""
    age_days = age_seconds / SECONDS_PER_DAY
    for max_days, weight in FRECENCY_BUCKETS:
        if max_days is None or age_days <= max_days:
            return weight
    return 10  # fallback


class FrecencyEngine:
    """Frecency-based directory ranking with aging and pruning."""

    def __init__(self, db: Database, aging_threshold: float = 10_000.0) -> None:
        self.db = db
        self.aging_threshold = aging_threshold

    def record_visit(self, path: str) -> None:
        """Record a directory visit and recompute its frecency score."""
        normalized = normalize_path(path)
        now = time.time()

        with self.db.transaction() as conn:
            # Upsert directory
            row = conn.execute(
                "SELECT id FROM directories WHERE path = ?", (normalized,)
            ).fetchone()

            if row is None:
                cursor = conn.execute(
                    "INSERT INTO directories (path, last_visit_ts, visit_count) VALUES (?, ?, 1)",
                    (normalized, now),
                )
                dir_id = cursor.lastrowid
            else:
                dir_id = row["id"]
                conn.execute(
                    "UPDATE directories SET last_visit_ts = ?, visit_count = visit_count + 1 WHERE id = ?",
                    (now, dir_id),
                )

            # Record visit
            conn.execute(
                "INSERT INTO directory_visits (directory_id, timestamp) VALUES (?, ?)",
                (dir_id, now),
            )

            # Recompute score for this directory
            self._recompute_score(conn, dir_id, now)

        # Check if aging is needed
        self._age_if_needed()

    def _recompute_score(
        self, conn: "sqlite3.Connection", dir_id: int, now: float  # type: ignore[name-defined]
    ) -> None:
        """Recompute frecency score for a single directory based on its visit history."""
        visits = conn.execute(
            "SELECT timestamp FROM directory_visits WHERE directory_id = ?", (dir_id,)
        ).fetchall()

        score = 0.0
        for visit in visits:
            age = now - visit["timestamp"]
            score += _bucket_weight(age)

        conn.execute(
            "UPDATE directories SET frecency_score = ? WHERE id = ?", (score, dir_id)
        )

    def get_top(self, limit: int = 10) -> list[dict[str, object]]:
        """Return top directories by frecency score."""
        rows = self.db.conn.execute(
            "SELECT path, frecency_score, visit_count, last_visit_ts FROM directories "
            "WHERE frecency_score > 0 ORDER BY frecency_score DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return [
            {
                "path": row["path"],
                "score": row["frecency_score"],
                "visit_count": row["visit_count"],
                "last_visit": row["last_visit_ts"],
            }
            for row in rows
        ]

    def query(self, partial: str, limit: int = 10) -> list[dict[str, object]]:
        """Fuzzy match against directory paths, ranked by frecency."""
        # Get all directories with positive scores
        rows = self.db.conn.execute(
            "SELECT path, frecency_score, visit_count, last_visit_ts FROM directories "
            "WHERE frecency_score > 0 ORDER BY frecency_score DESC",
        ).fetchall()

        results = []
        partial_lower = partial.lower()

        for row in rows:
            path = row["path"]
            # Match against basename first (stronger signal), then full path
            if path_basename_match(path, partial_lower) or path_contains(path, partial_lower):
                results.append(
                    {
                        "path": path,
                        "score": row["frecency_score"],
                        "visit_count": row["visit_count"],
                        "last_visit": row["last_visit_ts"],
                    }
                )
                if len(results) >= limit:
                    break

        return results

    def _age_if_needed(self) -> None:
        """If total scores exceed threshold, decay all scores proportionally and prune."""
        row = self.db.conn.execute(
            "SELECT SUM(frecency_score) as total FROM directories"
        ).fetchone()
        total = row["total"] if row and row["total"] else 0.0

        if total <= self.aging_threshold:
            return

        # Decay so total ≈ 90% of threshold
        factor = (self.aging_threshold * 0.9) / total
        logger.info("Aging frecency scores by factor %.4f (total was %.1f)", factor, total)

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE directories SET frecency_score = frecency_score * ?", (factor,)
            )
            # Delete visits for directories that will be pruned (FK constraint)
            conn.execute(
                "DELETE FROM directory_visits WHERE directory_id IN "
                "(SELECT id FROM directories WHERE frecency_score < 1.0)"
            )
            # Prune entries with near-zero scores
            conn.execute("DELETE FROM directories WHERE frecency_score < 1.0")
