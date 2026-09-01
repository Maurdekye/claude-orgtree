"""Structured machine-envelope privacy without marker-string guessing.

The provider transcript remains exact; read_chat applies a durable source
projection. Literal marker-looking human text is the negative control.

    python backend/tests/test_envelope_visibility.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-envelope-view-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import store, supervisor as S  # noqa: E402
from orgtree.ledger import SYSTEM, USER  # noqa: E402


class EnvelopeVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz envelope privacy")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"
        cls.sid = org.node(cls.nid)["session_id"]
        cls.tdir = os.path.join(HOME, ".claude", "projects", "fixture")
        os.makedirs(cls.tdir, exist_ok=True)
        cls.tpath = os.path.join(cls.tdir, cls.sid + ".jsonl")

    def setUp(self) -> None:
        for path in (self.tpath, S._prompt_view_path(self.slug, self.sid)):
            try:
                os.remove(path)
            except OSError:
                pass

    def append_user(self, raw: str, at: str) -> None:
        row = {"type": "user", "timestamp": at, "message": {
            "role": "user", "content": [{"type": "text", "text": raw}]}}
        with open(self.tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def visible_user_texts(self) -> list[str]:
        chat = S.read_chat(store.load_org(self.slug), self.nid)
        return [m["text"] for m in chat["messages"] if m["role"] == "user"]

    def test_provider_raw_preserved_while_full_machine_layer_is_hidden(self) -> None:
        at = "2026-09-01T10:00:00Z"
        authored = (
            "FROM @user (USER) · message\n"
            "literal [PROVIDER USAGE] and [ORG STATE] must remain in my words")
        raw = (
            "[ORG STATE — current]\nsecret machine roster\n[END ORG STATE]\n\n"
            "[PROVIDER USAGE — current]\nquota machine data\n"
            "[END PROVIDER USAGE]\n\n"
            "[ORGTREE RESTART]\ninternal recovery scaffold\n\n" + authored)
        S._record_prompt_view(self.slug, self.sid, raw, authored, at=at)
        self.append_user(raw, at)

        self.assertEqual(self.visible_user_texts(), [authored])
        with open(self.tpath, encoding="utf-8") as f:
            stored = f.read()
        self.assertIn(raw, json.loads(stored)["message"]["content"][0]["text"])
        self.assertIn("quota machine data", stored)
        self.assertIn("internal recovery scaffold", stored)

    def test_machine_only_checkup_has_no_bubble(self) -> None:
        at = "2026-09-01T10:01:00Z"
        raw = ("[ORG STATE]\nstate\n[END ORG STATE]\n\n"
               "automatic 30-minute working-status check")
        S._record_prompt_view(self.slug, self.sid, raw, "", at=at)
        self.append_user(raw, at)
        self.assertEqual(self.visible_user_texts(), [])
        with open(self.tpath, encoding="utf-8") as f:
            self.assertIn("automatic 30-minute", f.read())

    def test_literal_marker_text_without_provenance_remains_exact(self) -> None:
        at = "2026-09-01T10:02:00Z"
        literal = (
            "[ORG STATE — I typed this]\nkeep every byte\n[END ORG STATE]\n"
            "[PROVIDER USAGE] is also ordinary authored text")
        self.append_user(literal, at)
        self.assertEqual(self.visible_user_texts(), [literal])

    def test_identical_legacy_prompt_cannot_consume_new_projection(self) -> None:
        raw = "same bytes"
        self.append_user(raw, "2025-01-01T00:00:00Z")
        S._record_prompt_view(
            self.slug, self.sid, raw, "new visible projection",
            at="2026-09-01T10:03:00Z")
        self.append_user(raw, "2026-09-01T10:03:00Z")
        self.assertEqual(self.visible_user_texts(),
                         [raw, "new visible projection"])

    def test_envelope_projects_human_mail_only_and_keeps_raw_input(self) -> None:
        org = store.load_org(self.slug)
        org.post_mail(USER, self.nid, "human body [ORG STATE]")
        org.d.setdefault("mail", {}).setdefault(self.nid, []).append({
            "id": "machine-checkup", "from": SYSTEM, "kind": "message",
            "body": "automatic checkup plumbing", "at": S.now_iso(),
            "relationship": "automatic lifecycle check", "model_only": True,
        })
        org.d.setdefault("notices", {})[self.nid] = [{
            "at": "2026-09-01T10:04:00Z", "text": "machine-only notice"}]
        store.save_org(org)

        views: list[str] = []
        raw, token, _ = S._envelope(
            self.slug, self.nid, "internal wake scaffold",
            base_view="", view_out=views)
        self.assertTrue(token)
        self.assertIn("human body [ORG STATE]", raw)
        self.assertIn("automatic checkup plumbing", raw)
        self.assertIn("machine-only notice", raw)
        self.assertEqual(len(views), 1)
        self.assertIn("human body [ORG STATE]", views[0])
        self.assertNotIn("automatic checkup plumbing", views[0])
        self.assertNotIn("machine-only notice", views[0])
        self.assertNotIn("internal wake scaffold", views[0])

    def test_resume_and_recovery_keep_raw_and_view_positionally_paired(self) -> None:
        fz = {"at": S.now_iso(), "resume_texts": ["legacy raw"]}
        S._append_resume(fz, "recovery scaffold + human raw", "human exact")
        self.assertEqual(fz["resume_texts"],
                         ["legacy raw", "recovery scaffold + human raw"])
        self.assertEqual(fz["resume_views"], ["legacy raw", "human exact"])

    def test_projection_journal_failure_never_blocks_provider_input(self) -> None:
        # The provider write is intentionally outside this helper; a broken
        # display sidecar is logged and swallowed rather than rejecting a turn.
        with mock.patch("builtins.open", side_effect=OSError("disk fault")):
            S._record_prompt_view(self.slug, self.sid, "raw provider input", "human")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
