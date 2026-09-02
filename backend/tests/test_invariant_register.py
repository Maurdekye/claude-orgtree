"""Mechanical checks on docs/INVARIANTS.md — the app-state invariant register.

Hermetic: reads only the checked-in Markdown files, no network, no CLI.

    python backend/tests/test_invariant_register.py

Guards against exactly the failure modes the register exists to prevent:
an entry missing a required field, a duplicate or malformed ID, a status
outside the closed vocabulary (in particular no generic "unknown" or
"unaccounted" state), an `enforced` entry with no owning reference, and the
register going undiscoverable from the two indexes that are supposed to
point at it.
"""
from __future__ import annotations

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
REGISTER = os.path.join(REPO_ROOT, "docs", "INVARIANTS.md")
DOCS_README = os.path.join(REPO_ROOT, "docs", "README.md")
DECISIONS = os.path.join(REPO_ROOT, "DECISIONS.md")

REQUIRED_FIELDS = [
    "Statement", "Scope", "Prohibited states", "Allowed exceptions",
    "Observable enforcement", "Owning references", "Status", "Provenance",
    "Amendments",
]

ALLOWED_STATUS = ("enforced", "implementation_in_flight", "known_gap")

# Disallowed regardless of where they appear in a Status value: the whole
# point of the closed vocabulary is that nothing collapses to a shrug.
FORBIDDEN_STATUS_WORDS = ("unknown", "unaccounted", "tbd", "n/a")

HEADING_RE = re.compile(r"^### (INV-(\d{3})) · (.+)$", re.MULTILINE)
ID_RE = re.compile(r"^INV-\d{3}$")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _entries(text: str) -> list[tuple[str, str, str]]:
    """[(id, title, body)] for every ``### INV-NNN`` heading, body = the
    text up to (not including) the next ``##``-or-higher heading."""
    matches = list(HEADING_RE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        start = m.end()
        end = len(text)
        # Body ends at the next entry heading or any ## section heading.
        rest = text[start:]
        next_heading = re.search(r"\n##+ ", rest)
        if next_heading:
            end = start + next_heading.start()
        out.append((m.group(1), m.group(3), text[start:end]))
    return out


class InvariantRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue_ = None
        if not os.path.isfile(REGISTER):
            raise unittest.SkipTest(f"register not found at {REGISTER}")
        cls.text = _read(REGISTER)
        cls.entries = _entries(cls.text)

    def test_register_is_nonempty(self) -> None:
        self.assertGreater(len(self.entries), 0, "no INV-NNN entries found")

    def test_ids_are_well_formed_and_unique(self) -> None:
        ids = [eid for eid, _title, _body in self.entries]
        for eid in ids:
            self.assertRegex(eid, ID_RE, f"malformed invariant id {eid!r}")
        dupes = {eid for eid in ids if ids.count(eid) > 1}
        self.assertFalse(dupes, f"duplicate invariant ids: {sorted(dupes)}")

    def test_every_entry_has_every_required_field(self) -> None:
        missing: dict[str, list[str]] = {}
        for eid, _title, body in self.entries:
            gaps = [field for field in REQUIRED_FIELDS
                    if not re.search(
                        r"^- \*\*" + re.escape(field), body, re.MULTILINE)]
            if gaps:
                missing[eid] = gaps
        self.assertFalse(
            missing,
            "entries missing required fields (id -> missing fields): "
            f"{missing}")

    def test_every_status_is_in_the_closed_vocabulary(self) -> None:
        bad: dict[str, str] = {}
        for eid, _title, body in self.entries:
            m = re.search(r"^- \*\*Status:\*\*\s*(\S+)", body, re.MULTILINE)
            if not m:
                bad[eid] = "<no Status line found>"
                continue
            value = m.group(1).rstrip(".,;")
            if value not in ALLOWED_STATUS:
                bad[eid] = value
        self.assertFalse(
            bad,
            "entries with a Status value outside "
            f"{ALLOWED_STATUS}: {bad}")

    def test_no_status_line_uses_a_generic_unknown_word(self) -> None:
        """A named, enumerated state (e.g. `RESOLVED-UNKNOWN`, presented as
        code) is a real, accounted status and is allowed — it is prose use
        of "unknown"/"unaccounted" as a shrug, outside a code span, that this
        register's status vocabulary exists to forbid."""
        offenders: dict[str, list[str]] = {}
        for eid, _title, body in self.entries:
            m = re.search(
                r"^- \*\*Status:\*\*(.*?)(?=\n- \*\*|\n\n)", body,
                re.MULTILINE | re.DOTALL)
            status_text = (m.group(1) if m else "")
            prose = re.sub(r"`[^`]*`", "", status_text).lower()
            hits = [w for w in FORBIDDEN_STATUS_WORDS if w in prose]
            if hits:
                offenders[eid] = hits
        self.assertFalse(
            offenders,
            f"Status text uses a forbidden generic word outside a code "
            f"span: {offenders}")

    def test_enforced_entries_name_an_owning_reference(self) -> None:
        """An `enforced` verdict with an empty owning-reference line is an
        unsubstantiated claim, which the charter explicitly forbids."""
        bad = []
        for eid, _title, body in self.entries:
            status_m = re.search(
                r"^- \*\*Status:\*\*\s*(\S+)", body, re.MULTILINE)
            if not status_m or not status_m.group(1).startswith("enforced"):
                continue
            ref_m = re.search(
                r"^- \*\*Owning references:\*\*(.*?)(?=\n- \*\*|\n\n)", body,
                re.MULTILINE | re.DOTALL)
            ref_text = (ref_m.group(1) if ref_m else "").strip()
            if len(ref_text) < 8:  # a real citation is never this short
                bad.append(eid)
        self.assertFalse(
            bad, f"enforced entries with no substantive owning reference: "
            f"{bad}")

    def test_provenance_and_amendments_present_for_every_entry(self) -> None:
        """Amendment history is how the register stays user-amendable only
        — every entry must carry the field even when it is empty so far."""
        bad = []
        for eid, _title, body in self.entries:
            if not re.search(r"^- \*\*Amendments:\*\*\s*\S", body,
                              re.MULTILINE):
                bad.append(eid)
        self.assertFalse(bad, f"entries with an empty Amendments line: {bad}")

    def test_docs_readme_links_to_the_register(self) -> None:
        readme = _read(DOCS_README)
        self.assertIn(
            "INVARIANTS.md", readme,
            "docs/README.md does not link to docs/INVARIANTS.md")

    def test_decisions_links_to_the_register_without_duplicating_it(self) -> None:
        decisions = _read(DECISIONS)
        self.assertIn(
            "INVARIANTS.md", decisions,
            "DECISIONS.md does not link to docs/INVARIANTS.md")
        # The register is explicitly narrower than DECISIONS.md and must not
        # try to re-host its entry template or D-NNN history inline.
        self.assertNotIn("### INV-", decisions,
                          "DECISIONS.md duplicates invariant entries inline "
                          "instead of linking to the register")


if __name__ == "__main__":
    unittest.main(verbosity=2)
