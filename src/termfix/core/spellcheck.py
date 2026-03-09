"""PATH scanner and Levenshtein-based command spell correction."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Try rapidfuzz, fall back to pure-Python implementation
try:
    from rapidfuzz.distance import Levenshtein as _rf_levenshtein

    def _levenshtein_distance(s1: str, s2: str) -> int:
        return _rf_levenshtein.distance(s1, s2)  # type: ignore[no-any-return]

except ImportError:
    logger.info("rapidfuzz not available — using pure-Python Levenshtein")

    def _levenshtein_distance(s1: str, s2: str) -> int:
        """Pure-Python Levenshtein distance (Wagner-Fischer algorithm)."""
        if len(s1) < len(s2):
            return _levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                cost = 0 if c1 == c2 else 1
                curr_row.append(
                    min(
                        curr_row[j] + 1,       # insert
                        prev_row[j + 1] + 1,   # delete
                        prev_row[j] + cost,     # substitute
                    )
                )
            prev_row = curr_row
        return prev_row[-1]


class SpellChecker:
    """Scans PATH for executables and provides Levenshtein-based corrections."""

    def __init__(
        self,
        max_distance: int = 2,
        scan_extensions: list[str] | None = None,
        custom_commands: dict[str, str] | None = None,
    ) -> None:
        self.max_distance = max_distance
        self.scan_extensions = set(
            ext.lower() for ext in (scan_extensions or [".exe", ".cmd", ".bat", ".ps1", ".com"])
        )
        self.custom_commands = custom_commands or {}
        self._executables: dict[str, str] = {}  # stem.lower() -> full_path
        self._path_hash: str = ""

    def scan_path(self) -> int:
        """Scan all PATH directories for executables. Returns count found."""
        path_var = os.environ.get("PATH", "")
        new_hash = hashlib.md5(path_var.encode()).hexdigest()

        executables: dict[str, str] = {}
        dirs = path_var.split(os.pathsep)

        for dir_str in dirs:
            if not dir_str:
                continue
            try:
                dir_path = Path(os.fspath(dir_str))
                if not dir_path.is_dir():
                    continue
                with os.scandir(dir_path) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_file():
                                continue
                            name = entry.name
                            stem, ext = os.path.splitext(name)
                            if ext.lower() in self.scan_extensions:
                                key = stem.lower()
                                if key not in executables:
                                    executables[key] = entry.path
                        except OSError:
                            continue
            except (PermissionError, OSError) as e:
                logger.debug("Skipping PATH dir %s: %s", dir_str, e)
                continue

        self._executables = executables
        self._path_hash = new_hash
        logger.info("PATH scan complete: %d executables found", len(executables))
        return len(executables)

    def needs_refresh(self) -> bool:
        """Check if PATH has changed since last scan."""
        path_var = os.environ.get("PATH", "")
        current_hash = hashlib.md5(path_var.encode()).hexdigest()
        return current_hash != self._path_hash

    def refresh_if_needed(self) -> None:
        """Re-scan PATH only if it has changed."""
        if self.needs_refresh() or not self._executables:
            self.scan_path()

    def check(self, command_name: str) -> list[tuple[str, int, str]]:
        """Check a command name for spelling corrections.

        Returns list of (corrected_name, distance, full_path) sorted by distance.
        """
        self.refresh_if_needed()

        # Check custom corrections first
        lower_cmd = command_name.lower()
        if lower_cmd in self.custom_commands:
            target = self.custom_commands[lower_cmd]
            if target.lower() in self._executables:
                return [(target, 0, self._executables[target.lower()])]

        # Exact match means no correction needed
        if lower_cmd in self._executables:
            return []

        results: list[tuple[str, int, str]] = []
        for stem, full_path in self._executables.items():
            # Quick length filter to skip obvious non-matches
            if abs(len(stem) - len(lower_cmd)) > self.max_distance:
                continue
            dist = _levenshtein_distance(lower_cmd, stem)
            if 0 < dist <= self.max_distance:
                results.append((stem, dist, full_path))

        results.sort(key=lambda x: (x[1], x[0]))
        return results

    @property
    def executable_count(self) -> int:
        return len(self._executables)
