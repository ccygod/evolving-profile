"""Regression tests for the preference-unit storage contract (2026-11-24).

Incident: an early ontology_hygiene migration replaced each active unit's dict
`scope` with its scope_level string in place (same revision). Readers crashed
with "'str' object has no attribute 'get'" for 4 days, session restrictions were
lost, and revision-keyed caches kept trusting changed content.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from repository import GuidanceRepository

BANK = "test-bank"


def _unit(scope):
    return {"text": "t", "scope": scope, "scope_level": "global", "applies_when": [], "exceptions": []}


class ScopeContractTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "g.sqlite3"
        GuidanceRepository(self.path, BANK)  # creates schema + triggers
        self.db = sqlite3.connect(self.path)

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def insert(self, rev, payload, active=1):
        self.db.execute("INSERT INTO unit_revisions VALUES(?,?,?,?,?,0)", ("u1", rev, "p", json.dumps(payload), active))
        self.db.commit()

    def test_string_scope_is_rejected_on_insert(self):
        with self.assertRaisesRegex(sqlite3.DatabaseError, "invalid_guidance_scope"):
            self.insert("r1", _unit("global"))

    def test_missing_scope_is_rejected(self):
        payload = _unit({})
        del payload["scope"]
        with self.assertRaisesRegex(sqlite3.DatabaseError, "invalid_guidance_scope"):
            self.insert("r1", payload)

    def test_active_payload_cannot_be_edited_in_place(self):
        self.insert("r1", _unit({"user_id": "u"}))
        with self.assertRaisesRegex(sqlite3.DatabaseError, "unit_revision_immutable"):
            self.db.execute("UPDATE unit_revisions SET payload_json=? WHERE revision='r1'", (json.dumps(_unit({"user_id": "u", "x": 1})),))

    def test_overwriting_scope_with_string_is_rejected_even_when_inactive(self):
        self.insert("r1", _unit({"user_id": "u"}), active=0)
        with self.assertRaisesRegex(sqlite3.DatabaseError, "invalid_guidance_scope"):
            self.db.execute("UPDATE unit_revisions SET payload_json=? WHERE revision='r1'", (json.dumps(_unit("global")),))

    def test_withdraw_with_note_is_still_allowed(self):
        self.insert("r1", _unit({"user_id": "u"}))
        repo = GuidanceRepository(self.path, BANK)
        self.assertEqual(repo.withdraw_unit("u1", "r1", "test")["state"], "withdrawn")


if __name__ == "__main__":
    unittest.main()
