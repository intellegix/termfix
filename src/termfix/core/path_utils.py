"""Windows path normalization utilities."""

from __future__ import annotations

import os
import re
from pathlib import Path


def normalize_path(path_str: str) -> str:
    """Normalize a Windows path for consistent storage and comparison.

    - Expands environment variables (%USERPROFILE%, etc.)
    - Converts forward slashes to backslashes
    - Uppercases drive letter (c: -> C:)
    - Preserves UNC paths (\\\\server\\share)
    - Removes trailing backslash (except for root like C:\\)
    """
    if not path_str:
        return path_str

    # Expand environment variables
    expanded = os.path.expandvars(path_str)

    # Use os.path.normpath + normcase for canonical Windows form
    # normcase lowercases on Windows (paths are case-insensitive)
    normalized = os.path.normcase(os.path.normpath(expanded))

    # Uppercase drive letter (normcase lowercases it, we want C: not c:)
    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = normalized[0].upper() + normalized[1:]

    return normalized


def _is_unc_root(path: str) -> bool:
    """Check if a path is a UNC root (e.g., \\\\server\\share\\)."""
    if not path.startswith("\\\\"):
        return False
    # Count backslash-separated parts after \\
    parts = path[2:].rstrip("\\").split("\\")
    return len(parts) <= 2


def is_unc_path(path_str: str) -> bool:
    """Check if a path is a UNC path."""
    return path_str.startswith("\\\\")


def get_home_dir() -> Path:
    """Get home directory (Unicode-safe via Path.home())."""
    return Path.home()


def path_contains(path_str: str, fragment: str) -> bool:
    """Check if a path contains a fragment (case-insensitive)."""
    return fragment.lower() in path_str.lower()


def path_basename_match(path_str: str, query: str) -> bool:
    """Check if the basename of a path matches a query (case-insensitive)."""
    basename = os.path.basename(path_str.rstrip("\\"))
    return query.lower() in basename.lower()


def split_path_components(path_str: str) -> list[str]:
    """Split a path into its component parts for matching."""
    normalized = normalize_path(path_str)
    # Split on backslash, filter empties
    return [p for p in normalized.split("\\") if p and p != ":"]


ENV_VAR_RE = re.compile(r"%([^%]+)%")


def collapse_env_vars(path_str: str) -> str:
    """Replace known paths with environment variable equivalents for display."""
    home = str(Path.home())
    if path_str.startswith(home):
        return path_str.replace(home, "~", 1)
    return path_str
