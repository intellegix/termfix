"""Tests for the Named Pipe protocol encoding/decoding layer."""

from __future__ import annotations

import json
import struct

import pytest
from pydantic import ValidationError

from termfix.daemon.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    Request,
    Response,
    decode_header,
    decode_request,
    decode_response,
    encode_message,
)

# ---------------------------------------------------------------------------
# encode_message
# ---------------------------------------------------------------------------


class TestEncodeMessage:
    def test_encode_request(self) -> None:
        req = Request(type="ping")
        raw = encode_message(req)
        header = raw[:HEADER_SIZE]
        payload = raw[HEADER_SIZE:]
        (length,) = struct.unpack(HEADER_FORMAT, header)
        assert length == len(payload)
        body = json.loads(payload)
        assert body["type"] == "ping"
        assert body["payload"] == {}

    def test_encode_response_ok(self) -> None:
        resp = Response.ok(count=42)
        raw = encode_message(resp)
        payload = raw[HEADER_SIZE:]
        body = json.loads(payload)
        assert body["status"] == "ok"
        assert body["data"]["count"] == 42
        assert body["error"] is None

    def test_encode_response_err(self) -> None:
        resp = Response.err("boom")
        raw = encode_message(resp)
        payload = raw[HEADER_SIZE:]
        body = json.loads(payload)
        assert body["status"] == "error"
        assert body["error"] == "boom"

    def test_header_is_4_bytes_le(self) -> None:
        req = Request(type="ping")
        raw = encode_message(req)
        expected_len = len(raw) - HEADER_SIZE
        assert struct.unpack("<I", raw[:4])[0] == expected_len


# ---------------------------------------------------------------------------
# decode_header
# ---------------------------------------------------------------------------


class TestDecodeHeader:
    def test_valid_header(self) -> None:
        header = struct.pack(HEADER_FORMAT, 256)
        assert decode_header(header) == 256

    def test_short_header_raises(self) -> None:
        with pytest.raises(ValueError, match="Header too short"):
            decode_header(b"\x00\x01")

    def test_zero_length(self) -> None:
        header = struct.pack(HEADER_FORMAT, 0)
        assert decode_header(header) == 0

    def test_large_length(self) -> None:
        header = struct.pack(HEADER_FORMAT, 2**32 - 1)
        assert decode_header(header) == 2**32 - 1


# ---------------------------------------------------------------------------
# decode_request
# ---------------------------------------------------------------------------


class TestDecodeRequest:
    def test_valid_ping(self) -> None:
        data = json.dumps({"type": "ping"}).encode()
        req = decode_request(data)
        assert req.type == "ping"
        assert req.payload == {}

    def test_spell_check_with_payload(self) -> None:
        data = json.dumps({"type": "spell_check", "payload": {"command": "gti"}}).encode()
        req = decode_request(data)
        assert req.type == "spell_check"
        assert req.payload["command"] == "gti"

    def test_invalid_type_raises(self) -> None:
        data = json.dumps({"type": "not_a_real_type"}).encode()
        with pytest.raises(ValidationError):
            decode_request(data)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            decode_request(b"not json")


# ---------------------------------------------------------------------------
# decode_response
# ---------------------------------------------------------------------------


class TestDecodeResponse:
    def test_ok_response(self) -> None:
        data = json.dumps({"status": "ok", "data": {"v": 1}}).encode()
        resp = decode_response(data)
        assert resp.status == "ok"
        assert resp.data["v"] == 1

    def test_error_response(self) -> None:
        data = json.dumps({"status": "error", "error": "bad"}).encode()
        resp = decode_response(data)
        assert resp.status == "error"
        assert resp.error == "bad"


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_request_roundtrip(self) -> None:
        original = Request(type="spell_check", payload={"command": "noed"})
        raw = encode_message(original)
        length = decode_header(raw[:HEADER_SIZE])
        decoded = decode_request(raw[HEADER_SIZE : HEADER_SIZE + length])
        assert decoded.type == original.type
        assert decoded.payload == original.payload

    def test_response_roundtrip(self) -> None:
        original = Response.ok(suggestions=["node", "npm"])
        raw = encode_message(original)
        length = decode_header(raw[:HEADER_SIZE])
        decoded = decode_response(raw[HEADER_SIZE : HEADER_SIZE + length])
        assert decoded.status == original.status
        assert decoded.data == original.data

    def test_error_response_roundtrip(self) -> None:
        original = Response.err("something failed")
        raw = encode_message(original)
        length = decode_header(raw[:HEADER_SIZE])
        decoded = decode_response(raw[HEADER_SIZE : HEADER_SIZE + length])
        assert decoded.status == "error"
        assert decoded.error == "something failed"
