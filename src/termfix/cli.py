"""Click CLI for termfix — daemon management, shell hooks, manual commands."""

from __future__ import annotations

import sys

import click

from termfix import __version__


@click.group()
@click.version_option(__version__, prog_name="termfix")
def main() -> None:
    """termfix — Windows terminal assistant with spell correction, frecency dirs, and fuzzy suggestions."""


# ---------------------------------------------------------------------------
# Daemon commands
# ---------------------------------------------------------------------------


@main.group()
def daemon() -> None:
    """Manage the termfix background daemon."""


@daemon.command()
def start() -> None:
    """Start the daemon in the background."""
    from termfix.daemon.manager import start as daemon_start

    if daemon_start():
        click.echo("Daemon started.")
    else:
        click.echo("Failed to start daemon.", err=True)
        sys.exit(1)


@daemon.command()
def stop() -> None:
    """Stop the running daemon."""
    from termfix.daemon.manager import stop as daemon_stop

    if daemon_stop():
        click.echo("Daemon stopped.")
    else:
        click.echo("Failed to stop daemon.", err=True)
        sys.exit(1)


@daemon.command()
def status() -> None:
    """Show daemon status."""
    from termfix.daemon.manager import status as daemon_status

    info = daemon_status()
    if info["running"]:
        click.echo(f"Daemon is running (PID {info['pid']})")
    else:
        if info.get("stale_pid"):
            click.echo(f"Daemon is not running (stale PID file: {info['pid']})")
        else:
            click.echo("Daemon is not running.")


@daemon.command()
def run() -> None:
    """Run daemon in the foreground (for debugging)."""
    from termfix.daemon.server import run_daemon

    click.echo("Running daemon in foreground (Ctrl+C to stop)...")
    run_daemon()


@daemon.group()
def autostart() -> None:
    """Manage daemon auto-start on login."""


@autostart.command("enable")
def autostart_enable() -> None:
    """Register daemon to start on Windows login."""
    from termfix.daemon.manager import autostart_enable as _enable

    if _enable():
        click.echo("Autostart enabled. Daemon will start on next login.")
    else:
        click.echo("Failed to enable autostart.", err=True)
        sys.exit(1)


@autostart.command("disable")
def autostart_disable() -> None:
    """Remove daemon auto-start task."""
    from termfix.daemon.manager import autostart_disable as _disable

    if _disable():
        click.echo("Autostart disabled.")
    else:
        click.echo("Failed to disable autostart.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Init commands (shell hook installation)
# ---------------------------------------------------------------------------


@main.group()
def init() -> None:
    """Install shell hooks."""


@init.command()
def powershell() -> None:
    """Install PowerShell hook into $PROFILE."""
    from termfix.shell.installer import install_powershell

    result = install_powershell()
    if result["success"]:
        click.echo(f"PowerShell hook installed at: {result['profile_path']}")
        click.echo("Restart PowerShell to activate.")
        if result.get("warning"):
            click.echo(f"Warning: {result['warning']}")
    else:
        click.echo(f"Failed: {result['error']}", err=True)
        sys.exit(1)


@init.command()
@click.option("--confirm", is_flag=True, help="Confirm CMD AutoRun registry modification.")
def cmd(confirm: bool) -> None:
    """Install CMD hook via AutoRun registry key."""
    if not confirm:
        click.echo("CMD AutoRun modifies a registry key that runs on every CMD start.")
        click.echo("Some antivirus software may flag this.")
        click.echo("Re-run with --confirm to proceed, or use PowerShell (recommended).")
        sys.exit(1)

    from termfix.shell.installer import install_cmd

    result = install_cmd()
    if result["success"]:
        click.echo("CMD hook installed via AutoRun registry key.")
    else:
        click.echo(f"Failed: {result['error']}", err=True)
        sys.exit(1)


@init.command()
def uninstall() -> None:
    """Remove all shell hooks."""
    from termfix.shell.installer import uninstall_cmd, uninstall_powershell

    ps_result = uninstall_powershell()
    cmd_result = uninstall_cmd()

    if ps_result["success"]:
        click.echo("PowerShell hook removed.")
    elif ps_result.get("error"):
        click.echo(f"PowerShell: {ps_result['error']}")

    if cmd_result["success"]:
        click.echo("CMD hook removed.")
    elif cmd_result.get("error"):
        click.echo(f"CMD: {cmd_result['error']}")


# ---------------------------------------------------------------------------
# Manual spell check
# ---------------------------------------------------------------------------


@main.command()
@click.argument("command")
def check(command: str) -> None:
    """Check a command name for spelling corrections."""
    from termfix.core.spellcheck import SpellChecker

    checker = SpellChecker()
    checker.scan_path()
    results = checker.check(command)

    if not results:
        click.echo(f"'{command}' is correct or not found in PATH.")
    else:
        click.echo(f"Did you mean:")
        for name, dist, path in results:
            click.echo(f"  {name}  (distance={dist})  [{path}]")


# ---------------------------------------------------------------------------
# PATH scan
# ---------------------------------------------------------------------------


@main.command()
def scan() -> None:
    """Force a fresh PATH scan and report results."""
    from termfix.core.spellcheck import SpellChecker

    checker = SpellChecker()
    count = checker.scan_path()
    click.echo(f"Scanned PATH: {count} executables found.")


# ---------------------------------------------------------------------------
# Jump (frecency directory navigation)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query", required=False)
def jump(query: str | None) -> None:
    """Navigate to a frecent directory (used by CMD DOSKEY 'j' macro)."""
    from termfix.config import TermfixConfig
    from termfix.core.frecency import FrecencyEngine
    from termfix.db.database import Database

    config = TermfixConfig()
    db = Database(config.data_dir / "data.db")
    db.initialize()
    engine = FrecencyEngine(db)

    if query:
        results = engine.query(query, limit=1)
        if results:
            path = results[0]["path"]
            click.echo(path)  # CMD DOSKEY will cd to this
        else:
            click.echo(f"No match for '{query}'", err=True)
            sys.exit(1)
    else:
        results = engine.get_top(limit=10)
        if results:
            for entry in results:
                score = f"{entry['score']:.0f}"
                click.echo(f"  {score:>6}  {entry['path']}")
        else:
            click.echo("No directory history yet.")

    db.close()


@main.command("cd-hook")
@click.argument("path")
def cd_hook(path: str) -> None:
    """Record a directory change (used by CMD DOSKEY)."""
    from termfix.config import TermfixConfig
    from termfix.core.frecency import FrecencyEngine
    from termfix.db.database import Database

    config = TermfixConfig()
    db = Database(config.data_dir / "data.db")
    db.initialize()
    engine = FrecencyEngine(db)
    engine.record_visit(path)
    db.close()


# ---------------------------------------------------------------------------
# Import history
# ---------------------------------------------------------------------------


@main.command("import-history")
def import_history() -> None:
    """Import PSReadLine command history to seed the database."""
    from termfix.config import TermfixConfig
    from termfix.db.database import Database
    from termfix.importers.psreadline import import_psreadline_history

    config = TermfixConfig()
    db = Database(config.data_dir / "data.db")
    db.initialize()

    stats = import_psreadline_history(db)
    click.echo(f"Imported {stats['commands']} commands, {stats['directories']} cd entries.")
    db.close()


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------


@main.group()
def config() -> None:
    """View or edit configuration."""


@config.command("show")
def config_show() -> None:
    """Show current configuration."""
    from termfix.config import TermfixConfig

    cfg = TermfixConfig()
    for key, val in cfg.model_dump().items():
        click.echo(f"  {key}: {val}")


@config.command("edit")
def config_edit() -> None:
    """Open config file in default editor."""
    from termfix.config import TermfixConfig

    cfg = TermfixConfig()
    toml_path = cfg.data_dir / "config.toml"
    if not toml_path.exists():
        cfg.ensure_data_dir()
        toml_path.write_text("# termfix configuration\n# See: termfix config show\n")

    click.launch(str(toml_path))
