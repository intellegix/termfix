"""Tests for DaemonServer.handle_request() — all 7 request types."""

from __future__ import annotations

import os

from termfix.daemon.protocol import (
    HEADER_SIZE,
    Request,
    decode_header,
    decode_response,
    encode_message,
)
from termfix.daemon.server import DaemonServer

# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_returns_pid(self, daemon_server: DaemonServer) -> None:
        req = Request(type="ping")
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"
        assert resp.data["pong"] is True
        assert resp.data["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# spell_check
# ---------------------------------------------------------------------------


class TestSpellCheck:
    def test_valid_typo(self, daemon_server: DaemonServer) -> None:
        req = Request(type="spell_check", payload={"command": "gti"})
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"
        names = [s["name"] for s in resp.data["suggestions"]]
        assert "git" in names

    def test_missing_command(self, daemon_server: DaemonServer) -> None:
        req = Request(type="spell_check", payload={})
        resp = daemon_server.handle_request(req)
        assert resp.status == "error"
        assert "missing" in resp.error.lower()

    def test_exact_match_returns_empty(self, daemon_server: DaemonServer) -> None:
        req = Request(type="spell_check", payload={"command": "git"})
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"
        assert resp.data["suggestions"] == []


# ---------------------------------------------------------------------------
# scan_path
# ---------------------------------------------------------------------------


class TestScanPath:
    def test_scan_path_returns_count(self, daemon_server: DaemonServer) -> None:
        req = Request(type="scan_path")
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"
        assert resp.data["executable_count"] > 0


# ---------------------------------------------------------------------------
# record_cd / get_frecent_dirs
# ---------------------------------------------------------------------------


class TestFrecency:
    def test_record_cd_valid(self, daemon_server: DaemonServer) -> None:
        req = Request(type="record_cd", payload={"path": "C:\\Users\\test"})
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"

    def test_record_cd_missing_path(self, daemon_server: DaemonServer) -> None:
        req = Request(type="record_cd", payload={})
        resp = daemon_server.handle_request(req)
        assert resp.status == "error"
        assert "missing" in resp.error.lower()

    def test_frecent_dirs_empty(self, daemon_server: DaemonServer) -> None:
        req = Request(type="get_frecent_dirs")
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"
        assert resp.data["directories"] == []

    def test_frecent_dirs_after_visits(self, daemon_server: DaemonServer) -> None:
        daemon_server.handle_request(
            Request(type="record_cd", payload={"path": "C:\\projects"})
        )
        daemon_server.handle_request(
            Request(type="record_cd", payload={"path": "C:\\docs"})
        )
        resp = daemon_server.handle_request(Request(type="get_frecent_dirs"))
        assert resp.status == "ok"
        paths = [d["path"] for d in resp.data["directories"]]
        assert "C:\\projects" in paths
        assert "C:\\docs" in paths

    def test_frecent_dirs_with_query(self, daemon_server: DaemonServer) -> None:
        daemon_server.handle_request(
            Request(type="record_cd", payload={"path": "C:\\projects\\alpha"})
        )
        daemon_server.handle_request(
            Request(type="record_cd", payload={"path": "C:\\docs\\beta"})
        )
        resp = daemon_server.handle_request(
            Request(type="get_frecent_dirs", payload={"query": "alpha"})
        )
        assert resp.status == "ok"
        paths = [d["path"] for d in resp.data["directories"]]
        assert any("alpha" in p for p in paths)


# ---------------------------------------------------------------------------
# record_command / suggest_command
# ---------------------------------------------------------------------------


class TestSuggest:
    def test_record_command_valid(self, daemon_server: DaemonServer) -> None:
        req = Request(
            type="record_command",
            payload={"command": "git status", "cwd": "C:\\projects", "exit_code": 0},
        )
        resp = daemon_server.handle_request(req)
        assert resp.status == "ok"

    def test_record_command_missing(self, daemon_server: DaemonServer) -> None:
        req = Request(type="record_command", payload={})
        resp = daemon_server.handle_request(req)
        assert resp.status == "error"
        assert "missing" in resp.error.lower()

    def test_suggest_after_recording(self, daemon_server: DaemonServer) -> None:
        daemon_server.handle_request(
            Request(
                type="record_command",
                payload={"command": "git status", "cwd": "C:\\proj"},
            )
        )
        # Flush in-memory cache to DB so suggest can find it
        daemon_server.suggest.flush_to_db()
        resp = daemon_server.handle_request(
            Request(type="suggest_command", payload={"partial": "git st"})
        )
        assert resp.status == "ok"
        commands = [s["command"] for s in resp.data["suggestions"]]
        assert "git status" in commands

    def test_suggest_missing_partial(self, daemon_server: DaemonServer) -> None:
        req = Request(type="suggest_command", payload={})
        resp = daemon_server.handle_request(req)
        assert resp.status == "error"
        assert "missing" in resp.error.lower()


# ---------------------------------------------------------------------------
# Unknown type (edge case via model_construct)
# ---------------------------------------------------------------------------


class TestUnknownType:
    def test_unknown_type_returns_error(self, daemon_server: DaemonServer) -> None:
        # Bypass Pydantic validation to inject an unknown type
        req = Request.model_construct(type="not_a_real_type", payload={})
        resp = daemon_server.handle_request(req)
        assert resp.status == "error"
        assert "unknown" in resp.error.lower()


# ---------------------------------------------------------------------------
# Full encode → decode → handle → encode → decode roundtrip
# ---------------------------------------------------------------------------


class TestFullRoundtrip:
    def test_end_to_end(self, daemon_server: DaemonServer) -> None:
        # Encode a request
        original_req = Request(type="ping")
        req_bytes = encode_message(original_req)

        # Decode it like the server would
        length = decode_header(req_bytes[:HEADER_SIZE])
        decoded_req = Request.model_validate_json(req_bytes[HEADER_SIZE : HEADER_SIZE + length])

        # Handle it
        resp = daemon_server.handle_request(decoded_req)

        # Encode the response
        resp_bytes = encode_message(resp)

        # Decode like the client would
        resp_length = decode_header(resp_bytes[:HEADER_SIZE])
        decoded_resp = decode_response(resp_bytes[HEADER_SIZE : HEADER_SIZE + resp_length])

        assert decoded_resp.status == "ok"
        assert decoded_resp.data["pong"] is True
