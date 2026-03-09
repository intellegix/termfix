"""Named Pipe server with request routing for the termfix daemon."""

from __future__ import annotations

import logging
import os
import struct
import sys
import time
from pathlib import Path

from termfix import PIPE_NAME, SHUTDOWN_EVENT_NAME
from termfix.config import TermfixConfig
from termfix.core.frecency import FrecencyEngine
from termfix.core.spellcheck import SpellChecker
from termfix.core.suggest import SuggestEngine
from termfix.daemon.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    Request,
    Response,
    decode_request,
    encode_message,
)
from termfix.db.database import Database

logger = logging.getLogger(__name__)

# Buffer sizes
PIPE_BUFFER_SIZE = 65536
MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB


class DaemonServer:
    """Named Pipe daemon server for termfix."""

    def __init__(self, config: TermfixConfig | None = None) -> None:
        self.config = config or TermfixConfig()
        self._running = False

        # Initialize subsystems
        data_dir = self.config.ensure_data_dir()
        db_path = data_dir / "data.db"
        self.db = Database(db_path)
        self.db.initialize()

        self.spellcheck = SpellChecker(
            max_distance=self.config.spell_max_distance,
            scan_extensions=self.config.spell_scan_extensions,
            custom_commands=self.config.spell_custom_commands,
        )
        self.frecency = FrecencyEngine(
            self.db, aging_threshold=self.config.frecency_aging_threshold
        )
        self.suggest = SuggestEngine(
            self.db,
            cache_size=self.config.suggest_cache_size,
            cache_ttl=self.config.suggest_cache_ttl_seconds,
            min_score=self.config.suggest_min_score,
        )

        # Initial PATH scan
        self.spellcheck.scan_path()

    def _acquire_lock(self) -> bool:
        """Acquire daemon lock file. Returns False if another instance is running."""
        self._lock_path = self.config.data_dir / "daemon.lock"
        try:
            if self._lock_path.exists():
                # Check if the PID in the lock is still alive
                try:
                    old_pid = int(self._lock_path.read_text().strip())
                    # On Windows, check if process exists
                    import ctypes

                    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                    handle = kernel32.OpenProcess(0x1000, False, old_pid)  # PROCESS_QUERY_LIMITED
                    if handle:
                        kernel32.CloseHandle(handle)
                        logger.error("Another daemon is running (PID %d)", old_pid)
                        return False
                except (ValueError, OSError):
                    pass
                # Stale lock — remove it
                self._lock_path.unlink(missing_ok=True)

            self._lock_path.write_text(str(os.getpid()))
            return True
        except OSError as e:
            logger.error("Failed to acquire lock: %s", e)
            return False

    def _release_lock(self) -> None:
        """Release daemon lock file."""
        try:
            if hasattr(self, "_lock_path"):
                self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def handle_request(self, request: Request) -> Response:
        """Route a request to the appropriate handler."""
        try:
            match request.type:
                case "ping":
                    return Response.ok(pong=True, pid=os.getpid(), uptime=time.time())

                case "spell_check":
                    command = request.payload.get("command", "")
                    if not command:
                        return Response.err("missing 'command' in payload")
                    results = self.spellcheck.check(command)
                    return Response.ok(
                        suggestions=[
                            {"name": name, "distance": dist, "path": path}
                            for name, dist, path in results
                        ]
                    )

                case "scan_path":
                    count = self.spellcheck.scan_path()
                    return Response.ok(executable_count=count)

                case "record_cd":
                    path = request.payload.get("path", "")
                    if not path:
                        return Response.err("missing 'path' in payload")
                    self.frecency.record_visit(path)
                    return Response.ok()

                case "get_frecent_dirs":
                    query = request.payload.get("query")
                    limit = request.payload.get("limit", 10)
                    if query:
                        dirs = self.frecency.query(query, limit=limit)
                    else:
                        dirs = self.frecency.get_top(limit=limit)
                    return Response.ok(directories=dirs)

                case "record_command":
                    command = request.payload.get("command", "")
                    if not command:
                        return Response.err("missing 'command' in payload")
                    cwd = request.payload.get("cwd")
                    exit_code = request.payload.get("exit_code")
                    self.suggest.record(command, cwd=cwd, exit_code=exit_code)
                    return Response.ok()

                case "suggest_command":
                    partial = request.payload.get("partial", "")
                    if not partial:
                        return Response.err("missing 'partial' in payload")
                    limit = request.payload.get("limit", 5)
                    suggestions = self.suggest.suggest(partial, limit=limit)
                    return Response.ok(
                        suggestions=[
                            {"command": cmd, "score": score}
                            for cmd, score in suggestions
                        ]
                    )

                case _:
                    return Response.err(f"unknown request type: {request.type}")

        except Exception as e:
            logger.exception("Error handling request type=%s", request.type)
            return Response.err(str(e))

    def run(self) -> None:
        """Run the daemon server (blocking). Called in the background process."""
        import pywintypes
        import win32event
        import win32file
        import win32pipe

        # Set UTF-8 mode
        os.environ["PYTHONUTF8"] = "1"

        # Configure logging
        log_path = self.config.data_dir / "termfix.log"
        logging.basicConfig(
            level=getattr(logging, self.config.daemon_log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(str(log_path), encoding="utf-8"),
            ],
        )

        if not self._acquire_lock():
            logger.error("Cannot start: another daemon instance is running")
            sys.exit(1)

        # Create shutdown event
        shutdown_event = win32event.CreateEvent(None, True, False, SHUTDOWN_EVENT_NAME)

        logger.info("Daemon starting (PID %d)", os.getpid())
        self._running = True

        try:
            while self._running:
                # Create pipe instance
                pipe_handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    (
                        win32pipe.PIPE_ACCESS_DUPLEX
                        | win32file.FILE_FLAG_OVERLAPPED
                    ),
                    (
                        win32pipe.PIPE_TYPE_BYTE
                        | win32pipe.PIPE_READMODE_BYTE
                        | win32pipe.PIPE_WAIT
                        | win32pipe.PIPE_REJECT_REMOTE_CLIENTS
                    ),
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    0,
                    None,
                )

                try:
                    # Overlapped connect so we can check shutdown event
                    overlapped = pywintypes.OVERLAPPED()
                    overlapped.hEvent = win32event.CreateEvent(None, True, False, None)

                    try:
                        win32pipe.ConnectNamedPipe(pipe_handle, overlapped)
                    except pywintypes.error as e:
                        if e.winerror != 997:  # ERROR_IO_PENDING
                            raise

                    # Wait for either a client connection or shutdown
                    result = win32event.WaitForMultipleObjects(
                        [overlapped.hEvent, shutdown_event],
                        False,  # wait for any
                        5000,   # 5 second timeout for periodic maintenance
                    )

                    if result == win32event.WAIT_OBJECT_0 + 1:
                        # Shutdown event signaled
                        logger.info("Shutdown event received")
                        win32pipe.DisconnectNamedPipe(pipe_handle)
                        win32file.CloseHandle(pipe_handle)
                        break
                    elif result == win32event.WAIT_TIMEOUT:
                        # Timeout — do maintenance, loop again
                        win32pipe.DisconnectNamedPipe(pipe_handle)
                        win32file.CloseHandle(pipe_handle)
                        continue
                    elif result == win32event.WAIT_OBJECT_0:
                        # Client connected — handle request
                        self._handle_client(pipe_handle)
                    else:
                        logger.warning("Unexpected wait result: %s", result)
                        win32pipe.DisconnectNamedPipe(pipe_handle)
                        win32file.CloseHandle(pipe_handle)

                except pywintypes.error as e:
                    logger.error("Pipe error: %s", e)
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass

        except Exception:
            logger.exception("Fatal daemon error")
        finally:
            self._running = False
            self.suggest.flush_to_db()
            self.db.close()
            self._release_lock()
            win32event.CloseHandle(shutdown_event)
            logger.info("Daemon stopped")

    def _handle_client(self, pipe_handle: int) -> None:
        """Read request from pipe, process it, write response."""
        import win32file
        import win32pipe

        try:
            # Read header (4 bytes)
            _, header_data = win32file.ReadFile(pipe_handle, HEADER_SIZE)
            if len(header_data) < HEADER_SIZE:
                return

            payload_size = struct.unpack(HEADER_FORMAT, header_data)[0]
            if payload_size > MAX_MESSAGE_SIZE:
                response = Response.err("message too large")
            else:
                # Read payload
                _, payload_data = win32file.ReadFile(pipe_handle, payload_size)
                request = decode_request(payload_data)
                response = self.handle_request(request)

            # Write response
            response_bytes = encode_message(response)
            win32file.WriteFile(pipe_handle, response_bytes)
            win32file.FlushFileBuffers(pipe_handle)

        except Exception as e:
            logger.error("Client handling error: %s", e)
            try:
                err_response = encode_message(Response.err(str(e)))
                win32file.WriteFile(pipe_handle, err_response)
            except Exception:
                pass
        finally:
            try:
                win32pipe.DisconnectNamedPipe(pipe_handle)
                win32file.CloseHandle(pipe_handle)
            except Exception:
                pass


def run_daemon(config: TermfixConfig | None = None) -> None:
    """Entry point for the daemon process."""
    server = DaemonServer(config=config)
    server.run()
