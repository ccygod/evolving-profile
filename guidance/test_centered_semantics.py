"""Corpus-centered semantic gate: weak evidence needs two signals to agree."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import recall_first
import semantic_recall


def _unit(uid, text, **extra):
    return {'id': uid, 'revision': 'r1', 'text': text, 'applies_when': [], 'preference_audit': {'state': 'approved'}, **extra}


class _Service:
    def __init__(self, scores): self.scores = scores
    def centered_scores(self, units, task): return dict(self.scores)


class CenteredCosineTests(unittest.TestCase):
    def test_centering_separates_vectors_that_share_a_dominant_direction(self):
        shared = [10.0, 10.0, 10.0]
        documents = [[s + d for s, d in zip(shared, delta)] for delta in ([1, 0, 0], [0, 1, 0], [0, 0, 1])] * 7
        query = [s + d for s, d in zip(shared, [1, 0, 0])]
        raw = [semantic_recall._cosine(query, document) for document in documents[:3]]
        self.assertGreater(min(raw), 0.99)  # raw cosine cannot tell them apart
        centered = semantic_recall.centered_cosines(query, documents)[:3]
        self.assertGreater(centered[0], 0.9)
        self.assertLess(centered[1], 0.0); self.assertLess(centered[2], 0.0)

    def test_small_corpus_falls_back_to_raw_cosine(self):
        documents = [[1.0, 0.0], [0.0, 1.0]]
        self.assertEqual(semantic_recall.centered_cosines([1.0, 0.0], documents), [1.0, 0.0])


class SemanticGateTests(unittest.TestCase):
    def setUp(self):
        self.units = [_unit('core', '核心'), _unit('weak-ok', '弱但语义支持'), _unit('weak-bad', '弱且语义不支持'),
                      _unit('sem-hit', '纯语义命中'), _unit('sem-low', '语义不足'), _unit('named', '用 HiSite 管理连接')]
        self.ranked = [(self.units[0], {'tier': 'core'}), (self.units[1], {'tier': 'related'}), (self.units[2], {'tier': 'related'})]
        self.service = _Service({'core': 0.01, 'weak-ok': 0.20, 'weak-bad': 0.05, 'sem-hit': 0.40, 'sem-low': 0.24, 'named': 0.50})

    def _ids(self):
        ranked, gate = recall_first._apply_centered_semantics(self.ranked, self.units, {'current_user_message': '随便'}, self.service)
        return [unit['id'] for unit, _ in ranked], gate, dict((unit['id'], why) for unit, why in ranked)

    def test_core_lexical_match_survives_low_semantics(self):
        self.assertIn('core', self._ids()[0])

    def test_related_lexical_match_needs_semantic_corroboration(self):
        ids, gate, _ = self._ids()
        self.assertIn('weak-ok', ids); self.assertNotIn('weak-bad', ids)
        self.assertEqual(gate['lexical_related_dropped'], 1)

    def test_semantic_only_match_needs_the_on_topic_floor(self):
        ids, gate, reasons = self._ids()
        self.assertIn('sem-hit', ids); self.assertNotIn('sem-low', ids)
        self.assertEqual(reasons['sem-hit']['tier'], 'semantic_backfill')

    def test_unmentioned_named_product_is_not_backfilled(self):
        self.assertNotIn('named', self._ids()[0])

    def test_nothing_relevant_yields_nothing(self):
        service = _Service({unit['id']: 0.05 for unit in self.units})
        ranked, gate = recall_first._apply_centered_semantics([(self.units[1], {'tier': 'related'})], self.units, {'current_user_message': 'x'}, service)
        self.assertEqual(ranked, []); self.assertEqual(gate['semantic_backfill'], 0)


if __name__ == '__main__':
    unittest.main()
