"""Daemon lifecycle management: start, stop, status, autostart."""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

from termfix import PIPE_NAME, SHUTDOWN_EVENT_NAME
from termfix.config import TermfixConfig
from termfix.daemon.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    Request,
    Response,
    decode_response,
    encode_message,
)

logger = logging.getLogger(__name__)

# Windows process creation flags
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _find_pythonw() -> Path:
    """Find pythonw.exe next to the current Python interpreter."""
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if pythonw.exists():
        return pythonw
    # Fallback: just use python.exe with creation flags to hide window
    return Path(sys.executable)


def _get_pid_path(config: TermfixConfig) -> Path:
    return config.data_dir / "daemon.pid"


def _send_pipe_request(request: Request, timeout_ms: int = 2000) -> Response | None:
    """Send a request to the daemon via Named Pipe. Returns None if daemon unreachable."""
    try:
        import pywintypes
        import win32file
        import win32pipe

        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )

        # Set pipe to message mode
        win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_BYTE, None, None
        )

        # Send request
        request_bytes = encode_message(request)
        win32file.WriteFile(handle, request_bytes)

        # Read response header
        _, header_data = win32file.ReadFile(handle, HEADER_SIZE)
        payload_size = struct.unpack(HEADER_FORMAT, header_data)[0]

        # Read response payload
        _, payload_data = win32file.ReadFile(handle, payload_size)
        win32file.CloseHandle(handle)

        return decode_response(payload_data)

    except Exception as e:
        logger.debug("Pipe request failed: %s", e)
        return None


def start(config: TermfixConfig | None = None) -> bool:
    """Start the daemon as a background process. Returns True if started."""
    config = config or TermfixConfig()
    config.ensure_data_dir()

    # Check if already running
    if is_running(config):
        logger.info("Daemon is already running")
        return True

    pythonw = _find_pythonw()
    pid_path = _get_pid_path(config)

    # Build command: pythonw -m termfix.daemon.server
    cmd = [
        str(pythonw),
        "-m", "termfix.daemon.server",
    ]

    creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            cmd,
            creationflags=creation_flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        pid_path.write_text(str(proc.pid))
        logger.info("Daemon started (PID %d)", proc.pid)

        # Wait briefly and verify it's responding
        time.sleep(0.5)
        for _ in range(5):
            if is_running(config):
                return True
            time.sleep(0.3)

        logger.warning("Daemon process started but not responding to ping")
        return True

    except Exception as e:
        logger.error("Failed to start daemon: %s", e)
        return False


def stop(config: TermfixConfig | None = None) -> bool:
    """Stop the daemon via shutdown event. Returns True if stopped."""
    config = config or TermfixConfig()

    try:
        import win32event

        # Signal the shutdown event
        event = win32event.OpenEvent(
            win32event.EVENT_MODIFY_STATE, False, SHUTDOWN_EVENT_NAME
        )
        win32event.SetEvent(event)
        win32event.CloseHandle(event)
        logger.info("Shutdown event sent")

        # Wait for daemon to stop
        for _ in range(10):
            time.sleep(0.3)
            if not is_running(config):
                # Clean up PID file
                pid_path = _get_pid_path(config)
                pid_path.unlink(missing_ok=True)
                logger.info("Daemon stopped")
                return True

        logger.warning("Daemon did not stop within timeout")
        return False

    except Exception as e:
        logger.debug("Could not open shutdown event: %s", e)
        # Fallback: try to kill by PID
        return _kill_by_pid(config)


def _kill_by_pid(config: TermfixConfig) -> bool:
    """Last-resort: kill daemon by PID file."""
    pid_path = _get_pid_path(config)
    if not pid_path.exists():
        return True

    try:
        pid = int(pid_path.read_text().strip())
        import signal

        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        pid_path.unlink(missing_ok=True)
        return True
    except (ProcessLookupError, ValueError):
        pid_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.error("Failed to kill daemon: %s", e)
        return False


def status(config: TermfixConfig | None = None) -> dict[str, object]:
    """Get daemon status info."""
    config = config or TermfixConfig()
    pid_path = _get_pid_path(config)

    pid: int | None = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
        except (ValueError, OSError):
            pass

    response = _send_pipe_request(Request(type="ping"), timeout_ms=500)

    if response and response.status == "ok":
        return {
            "running": True,
            "pid": response.data.get("pid", pid),
            "uptime": response.data.get("uptime"),
        }

    return {
        "running": False,
        "pid": pid,
        "stale_pid": pid is not None,
    }


def is_running(config: TermfixConfig | None = None) -> bool:
    """Quick check: is the daemon responding to pings?"""
    response = _send_pipe_request(Request(type="ping"), timeout_ms=500)
    return response is not None and response.status == "ok"


def autostart_enable(config: TermfixConfig | None = None) -> bool:
    """Register termfix daemon as a scheduled task to start on login."""
    config = config or TermfixConfig()
    pythonw = _find_pythonw()
    task_name = "Termfix Daemon"

    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", f'"{pythonw}" -m termfix.daemon.server',
        "/sc", "ONLOGON",
        "/delay", "0000:10",  # 10-second delay after login
        "/rl", "LIMITED",
        "/f",  # force overwrite existing
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Autostart task created")
            return True
        logger.error("schtasks failed: %s", result.stderr)
        return False
    except Exception as e:
        logger.error("Failed to create autostart task: %s", e)
        return False


def autostart_disable() -> bool:
    """Remove the termfix daemon scheduled task."""
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", "Termfix Daemon", "/f"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error("Failed to remove autostart task: %s", e)
        return False
