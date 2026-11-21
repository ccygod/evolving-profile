import datetime as dt
import unittest

from v6 import ledger_bank_bridge as bridge


def row(seq, text, role="user"):
    return (seq, role, dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc), text, "/p")


class LedgerBankBridgeTest(unittest.TestCase):
    def test_batches_respect_char_budget_and_keep_order(self):
        rows = [row(i, "x" * 5000) for i in range(5)]
        batches = bridge._batches(rows)
        self.assertGreater(len(batches), 1)
        self.assertEqual([r[0] for b in batches for r in b], list(range(5)))
        for b in batches:
            self.assertLessEqual(sum(len(r[3]) + 16 for r in b), bridge.MAX_BATCH_CHARS + 5016)

    def test_truncate_marks_cut(self):
        self.assertTrue(bridge._truncate("a" * 10, 4).startswith("aaaa…[truncated 6"))
        self.assertEqual(bridge._truncate("abc", 4), "abc")

    def test_bootstrap_skips_manually_retained_range(self):
        class Cur:
            def __init__(self):
                self.calls = []
                self.results = [(dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc),), (41,)]
            def execute(self, sql, params):
                self.calls.append(params)
            def fetchone(self):
                return self.results.pop(0)
        cur = Cur()
        self.assertEqual(bridge._bootstrap_seq(cur, "bank", "antigravity", "023f74ba-87b7"), 41)
        self.assertEqual(cur.calls[0][1], "antigravity-023f74ba-%")

    def test_bootstrap_without_manual_docs(self):
        class Cur:
            def execute(self, sql, params):
                pass
            def fetchone(self):
                return (None,)
        self.assertEqual(bridge._bootstrap_seq(Cur(), "bank", "antigravity", "abc"), -1)


if __name__ == "__main__":
    unittest.main()
