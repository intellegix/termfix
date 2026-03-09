"""Shell hook installer/uninstaller for PowerShell and CMD."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

HOOK_MARKER_START = "# >>> termfix initialize >>>"
HOOK_MARKER_END = "# <<< termfix initialize <<<"


def _get_ps_hook_path() -> Path:
    """Get the path to the bundled PowerShell hook script."""
    return Path(__file__).parent / "powershell" / "termfix_hook.ps1"


def _get_cmd_hook_path() -> Path:
    """Get the path to the bundled CMD hook script."""
    return Path(__file__).parent / "cmd" / "termfix_hook.cmd"


def _get_ps_profile_path() -> str | None:
    """Query PowerShell for the $PROFILE path."""
    for ps_exe in ["pwsh", "powershell"]:
        try:
            result = subprocess.run(
                [ps_exe, "-NoProfile", "-Command", "echo $PROFILE"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _check_execution_policy() -> str | None:
    """Check PowerShell execution policy. Returns warning string if restrictive."""
    for ps_exe in ["pwsh", "powershell"]:
        try:
            result = subprocess.run(
                [ps_exe, "-NoProfile", "-Command", "Get-ExecutionPolicy -Scope CurrentUser"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                policy = result.stdout.strip().lower()
                if policy in ("restricted", "undefined"):
                    return (
                        f"Execution policy is '{policy}'. Scripts may not load. "
                        "Run: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
                    )
                return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return "Could not determine execution policy."


def install_powershell() -> dict[str, object]:
    """Install PowerShell hook into $PROFILE."""
    profile_path = _get_ps_profile_path()
    if not profile_path:
        return {"success": False, "error": "Could not find PowerShell $PROFILE path."}

    hook_path = _get_ps_hook_path()
    if not hook_path.exists():
        return {"success": False, "error": f"Hook script not found: {hook_path}"}

    warning = _check_execution_policy()

    profile = Path(profile_path)
    profile.parent.mkdir(parents=True, exist_ok=True)

    # Read existing profile
    existing = profile.read_text(encoding="utf-8") if profile.exists() else ""

    # Check if already installed
    if HOOK_MARKER_START in existing:
        return {
            "success": True,
            "profile_path": profile_path,
            "warning": warning,
            "already_installed": True,
        }

    # Append hook sourcing block
    # Use forward slashes in the dot-source path for PS compatibility
    hook_source = str(hook_path).replace("\\", "/")
    block = f"\n{HOOK_MARKER_START}\n. \"{hook_source}\"\n{HOOK_MARKER_END}\n"

    with open(profile, "a", encoding="utf-8") as f:
        f.write(block)

    return {
        "success": True,
        "profile_path": profile_path,
        "warning": warning,
    }


def uninstall_powershell() -> dict[str, object]:
    """Remove termfix hook from PowerShell $PROFILE."""
    profile_path = _get_ps_profile_path()
    if not profile_path:
        return {"success": True, "error": "No PowerShell profile found."}

    profile = Path(profile_path)
    if not profile.exists():
        return {"success": True}

    content = profile.read_text(encoding="utf-8")
    if HOOK_MARKER_START not in content:
        return {"success": True, "error": "Hook not found in profile."}

    # Remove the block between markers
    lines = content.split("\n")
    new_lines = []
    in_block = False
    for line in lines:
        if HOOK_MARKER_START in line:
            in_block = True
            continue
        if HOOK_MARKER_END in line:
            in_block = False
            continue
        if not in_block:
            new_lines.append(line)

    profile.write_text("\n".join(new_lines), encoding="utf-8")
    return {"success": True}


def install_cmd() -> dict[str, object]:
    """Install CMD hook via AutoRun registry key."""
    hook_path = _get_cmd_hook_path()
    if not hook_path.exists():
        return {"success": False, "error": f"Hook script not found: {hook_path}"}

    try:
        import winreg

        key_path = r"Software\Microsoft\Command Processor"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "AutoRun", 0, winreg.REG_SZ, str(hook_path))
        winreg.CloseKey(key)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def uninstall_cmd() -> dict[str, object]:
    """Remove CMD AutoRun registry key."""
    try:
        import winreg

        key_path = r"Software\Microsoft\Command Processor"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, "AutoRun")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
