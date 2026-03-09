"""Import PSReadLine command history to seed the termfix database."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from termfix.core.path_utils import normalize_path
from termfix.db.database import Database

logger = logging.getLogger(__name__)

CD_PATTERN = re.compile(
    r"^\s*(?:cd|Set-Location|Push-Location|sl)\s+(.+?)(?:\s*#.*)?$",
    re.IGNORECASE,
)


def _find_psreadline_history() -> Path | None:
    """Find PSReadLine history file path."""
    # Try querying PowerShell
    for ps_exe in ["pwsh", "powershell"]:
        try:
            result = subprocess.run(
                [ps_exe, "-NoProfile", "-Command",
                 "(Get-PSReadLineOption).HistorySavePath"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                path = Path(result.stdout.strip())
                if path.exists():
                    return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Fallback to default location
    default = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
    if default.exists():
        return default

    return None


def import_psreadline_history(db: Database) -> dict[str, int]:
    """Import PSReadLine history for commands and directory seeds.

    Returns dict with 'commands' and 'directories' counts.
    """
    history_path = _find_psreadline_history()
    if not history_path:
        logger.warning("PSReadLine history file not found")
        return {"commands": 0, "directories": 0}

    logger.info("Importing from: %s", history_path)

    lines: list[str] = []
    try:
        with open(history_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        logger.error("Failed to read history: %s", e)
        return {"commands": 0, "directories": 0}

    commands_imported = 0
    directories_imported = 0
    now = time.time()
    seen_commands: set[str] = set()
    seen_dirs: set[str] = set()

    with db.transaction() as conn:
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Import as command (deduplicate)
            if line not in seen_commands:
                seen_commands.add(line)
                # Spread timestamps across history for rough frecency seeding
                fake_ts = now - (len(lines) - i) * 60  # 1 min apart
                conn.execute(
                    "INSERT INTO commands (command, cwd, exit_code, timestamp) VALUES (?, ?, ?, ?)",
                    (line, None, None, fake_ts),
                )
                commands_imported += 1

            # Check if it's a cd command
            match = CD_PATTERN.match(line)
            if match:
                raw_path = match.group(1).strip().strip('"').strip("'")
                # Skip variables and relative paths we can't resolve
                if raw_path.startswith("$") or raw_path == "-" or raw_path == "~":
                    continue
                # Expand ~ and env vars
                expanded = os.path.expanduser(os.path.expandvars(raw_path))
                try:
                    resolved = Path(expanded)
                    if resolved.is_absolute():
                        normalized = normalize_path(str(resolved))
                        if normalized not in seen_dirs:
                            seen_dirs.add(normalized)
                            fake_ts = now - (len(lines) - i) * 60
                            # Upsert directory
                            row = conn.execute(
                                "SELECT id FROM directories WHERE path = ?",
                                (normalized,),
                            ).fetchone()
                            if row is None:
                                cursor = conn.execute(
                                    "INSERT INTO directories (path, frecency_score, last_visit_ts, visit_count) "
                                    "VALUES (?, ?, ?, ?)",
                                    (normalized, 10.0, fake_ts, 1),
                                )
                                dir_id = cursor.lastrowid
                            else:
                                dir_id = row["id"]
                                conn.execute(
                                    "UPDATE directories SET visit_count = visit_count + 1, "
                                    "frecency_score = frecency_score + 10 WHERE id = ?",
                                    (dir_id,),
                                )
                            conn.execute(
                                "INSERT INTO directory_visits (directory_id, timestamp) VALUES (?, ?)",
                                (dir_id, fake_ts),
                            )
                            directories_imported += 1
                except (OSError, ValueError):
                    continue

    logger.info("Imported %d commands, %d directories", commands_imported, directories_imported)
    return {"commands": commands_imported, "directories": directories_imported}
