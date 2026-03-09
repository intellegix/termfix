"""Tests for the spell check engine."""

from __future__ import annotations

from pathlib import Path

from termfix.core.spellcheck import SpellChecker, _levenshtein_distance


class TestLevenshteinDistance:
    def test_identical_strings(self) -> None:
        assert _levenshtein_distance("git", "git") == 0

    def test_single_insertion(self) -> None:
        assert _levenshtein_distance("git", "gitt") == 1

    def test_single_deletion(self) -> None:
        assert _levenshtein_distance("gitt", "git") == 1

    def test_single_substitution(self) -> None:
        assert _levenshtein_distance("git", "gat") == 1

    def test_transposition(self) -> None:
        # "gti" -> "git" requires 2 operations (not 1, since this is pure Levenshtein)
        assert _levenshtein_distance("gti", "git") == 2

    def test_empty_string(self) -> None:
        assert _levenshtein_distance("", "git") == 3
        assert _levenshtein_distance("git", "") == 3

    def test_both_empty(self) -> None:
        assert _levenshtein_distance("", "") == 0

    def test_cladue_to_claude(self) -> None:
        assert _levenshtein_distance("cladue", "claude") == 2


class TestSpellChecker:
    def test_exact_match_returns_empty(self, patched_path: Path) -> None:
        checker = SpellChecker()
        checker.scan_path()
        result = checker.check("git")
        assert result == []

    def test_cladue_suggests_claude(self, patched_path: Path) -> None:
        checker = SpellChecker()
        checker.scan_path()
        result = checker.check("cladue")
        names = [r[0] for r in result]
        assert "claude" in names

    def test_gti_suggests_git(self, patched_path: Path) -> None:
        checker = SpellChecker(max_distance=2)
        checker.scan_path()
        result = checker.check("gti")
        names = [r[0] for r in result]
        assert "git" in names

    def test_high_distance_returns_nothing(self, patched_path: Path) -> None:
        checker = SpellChecker(max_distance=1)
        checker.scan_path()
        result = checker.check("xyzabc")
        assert result == []

    def test_custom_corrections(self, patched_path: Path) -> None:
        checker = SpellChecker(custom_commands={"k": "kubectl"})
        checker.scan_path()
        result = checker.check("k")
        assert len(result) == 1
        assert result[0][0] == "kubectl"

    def test_scan_counts_executables(self, patched_path: Path) -> None:
        checker = SpellChecker()
        count = checker.scan_path()
        assert count > 0
        assert checker.executable_count == count

    def test_path_hash_refresh(self, patched_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        checker = SpellChecker()
        checker.scan_path()
        assert not checker.needs_refresh()

        # Modify PATH
        monkeypatch.setenv("PATH", str(patched_path) + ";" + str(patched_path))
        assert checker.needs_refresh()

    def test_results_sorted_by_distance(self, patched_path: Path) -> None:
        checker = SpellChecker(max_distance=2)
        checker.scan_path()
        result = checker.check("nod")  # node (dist 1), npm (dist 2+)
        if len(result) > 1:
            assert result[0][1] <= result[1][1]
