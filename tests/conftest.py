"""Shared test fixtures for termfix."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from termfix.config import TermfixConfig
from termfix.db.database import Database


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for test data."""
    return tmp_path


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    """Provide an initialized test database."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def test_config(tmp_path: Path) -> TermfixConfig:
    """Provide a test configuration pointing to temp directory."""
    return TermfixConfig(data_dir=tmp_path)


@pytest.fixture
def fake_path_dir(tmp_path: Path) -> Path:
    """Create a directory with fake executables for PATH testing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Create fake executables
    executables = [
        "git.exe", "python.exe", "pip.exe", "node.exe", "npm.exe",
        "code.exe", "claude.exe", "docker.exe", "kubectl.exe",
        "ssh.exe", "curl.exe", "wget.exe", "terraform.exe",
    ]

    for name in executables:
        (bin_dir / name).write_text("")

    return bin_dir


@pytest.fixture
def patched_path(fake_path_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Monkeypatch PATH to include only the fake bin directory."""
    monkeypatch.setenv("PATH", str(fake_path_dir))
    return fake_path_dir
