"""Unit tests for wire_protocol.py — pure encode/decode, no sockets."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wire_protocol import (  # noqa: E402
    DEFAULT_PORT,
    KNOWN_COMMANDS,
    Chunk,
    Command,
    Response,
    decode_message,
    encode_chunk,
    encode_command,
    encode_error,
    encode_ok,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_default_port_is_9999() -> None:
    assert DEFAULT_PORT == 9999


def test_known_commands_set() -> None:
    expected = {
        "connect", "disconnect", "set_source_voltage", "set_stop_mode",
        "get_modifiers", "start_measuring", "stop_measuring",
    }
    assert KNOWN_COMMANDS == expected


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def test_encode_command_simple() -> None:
    line = encode_command("connect")
    assert line.endswith(b"\n")
    assert json.loads(line) == {"cmd": "connect"}


def test_encode_command_with_params() -> None:
    line = encode_command("set_source_voltage", mV=3300)
    assert json.loads(line) == {"cmd": "set_source_voltage", "mV": 3300}


def test_encode_command_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown command"):
        encode_command("flux_capacitor")


def test_encode_ok_with_data() -> None:
    line = encode_ok({"mV": 3300})
    assert json.loads(line) == {"ok": True, "data": {"mV": 3300}}


def test_encode_ok_default_data() -> None:
    line = encode_ok()
    assert json.loads(line) == {"ok": True, "data": {}}


def test_encode_error() -> None:
    line = encode_error("voltage out of range")
    assert json.loads(line) == {"ok": False, "error": "voltage out of range"}


def test_encode_chunk_basic() -> None:
    samples = [(0, 50_000.0, 3.3, 0), (10, 85_000.0, 3.3, 1)]
    line = encode_chunk(samples)
    parsed = json.loads(line)
    assert parsed == {
        "chunk": [
            [0, 50000.0, 3.3, 0],
            [10, 85000.0, 3.3, 1],
        ]
    }


def test_encode_chunk_rounds_to_3_decimals() -> None:
    samples = [(0, 50_000.123456, 3.3, 0)]
    line = encode_chunk(samples)
    assert json.loads(line) == {"chunk": [[0, 50000.123, 3.3, 0]]}


def test_encode_chunk_empty() -> None:
    """Empty chunks are legal on the wire (server may skip them)."""
    line = encode_chunk([])
    assert json.loads(line) == {"chunk": []}


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def test_decode_command() -> None:
    msg = decode_message(b'{"cmd":"connect"}\n')
    assert isinstance(msg, Command)
    assert msg.cmd == "connect"
    assert msg.params == {}


def test_decode_command_with_params() -> None:
    msg = decode_message(b'{"cmd":"set_source_voltage","mV":3300}')
    assert isinstance(msg, Command)
    assert msg.cmd == "set_source_voltage"
    assert msg.params == {"mV": 3300}


def test_decode_response_ok() -> None:
    msg = decode_message(b'{"ok":true,"data":{"mV":3300}}')
    assert isinstance(msg, Response)
    assert msg.ok is True
    assert msg.data == {"mV": 3300}
    assert msg.error is None


def test_decode_response_error() -> None:
    msg = decode_message(b'{"ok":false,"error":"oops"}')
    assert isinstance(msg, Response)
    assert msg.ok is False
    assert msg.error == "oops"


def test_decode_chunk() -> None:
    msg = decode_message(b'{"chunk":[[0,50000.0,3.3,0],[10,85000.0,3.3,1]]}')
    assert isinstance(msg, Chunk)
    assert msg.samples == [(0, 50000.0, 3.3, 0), (10, 85000.0, 3.3, 1)]


def test_decode_string_input() -> None:
    """decode_message accepts str as well as bytes."""
    msg = decode_message('{"cmd":"connect"}')
    assert isinstance(msg, Command)


# ---------------------------------------------------------------------------
# Decoding error paths
# ---------------------------------------------------------------------------

def test_decode_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        decode_message(b"\n")


def test_decode_rejects_bad_json() -> None:
    with pytest.raises(ValueError, match="bad JSON"):
        decode_message(b'{not json')


def test_decode_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="object"):
        decode_message(b'42')


def test_decode_rejects_unrecognized() -> None:
    with pytest.raises(ValueError, match="discriminator"):
        decode_message(b'{"foo":"bar"}')


def test_decode_rejects_bad_cmd_type() -> None:
    with pytest.raises(ValueError, match="cmd must be string"):
        decode_message(b'{"cmd":42}')


def test_decode_rejects_bad_chunk_row() -> None:
    with pytest.raises(ValueError, match="4-element"):
        decode_message(b'{"chunk":[[0,1,2]]}')


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_roundtrip_command() -> None:
    line = encode_command("set_stop_mode", enabled=True)
    msg = decode_message(line)
    assert isinstance(msg, Command)
    assert msg.cmd == "set_stop_mode"
    assert msg.params == {"enabled": True}


def test_roundtrip_ok_response() -> None:
    line = encode_ok({"hwver": "mock"})
    msg = decode_message(line)
    assert isinstance(msg, Response)
    assert msg.ok is True
    assert msg.data == {"hwver": "mock"}


def test_roundtrip_error_response() -> None:
    line = encode_error("not connected")
    msg = decode_message(line)
    assert isinstance(msg, Response)
    assert msg.ok is False
    assert msg.error == "not connected"


def test_roundtrip_chunk() -> None:
    samples = [(0, 50_000.0, 3.3, 0), (10, 85_500.5, 3.3, 1)]
    line = encode_chunk(samples)
    msg = decode_message(line)
    assert isinstance(msg, Chunk)
    assert msg.samples == samples
