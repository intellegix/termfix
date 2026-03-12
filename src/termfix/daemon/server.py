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

# Timeouts
CLIENT_IO_TIMEOUT_MS = 5000  # 5 seconds for client read/write operations

# Mutex name for single-instance enforcement
DAEMON_MUTEX_NAME = "Global\\TermfixDaemonMutex"


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
        """Acquire named mutex for single-instance enforcement.

        Uses a Windows named mutex instead of a lock file to avoid
        TOCTOU race conditions. The mutex is automatically released
        when the process exits, even on crash.
        """
        import win32api
        import win32event
        import winerror

        try:
            self._mutex = win32event.CreateMutex(None, True, DAEMON_MUTEX_NAME)
            last_error = win32api.GetLastError()
            if last_error == winerror.ERROR_ALREADY_EXISTS:
                # Another daemon owns the mutex
                win32api.CloseHandle(self._mutex)
                self._mutex = None
                logger.error("Another daemon instance is already running")
                return False
            # Also write PID file for status/diagnostics (non-authoritative)
            self._pid_path = self.config.data_dir / "daemon.pid"
            try:
                self._pid_path.write_text(str(os.getpid()))
            except OSError:
                pass
            return True
        except Exception as e:
            logger.error("Failed to acquire mutex: %s", e)
            return False

    def _release_lock(self) -> None:
        """Release named mutex and clean up PID file."""
        import win32api
        import win32event

        try:
            if hasattr(self, "_mutex") and self._mutex is not None:
                win32event.ReleaseMutex(self._mutex)
                win32api.CloseHandle(self._mutex)
                self._mutex = None
        except Exception:
            pass
        try:
            if hasattr(self, "_pid_path"):
                self._pid_path.unlink(missing_ok=True)
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

    @staticmethod
    def _create_pipe_security_attributes() -> object:
        """Create SECURITY_ATTRIBUTES restricting pipe access to the current user.

        Returns a pywin32 SECURITY_ATTRIBUTES object with a DACL that grants
        full access only to the current user's SID, blocking other local users.
        """
        import ntsecuritycon as con
        import win32api
        import win32security

        # Get the current user's SID
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32security.TOKEN_QUERY,
        )
        user_sid = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )[0]
        win32api.CloseHandle(token)

        # Build a DACL granting only the current user full pipe access
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            con.FILE_ALL_ACCESS,
            user_sid,
        )

        # Create security descriptor with the DACL
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, dacl, False)

        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False

        return sa

    def run(self) -> None:
        """Run the daemon server (blocking). Called in the background process."""
        import pywintypes
        import win32api
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

        # Create pipe security attributes (DACL restricting to current user)
        try:
            pipe_sa = self._create_pipe_security_attributes()
        except Exception:
            logger.warning("Failed to create pipe DACL, using default security")
            pipe_sa = None

        logger.info("Daemon starting (PID %d)", os.getpid())
        self._running = True

        try:
            while self._running:
                # Create pipe instance with DACL security
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
                    pipe_sa,
                )

                # Create overlapped event for this iteration
                connect_event = win32event.CreateEvent(None, True, False, None)
                try:
                    overlapped = pywintypes.OVERLAPPED()
                    overlapped.hEvent = connect_event

                    try:
                        win32pipe.ConnectNamedPipe(pipe_handle, overlapped)
                    except pywintypes.error as e:
                        if e.winerror != 997:  # ERROR_IO_PENDING
                            raise

                    # Wait for either a client connection or shutdown
                    result = win32event.WaitForMultipleObjects(
                        [connect_event, shutdown_event],
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
                finally:
                    # Always close the connect event handle (fixes HIGH #4 handle leak)
                    win32api.CloseHandle(connect_event)

        except Exception:
            logger.exception("Fatal daemon error")
        finally:
            self._running = False
            self.suggest.flush_to_db()
            self.db.close()
            self._release_lock()
            win32api.CloseHandle(shutdown_event)
            logger.info("Daemon stopped")

    def _read_with_timeout(
        self, pipe_handle: int, num_bytes: int, timeout_ms: int
    ) -> bytes | None:
        """Read from pipe using overlapped I/O with a timeout.

        Returns the data read, or None if the operation timed out.
        """
        import pywintypes
        import win32api
        import win32event
        import win32file

        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                hr, data = win32file.ReadFile(pipe_handle, num_bytes, overlapped)
            except pywintypes.error as e:
                if e.winerror != 997:  # ERROR_IO_PENDING
                    raise
                hr = 997

            if hr == 997:  # IO pending — wait with timeout
                result = win32event.WaitForSingleObject(
                    overlapped.hEvent, timeout_ms
                )
                if result == win32event.WAIT_TIMEOUT:
                    # Cancel the pending I/O
                    try:
                        win32file.CancelIo(pipe_handle)
                    except Exception:
                        pass
                    return None
                # Get the result after wait
                n_bytes = win32file.GetOverlappedResult(pipe_handle, overlapped, True)
                # For pending reads, data is in the overlapped buffer
                # Re-read from the completed overlapped operation
                return bytes(overlapped.object) if hasattr(overlapped, 'object') else data[:n_bytes]
            else:
                return bytes(data)
        finally:
            win32api.CloseHandle(overlapped.hEvent)

    def _write_with_timeout(
        self, pipe_handle: int, data: bytes, timeout_ms: int
    ) -> bool:
        """Write to pipe using overlapped I/O with a timeout.

        Returns True on success, False on timeout.
        """
        import pywintypes
        import win32api
        import win32event
        import win32file

        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                hr, _ = win32file.WriteFile(pipe_handle, data, overlapped)
            except pywintypes.error as e:
                if e.winerror != 997:  # ERROR_IO_PENDING
                    raise
                hr = 997

            if hr == 997:  # IO pending — wait with timeout
                result = win32event.WaitForSingleObject(
                    overlapped.hEvent, timeout_ms
                )
                if result == win32event.WAIT_TIMEOUT:
                    try:
                        win32file.CancelIo(pipe_handle)
                    except Exception:
                        pass
                    return False
                win32file.GetOverlappedResult(pipe_handle, overlapped, True)
            return True
        finally:
            win32api.CloseHandle(overlapped.hEvent)

    def _handle_client(self, pipe_handle: int) -> None:
        """Read request from pipe, process it, write response.

        All I/O uses overlapped operations with CLIENT_IO_TIMEOUT_MS timeout
        to prevent a malicious or buggy client from blocking the daemon.
        """
        import win32file
        import win32pipe

        try:
            # Read header (4 bytes) with timeout
            header_data = self._read_with_timeout(
                pipe_handle, HEADER_SIZE, CLIENT_IO_TIMEOUT_MS
            )
            if header_data is None:
                logger.warning("Client read timed out on header")
                return
            if len(header_data) < HEADER_SIZE:
                return

            payload_size = struct.unpack(HEADER_FORMAT, header_data)[0]
            if payload_size > MAX_MESSAGE_SIZE:
                response = Response.err("message too large")
            else:
                # Read payload with timeout
                payload_data = self._read_with_timeout(
                    pipe_handle, payload_size, CLIENT_IO_TIMEOUT_MS
                )
                if payload_data is None:
                    logger.warning("Client read timed out on payload")
                    return
                request = decode_request(payload_data)
                response = self.handle_request(request)

            # Write response with timeout
            response_bytes = encode_message(response)
            if not self._write_with_timeout(
                pipe_handle, response_bytes, CLIENT_IO_TIMEOUT_MS
            ):
                logger.warning("Client write timed out")
                return
            win32file.FlushFileBuffers(pipe_handle)

        except Exception as e:
            logger.error("Client handling error: %s", e)
            try:
                err_response = encode_message(Response.err(str(e)))
                self._write_with_timeout(
                    pipe_handle, err_response, CLIENT_IO_TIMEOUT_MS
                )
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
