"""JSON message protocol with length-prefixed framing for Named Pipe IPC."""

from __future__ import annotations

import json
import struct
from typing import Any, Literal

from pydantic import BaseModel

# 4-byte unsigned little-endian header
HEADER_FORMAT = "<I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


class Request(BaseModel):
    """Client → Daemon request."""

    type: Literal[
        "spell_check",
        "record_cd",
        "get_frecent_dirs",
        "record_command",
        "suggest_command",
        "ping",
        "scan_path",
    ]
    payload: dict[str, Any] = {}


class Response(BaseModel):
    """Daemon → Client response."""

    status: Literal["ok", "error"]
    data: dict[str, Any] = {}
    error: str | None = None

    @classmethod
    def ok(cls, **data: Any) -> Response:
        return cls(status="ok", data=data)

    @classmethod
    def err(cls, message: str) -> Response:
        return cls(status="error", error=message)


def encode_message(msg: BaseModel) -> bytes:
    """Encode a Pydantic model as length-prefixed JSON bytes."""
    payload = msg.model_dump_json().encode("utf-8")
    header = struct.pack(HEADER_FORMAT, len(payload))
    return header + payload


def decode_header(data: bytes) -> int:
    """Decode the 4-byte length header, returning payload size."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Header too short: got {len(data)} bytes, need {HEADER_SIZE}")
    (length,) = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return length


def decode_request(data: bytes) -> Request:
    """Decode JSON bytes into a Request."""
    return Request.model_validate(json.loads(data))


def decode_response(data: bytes) -> Response:
    """Decode JSON bytes into a Response."""
    return Response.model_validate(json.loads(data))
