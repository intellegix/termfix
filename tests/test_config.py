"""Tests for TermfixConfig loading, validation, and error handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from termfix.config import TermfixConfig


class TestConfigDefaults:
    def test_default_loads(self, tmp_path: Path) -> None:
        cfg = TermfixConfig(data_dir=tmp_path)
        assert cfg.spell_max_distance == 2
        assert cfg.frecency_aging_threshold == 10_000.0
        assert cfg.daemon_log_level == "INFO"

    def test_custom_data_dir(self, tmp_path: Path) -> None:
        cfg = TermfixConfig(data_dir=tmp_path / "custom")
        assert cfg.data_dir == tmp_path / "custom"

    def test_ensure_data_dir_creates(self, tmp_path: Path) -> None:
        target = tmp_path / "newdir"
        cfg = TermfixConfig(data_dir=target)
        result = cfg.ensure_data_dir()
        assert result.is_dir()


class TestConfigToml:
    def test_toml_override(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('spell_max_distance = 5\ndaemon_log_level = "DEBUG"\n')
        cfg = TermfixConfig(data_dir=tmp_path)
        assert cfg.spell_max_distance == 5
        assert cfg.daemon_log_level == "DEBUG"

    def test_invalid_toml_graceful(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        toml_path = tmp_path / "config.toml"
        toml_path.write_text("this is not [valid toml\n")
        # Should not raise — invalid TOML is silently skipped with a warning
        cfg = TermfixConfig(data_dir=tmp_path)
        assert cfg.spell_max_distance == 2  # falls back to defaults

    def test_bad_type_in_toml_raises(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('spell_max_distance = "not_an_int"\n')
        with pytest.raises(ValidationError):
            TermfixConfig(data_dir=tmp_path)


class TestConfigEnvVar:
    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERMFIX_SPELL_MAX_DISTANCE", "7")
        cfg = TermfixConfig(data_dir=tmp_path)
        assert cfg.spell_max_distance == 7


class TestLoadConfigHelper:
    def test_load_config_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_load_config() returns a valid TermfixConfig on success."""
        from termfix.cli import _load_config

        monkeypatch.setenv("TERMFIX_DATA_DIR", str(tmp_path))
        cfg = _load_config()
        assert isinstance(cfg, TermfixConfig)

    def test_load_config_bad_toml_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_load_config() exits with code 1 on bad config."""
        from termfix.cli import _load_config

        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "config.toml").write_text('spell_max_distance = "bad"\n')
        monkeypatch.setenv("TERMFIX_DATA_DIR", str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            _load_config()
        assert exc_info.value.code == 1
