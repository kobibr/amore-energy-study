"""TCP/JSON wire protocol for the Mock PPK2.

Encodes the spec §6 line-protocol for command/response and chunked sample
streaming between ``mock_ppk2_server.py`` and ``mock_ppk2_client.py``.

Wire format
-----------

Messages are newline-delimited JSON (NDJSON). Each message is a JSON
object on a single line, terminated by ``\\n``. UTF-8 throughout. Both
sides exchange messages over a single bidirectional TCP stream.

Message types
-------------

**Command** (client → server)::

    {"cmd": "connect"}
    {"cmd": "set_source_voltage", "mV": 3300}
    {"cmd": "set_stop_mode", "enabled": true}
    {"cmd": "get_modifiers"}
    {"cmd": "start_measuring"}
    {"cmd": "stop_measuring"}
    {"cmd": "disconnect"}

**Response** (server → client). Always carries an ``ok`` field; ``data``
field is per-command (see below)::

    {"ok": true,  "data": <command-dependent>}
    {"ok": false, "error": "<human-readable message>"}

**Sample chunk** (server → client, only between start and stop)::

    {"chunk": [[ts0, i0, v0, gb0], [ts1, i1, v1, gb1], ...]}

Each chunk row is ``[timestamp_us, current_uA, voltage_V, gpio_byte]``,
matching the column order of ``csv_format.py``. Field types: int, float,
float, int. Chunks are emitted at server discretion (size and rate); the
client must accept any chunk size from 1 sample upward.

Per-command response data
-------------------------

* ``connect``            → ``data: {"hwver": "..."}``    (informational)
* ``set_source_voltage`` → ``data: {"mV": <echoed>}``
* ``set_stop_mode``      → ``data: {"enabled": <echoed>}``
* ``get_modifiers``      → ``data: {<calibration dict>}``  (mock returns
                            a constant dict; real PPK2 returns
                            calibration constants)
* ``start_measuring``    → ``data: {}`` then chunks begin streaming
* ``stop_measuring``     → ``data: {"total_samples": <int>}``  (mock-only
                            field; the upstream PPK2_API.stop_measuring
                            returns None — see API reconciliation note
                            #2 in the conversation log)
* ``disconnect``         → ``data: {}``

Rationale for the design
------------------------

* **JSON over binary** because the wire volume is dominated by sample
  chunks; a single chunk can hold thousands of samples in one frame
  with negligible overhead, and JSON makes debugging trivial (``tcpdump
  -A`` or ``nc`` works directly). Real PPK2 USB protocol is binary; we
  pay a ~3× wire size penalty here in exchange for inspectability,
  which is acceptable on a LAN at ~100 ksps × ~30 bytes/sample ≈
  3 MB/s vs gigabit available.

* **Newline-delimited** (not length-prefixed) because (a) trivial to
  read with ``socket.makefile().readline()``, (b) trivial to parse with
  ``json.loads``, (c) survives ``cat | nc`` debugging unchanged.

* **Per-command response** keeps the protocol synchronous. Sample
  chunks are an exception: they flow asynchronously between
  ``start_measuring`` and ``stop_measuring``, so the client must
  multiplex its read loop. See ``mock_ppk2_client.py`` for the
  read-loop implementation.

This module is **transport-free**: it only encodes/decodes messages
and validates their shape. The actual TCP socket I/O lives in the
server and client modules.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default port for the mock server. Spec §6 — fixed at 9999.
DEFAULT_PORT: int = 9999

#: All commands the protocol recognizes.
KNOWN_COMMANDS: frozenset[str] = frozenset({
    "connect",
    "set_source_voltage",
    "set_stop_mode",
    "get_modifiers",
    "start_measuring",
    "stop_measuring",
    "disconnect",
})


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_command(cmd: str, **params: Any) -> bytes:
    """Encode a command into a single NDJSON line (with trailing \\n).

    Raises ValueError for unknown command names.
    """
    if cmd not in KNOWN_COMMANDS:
        raise ValueError(f"unknown command: {cmd!r}")
    msg = {"cmd": cmd, **params}
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def encode_ok(data: dict[str, Any] | None = None) -> bytes:
    """Encode a successful response with optional ``data`` payload."""
    msg = {"ok": True, "data": data if data is not None else {}}
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def encode_error(error: str) -> bytes:
    """Encode an error response."""
    msg = {"ok": False, "error": error}
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def encode_chunk(samples: Iterable[Tuple[int, float, float, int]]) -> bytes:
    """Encode a sample chunk.

    ``samples`` is an iterable of ``(timestamp_us, current_uA, voltage_V,
    gpio_byte)`` tuples. Result: one NDJSON line. Empty chunks are
    legal (yields ``{"chunk":[]}``); the server should usually skip
    sending these but the protocol accepts them.

    Sample values are rounded to 3 decimal places on the wire to match
    the canonical CSV format (``csv_format.py``); this loses no
    information given the synthesis precision and keeps wire size
    small.
    """
    rows: List[List[Any]] = []
    for ts, i, v, gb in samples:
        rows.append([int(ts), round(float(i), 3), round(float(v), 3), int(gb)])
    msg = {"chunk": rows}
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Command:
    """A decoded client → server command."""
    cmd: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Response:
    """A decoded server → client response (ok or error)."""
    ok: bool
    data: dict[str, Any]
    error: str | None


@dataclass(frozen=True, slots=True)
class Chunk:
    """A decoded sample chunk."""
    samples: List[Tuple[int, float, float, int]]


def decode_message(line: bytes | str) -> Command | Response | Chunk:
    """Decode a single NDJSON line into one of the three message types.

    The discriminator is the top-level key set:

    * ``cmd``                → ``Command``
    * ``ok`` (true or false) → ``Response``
    * ``chunk``              → ``Chunk``

    Raises ValueError if the line is not parseable JSON, has none of
    the three discriminators, or has malformed contents.
    """
    if isinstance(line, bytes):
        text = line.decode("utf-8")
    else:
        text = line
    text = text.strip()
    if not text:
        raise ValueError("empty message")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON: {e.msg}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"top-level value must be object, got {type(obj).__name__}")

    if "cmd" in obj:
        cmd = obj.pop("cmd")
        if not isinstance(cmd, str):
            raise ValueError(f"cmd must be string, got {type(cmd).__name__}")
        return Command(cmd=cmd, params=obj)

    if "ok" in obj:
        ok = obj["ok"]
        if not isinstance(ok, bool):
            raise ValueError(f"ok must be boolean, got {type(ok).__name__}")
        if ok:
            data = obj.get("data", {})
            if not isinstance(data, dict):
                raise ValueError("data field must be object")
            return Response(ok=True, data=data, error=None)
        else:
            err = obj.get("error", "unknown error")
            if not isinstance(err, str):
                raise ValueError("error field must be string")
            return Response(ok=False, data={}, error=err)

    if "chunk" in obj:
        rows = obj["chunk"]
        if not isinstance(rows, list):
            raise ValueError("chunk field must be array")
        samples: List[Tuple[int, float, float, int]] = []
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError(f"chunk[{i}] must be 4-element array")
            ts, cur, volt, gb = row
            samples.append((int(ts), float(cur), float(volt), int(gb)))
        return Chunk(samples=samples)

    raise ValueError(
        f"message has no recognized discriminator (cmd/ok/chunk): keys={list(obj)}"
    )
