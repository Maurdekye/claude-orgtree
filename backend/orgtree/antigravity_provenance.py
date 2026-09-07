"""Conservative workaround for agy's historical LastRunErrorDetails lookup.

Only a resumed conversation, captured BEFORE launch, can supply evidence.
Missing/unsupported evidence preserves the CLI failure. No credentials or
conversation writes. Field numbers come from agy 1.1.27's embedded descriptors.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

MAX_ROWS = 2048
MAX_ERRORS = 32
MAX_BLOB = 1_048_576
MAX_BYTES = 8_388_608
MAX_EVENTS = 16384
QUOTA = re.compile(r"Individual quota reached\.[^\r\n]{0,400}\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _varint(b: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        byte = b[pos]; pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError("invalid protobuf varint")


def _fields(b: bytes) -> dict[int, list[bytes | int]]:
    if len(b) > MAX_BLOB:
        raise ValueError("payload bound")
    result: dict[int, list[bytes | int]] = {}
    pos = 0
    while pos < len(b):
        tag, pos = _varint(b, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("invalid field")
        if wire == 0:
            value, pos = _varint(b, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = _varint(b, pos)
            else:
                size = 8 if wire == 1 else 4
            if pos + size > len(b):
                raise ValueError("truncated field")
            value = b[pos:pos + size]; pos += size
        else:
            raise ValueError("unsupported wire type")
        result.setdefault(number, []).append(value)
    return result


def _one(fields: dict[int, list[bytes | int]], key: int,
         default: bytes | int = b"") -> bytes | int:
    values = fields.get(key, [default])
    if len(values) != 1:
        raise ValueError("ambiguous scalar")
    return values[0]


def _blob(fields: dict[int, list[bytes | int]], key: int) -> bytes:
    value = _one(fields, key)
    if not isinstance(value, bytes):
        raise ValueError("not a message/string")
    return value


def conversation_path(cid: str, env: dict[str, str]) -> Path | None:
    if not UUID.fullmatch(cid):
        return None
    home = env.get("USERPROFILE" if os.name == "nt" else "HOME")
    if not home:
        return None
    return Path(home) / ".gemini/antigravity-cli/conversations" / (cid + ".db")


def _identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    if not path.is_file() or Path(str(path) + "-wal").exists() or Path(str(path) + "-journal").exists():
        raise ValueError("database not a stable standalone file")
    return stat.st_dev, stat.st_ino


class _Read:
    def __init__(self, path: Path, cid: str):
        self.path = path
        self.identity = _identity(path)
        self.before = path.stat()
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=.1)
        self.work = 0
        self.bytes = 0
        self.db.set_progress_handler(self._budget, 1000)
        try:
            rows = self.db.execute("SELECT cascade_id FROM trajectory_meta LIMIT 2").fetchall()
            if rows != [(cid,)]:
                raise ValueError("wrong conversation")
        except Exception:
            self.db.close()
            raise

    def _budget(self) -> int:
        self.work += 1000
        return int(self.work > 200_000)

    def close(self) -> None:
        self.db.close()
        after = self.path.stat()
        if (_identity(self.path) != self.identity or after.st_size != self.before.st_size
                or after.st_mtime_ns != self.before.st_mtime_ns):
            raise ValueError("database changed during read")

    def rows(self, suffix: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        # Reject oversized blobs before SQLite copies them into Python.
        sql = ("SELECT idx,step_type,status,step_format,"
               "length(metadata),length(error_details),length(step_payload),"
               "CASE WHEN length(metadata)<=? THEN metadata END,"
               "CASE WHEN length(error_details)<=? THEN error_details END,"
               "CASE WHEN length(step_payload)<=? THEN step_payload END FROM steps " + suffix)
        result = []
        for row in self.db.execute(sql, (MAX_BLOB, MAX_BLOB, MAX_BLOB, *args)):
            sizes = [v or 0 for v in row[4:7]]
            self.bytes += sum(sizes)
            if max(sizes) > MAX_BLOB or self.bytes > MAX_BYTES or len(result) >= MAX_ROWS:
                raise ValueError("read bound")
            if row[3] != 0 or row[9] is None:
                raise ValueError("unsupported step format")
            result.append((row[0], row[1], row[2], row[7] or b"", row[8] or b"", row[9]))
        return result


def _fingerprint(row: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for value in row:
        data = value if isinstance(value, bytes) else str(value).encode()
        digest.update(len(data).to_bytes(8, "little")); digest.update(data)
    return digest.hexdigest()


def _payload(row: tuple[Any, ...]) -> dict[int, list[bytes | int]]:
    payload = _fields(row[5])
    if _one(payload, 1, 0) != row[1] or _one(payload, 4, 0) != row[2]:
        raise ValueError("column/payload disagreement")
    return payload


def _quota_error(row: tuple[Any, ...]) -> str | None:
    if row[1] != 17:
        return None
    run_error = _fields(_blob(_payload(row), 24))
    details = _fields(_blob(run_error, 3))
    if _one(details, 4, 0) != 0:  # CortexErrorDetails.is_benign
        return None
    message = _blob(details, 1).decode("utf-8").strip()
    return message if QUOTA.fullmatch(message) else None


@dataclass(frozen=True)
class Boundary:
    path: Path
    cid: str
    identity: tuple[int, int]
    index: int
    fingerprint: str
    errors: tuple[tuple[int, str, str], ...]


def capture(cid: str | None, env: dict[str, str]) -> Boundary | None:
    """Best effort, before Popen: never fail a turn for unavailable evidence."""
    reader = None
    try:
        path = conversation_path(cid or "", env)
        if path is None:
            return None
        reader = _Read(path, cid or "")
        last = reader.rows("ORDER BY idx DESC LIMIT 1")
        if len(last) != 1:
            return None
        errors = []
        for row in reader.rows("WHERE step_type=17 ORDER BY idx DESC LIMIT ?", (MAX_ERRORS,)):
            message = _quota_error(row)
            if message:
                errors.append((row[0], message, _fingerprint(row)))
        boundary = Boundary(path, cid or "", reader.identity, last[0][0], _fingerprint(last[0]), tuple(errors))
        reader.close(); reader = None
        return boundary if errors else None
    except (OSError, sqlite3.Error, ValueError, IndexError, TypeError):
        return None
    finally:
        if reader is not None:
            reader.db.close()


def reconcile(boundary: Boundary | None, prompt: str, cid: str | None,
              result: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return content-free correction evidence, or preserve the raw ERROR."""
    reader = None
    try:
        if (boundary is None or cid != boundary.cid or result.get("conversation_id") != cid
                or result.get("status") != "ERROR" or len(events) > MAX_EVENTS):
            return None
        error = result.get("error")
        if not isinstance(error, str) or not QUOTA.fullmatch(error.strip()):
            return None
        matches = [e for e in boundary.errors if e[1] == error.strip()]
        if not matches:
            return None
        old_index, _, old_hash = matches[0]
        reader = _Read(boundary.path, boundary.cid)
        if reader.identity != boundary.identity:
            return None
        last = reader.rows("WHERE idx=?", (boundary.index,))
        old = reader.rows("WHERE idx=?", (old_index,))
        if (len(last) != 1 or _fingerprint(last[0]) != boundary.fingerprint
                or len(old) != 1 or _fingerprint(old[0]) != old_hash):
            return None
        rows = reader.rows("WHERE idx>? ORDER BY idx LIMIT ?", (boundary.index, MAX_ROWS + 1))
        if (len(rows) < 2 or rows[0][1] != 14
                or [r[0] for r in rows] != list(range(boundary.index + 1, boundary.index + 1 + len(rows)))):
            return None
        payloads = []
        for i, row in enumerate(rows):
            if row[1] not in (14, 15, 101, 132) or row[2] != 3 or row[4] or (i and row[1] == 14):
                return None
            payload = _payload(row)
            if payload.get(24):  # RunError oneof, independent of SQL step_type.
                return None
            payloads.append(payload)
        user = _fields(_blob(payloads[0], 19))
        if _blob(user, 2).decode("utf-8") != prompt:
            return None
        final = rows[-1]
        if final[1] != 15:
            return None
        response = _fields(_blob(payloads[-1], 20))
        body = _blob(response, 8) or _blob(response, 1)
        if not body.strip() or response.get(7) or _one(response, 12, 0) != 2:
            return None
        states: dict[int, str] = {}
        texts: dict[int, str] = {}
        text_size = 0
        by_index = {r[0]: r[1] for r in rows}
        final_seen = False
        for event in events:
            if event.get("event") == "error":
                return None
            if event.get("event") != "step_update":
                continue
            step = event.get("step_update")
            if not isinstance(step, dict):
                return None
            index = step.get("step_index")
            if (type(index) is not int or index not in by_index or step.get("conversation_id") != cid
                    or step.get("state") not in ("ACTIVE", "DONE")):
                return None
            kind = step.get("step_type")
            expected = {14: "user_input", 15: "agent_response", 132: "tool", 101: "system_message"}[by_index[index]]
            if kind != expected:
                return None
            states[index] = step["state"]
            if kind == "agent_response":
                delta = step.get("text_delta", "")
                if not isinstance(delta, str):
                    return None
                text_size += len(delta)
                if text_size > MAX_BYTES:
                    return None
                texts[index] = texts.get(index, "") + delta
            if index == final[0] and step["state"] == "DONE":
                final_seen = True
        if (not final_seen or any(state != "DONE" for state in states.values())
                or texts.get(final[0], "").encode("utf-8") != body):
            return None
        # Every emitted model/tool step must belong to this complete appended
        # interval, and each persisted model/tool step must have completed on wire.
        if any(states.get(r[0]) != "DONE" for r in rows if r[1] in (15, 132)):
            return None
        reader.close(); reader = None
        return {"kind": "historical_cli_quota_error", "historical_step": old_index,
                "boundary_step": boundary.index, "final_step": final[0]}
    except (OSError, sqlite3.Error, ValueError, IndexError, TypeError, KeyError):
        return None
    finally:
        if reader is not None:
            reader.db.close()
