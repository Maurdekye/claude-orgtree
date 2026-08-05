# pyright: strict
"""SQLite store for the orgtree mail hub.

WAL mode, connection-per-request, busy_timeout — a queue with cursors and a
small roster, deliberately NOT the orgtree org-doc JSON store (that is a
single-process design guarded by a process-wide lock; the hub is a
multi-writer network service).

The hub stores NO secrets: `orgs.fingerprint` is sha256(secret) hex, and
verification hashes the presented secret and compares the full digest
(hmac.compare_digest) — never the 6-character display suffix.

Attachment BLOBS live as files under <data>/blobs/<id>; the table holds
metadata only, so downloads stream without SQLite blob gymnastics and the
retention sweep removes file + row together.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

DATA_DIR = os.environ.get("HUB_DATA", "/data")
DB_PATH = os.path.join(DATA_DIR, "hub.sqlite3")
BLOB_DIR = os.path.join(DATA_DIR, "blobs")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS orgs (
  slug          TEXT PRIMARY KEY,
  fingerprint   TEXT NOT NULL,
  org_name      TEXT,
  username      TEXT,
  blurb         TEXT,
  registered_at TEXT NOT NULL,
  last_seen     TEXT,
  kind          TEXT NOT NULL DEFAULT 'org'   -- org | chat (FR-06: independent
                                              -- Claude Code chats are clients)
);
CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,          -- client-minted: idempotent send
  from_slug       TEXT NOT NULL,
  to_slug         TEXT NOT NULL,
  body            TEXT NOT NULL,
  kind            TEXT,
  thread_id       TEXT,                      -- reserved (spec §11№2), unused v1
  sent_at         TEXT,                      -- claimed by sender, display only
  received_at     TEXT NOT NULL,             -- hub clock: the ordering authority
  state           TEXT NOT NULL DEFAULT 'queued',   -- queued | fetched
  fetched_at      TEXT,
  delivered_at    TEXT,
  read_at         TEXT,
  receipts_pushed INTEGER NOT NULL DEFAULT 1,       -- 0 = sender owed an update
  attachments     TEXT NOT NULL DEFAULT '[]'        -- [{id,name,bytes}] json
);
CREATE INDEX IF NOT EXISTS idx_msg_to   ON messages(to_slug, state);
CREATE INDEX IF NOT EXISTS idx_msg_from ON messages(from_slug, receipts_pushed);
CREATE TABLE IF NOT EXISTS attachments (
  id         TEXT PRIMARY KEY,
  owner_slug TEXT NOT NULL,
  name       TEXT NOT NULL,
  bytes      INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  message_id TEXT                            -- NULL until a send binds it
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BLOB_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_SCHEMA)
    # FR-06 migration: hubs created before the client-kind column
    try:
        con.execute("ALTER TABLE orgs ADD COLUMN kind TEXT NOT NULL "
                    "DEFAULT 'org'")
        con.commit()
    except sqlite3.OperationalError:
        pass                                    # column already exists
    return con


def blob_path(att_id: str) -> str:
    # att ids are server-minted hex — no traversal surface, but belt+braces
    safe = "".join(c for c in att_id if c.isalnum())
    return os.path.join(BLOB_DIR, safe)


def row_to_envelope(r: sqlite3.Row) -> dict[str, Any]:
    """The wire shape a recipient sees on poll."""
    return {
        "id": r["id"], "from": r["from_slug"], "to": r["to_slug"],
        "body": r["body"], "kind": r["kind"], "thread_id": r["thread_id"],
        "sent_at": r["sent_at"], "received_at": r["received_at"],
        "attachments": json.loads(r["attachments"] or "[]"),
    }
