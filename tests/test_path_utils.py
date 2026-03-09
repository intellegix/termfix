"""Tests for Windows path normalization utilities."""

from __future__ import annotations

from termfix.core.path_utils import (
    collapse_env_vars,
    is_unc_path,
    normalize_path,
    path_basename_match,
    path_contains,
    split_path_components,
)


class TestNormalizePath:
    def test_forward_slash_conversion(self) -> None:
        result = normalize_path("C:/Users/test/projects")
        assert result == r"C:\users\test\projects"

    def test_drive_letter_uppercase(self) -> None:
        assert normalize_path(r"c:\users\test") == r"C:\users\test"

    def test_trailing_slash_removal(self) -> None:
        result = normalize_path("C:\\Users\\test\\projects\\\\")
        assert not result.endswith("\\\\")
        assert "projects" in result

    def test_root_keeps_trailing_slash(self) -> None:
        result = normalize_path("C:\\")
        assert result == "C:\\"

    def test_unc_path_preserved(self) -> None:
        result = normalize_path("\\\\server\\share\\folder")
        assert result.startswith("\\\\")

    def test_empty_string(self) -> None:
        assert normalize_path("") == ""

    def test_env_var_expansion(self) -> None:
        import os
        os.environ["TEST_TERMFIX_VAR"] = "expanded"
        result = normalize_path("%TEST_TERMFIX_VAR%")
        assert "expanded" in result
        del os.environ["TEST_TERMFIX_VAR"]

    def test_mixed_separators(self) -> None:
        result = normalize_path("C:/Users\\test/projects")
        assert result == r"C:\users\test\projects"

    def test_case_insensitive(self) -> None:
        # Windows paths are case-insensitive; normalize lowercases
        assert normalize_path(r"C:\Users\Test") == normalize_path(r"c:\users\test")


class TestPathMatching:
    def test_basename_match(self) -> None:
        assert path_basename_match(r"C:\Users\test\podcast-notes", "podcast")

    def test_basename_no_match(self) -> None:
        assert not path_basename_match(r"C:\Users\test\documents", "podcast")

    def test_path_contains(self) -> None:
        assert path_contains(r"C:\Users\test\projects\myapp", "projects")

    def test_path_contains_case_insensitive(self) -> None:
        assert path_contains(r"C:\Users\Test\Projects", "projects")


class TestUncPath:
    def test_unc_detected(self) -> None:
        assert is_unc_path("\\\\server\\share")

    def test_local_not_unc(self) -> None:
        assert not is_unc_path(r"C:\Users\test")


class TestSplitPathComponents:
    def test_basic_split(self) -> None:
        parts = split_path_components(r"C:\Users\test\projects")
        assert "users" in parts
        assert "test" in parts
        assert "projects" in parts

    def test_unc_split(self) -> None:
        parts = split_path_components("\\\\server\\share\\folder")
        assert "server" in parts
        assert "share" in parts
        assert "folder" in parts


class TestCollapseEnvVars:
    def test_home_to_tilde(self) -> None:
        from pathlib import Path
        home = str(Path.home())
        result = collapse_env_vars(home + r"\documents")
        assert result.startswith("~")

    def test_no_match_unchanged(self) -> None:
        result = collapse_env_vars(r"D:\other\path")
        assert result == r"D:\other\path"
