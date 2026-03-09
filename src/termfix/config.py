"""Configuration via Pydantic settings with optional TOML file loading."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from termfix import DATA_DIR_NAME

logger = logging.getLogger(__name__)


def _default_data_dir() -> Path:
    return Path.home() / DATA_DIR_NAME


class TermfixConfig(BaseSettings):
    """Termfix configuration — loaded from env vars (TERMFIX_*) and optional TOML file."""

    model_config = {"env_prefix": "TERMFIX_"}

    data_dir: Path = Field(default_factory=_default_data_dir)

    # Spell correction
    spell_max_distance: int = 2
    spell_auto_execute: bool = False
    spell_scan_extensions: list[str] = Field(
        default=[".exe", ".cmd", ".bat", ".ps1", ".com"]
    )
    spell_custom_commands: dict[str, str] = Field(default_factory=dict)

    # Frecency
    frecency_max_results: int = 10
    frecency_aging_threshold: float = 10_000.0

    # Suggest
    suggest_cache_size: int = 5000
    suggest_cache_ttl_seconds: int = 300
    suggest_min_score: float = 60.0

    # Daemon
    daemon_log_level: str = "INFO"
    daemon_pipe_timeout_ms: int = 5000

    @model_validator(mode="before")
    @classmethod
    def load_toml_file(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Load settings from ~/.termfix/config.toml if it exists."""
        data_dir = values.get("data_dir") or _default_data_dir()
        toml_path = Path(data_dir) / "config.toml"
        if toml_path.is_file():
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    logger.warning("config.toml found but tomllib/tomli unavailable — skipping")
                    return values
            try:
                with open(toml_path, "rb") as f:
                    toml_data = tomllib.load(f)
                # TOML values are lower priority than env vars
                for key, val in toml_data.items():
                    if key not in values or values[key] is None:
                        values[key] = val
            except Exception:
                logger.warning("Failed to parse config.toml", exc_info=True)
        return values

    def ensure_data_dir(self) -> Path:
        """Create data directory if it doesn't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir
