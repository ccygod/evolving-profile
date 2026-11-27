"""Duplicate preference consolidation: group, keep the best-evidenced, stay reversible."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import consolidate_units as cu


def _unit(uid, refs=1, validity='context_sensitive', state='approved', at='2026-01-01'):
    return {'id': uid, 'revision': 'r1', 'text': uid, 'activated_at': at,
            'evidence_refs': [{'document_id': f'{uid}-doc{i}'} for i in range(refs)],
            'preference_audit': {'state': state, 'validity_kind': validity, 'scope_level': 'domain', 'reason': 'ok'}}


def _vectors(directions):
    """Twenty-plus documents sharing a dominant direction, as real embeddings do."""
    axes = {'a': [1, 0, 0, 0], 'a2': [0.97, 0.05, 0, 0], 'b': [0, 1, 0, 0], 'c': [0, 0, 1, 0], 'd': [0, 0, 0, 1]}
    return [[10 + value for value in axes[name]] for name in directions]


class _Repo:
    def __init__(self, units): self.units = units; self.superseded = []
    def active_units(self): return self.units
    def mark_unit_superseded(self, unit_id, revision, *, superseded_by, reason, authority):
        self.superseded.append((unit_id, superseded_by, reason))
        for unit in self.units:
            if unit['id'] == unit_id:
                unit['preference_audit'] = {'state': 'superseded', 'superseded_by': superseded_by, 'reason': reason}


class _Service:
    def __init__(self, vectors):
        outer = self
        outer.vectors = vectors
        class Cache:
            def document_vectors(self, units): return [outer.vectors[unit['id']] for unit in units]
        self.cache = Cache()


class ConsolidationTests(unittest.TestCase):
    def _fixture(self):
        names = ['a', 'a2'] + ['b', 'c', 'd'] * 7
        units = [_unit(f'u{i}') for i in range(len(names))]
        units[0] = _unit('u0', refs=1); units[1] = _unit('u1', refs=3)
        vectors = dict(zip([unit['id'] for unit in units], _vectors(names)))
        return units, vectors

    def test_restatements_are_grouped_and_distinct_rules_are_not(self):
        units, vectors = self._fixture()
        plans = cu.plan(units, [vectors[unit['id']] for unit in units], 0.7)
        grouped = [sorted([item['canonical']['id']] + [unit['id'] for unit in item['duplicates']]) for item in plans]
        self.assertIn(['u0', 'u1'], grouped)

    def test_small_registry_is_never_consolidated(self):
        # Uncentred, these three vectors have pairwise cosine ~1.0. Too few
        # documents to centre must mean "no opinion", not "all duplicates".
        units, vectors = self._fixture()
        few = [units[0], units[1], units[2]]
        self.assertEqual(cu.duplicate_clusters(few, [vectors[unit['id']] for unit in few], 0.7), [])

    def test_distinct_rules_in_a_full_registry_stay_separate(self):
        units, vectors = self._fixture()
        plans = cu.plan(units, [vectors[unit['id']] for unit in units], 0.7)
        for item in plans:
            members = {item['canonical']['id'], *[unit['id'] for unit in item['duplicates']]}
            self.assertFalse({'u0', 'u2'} <= members)  # axis a vs axis b

    def test_best_evidenced_unit_is_kept(self):
        self.assertEqual(cu.choose_canonical([_unit('one', refs=1), _unit('three', refs=3)])['id'], 'three')
        self.assertEqual(cu.choose_canonical([_unit('ctx', validity='context_sensitive'), _unit('stable', validity='stable')])['id'], 'stable')
        self.assertEqual(cu.choose_canonical([_unit('old', at='2026-01-01'), _unit('new', at='2026-06-01')])['id'], 'new')

    def test_apply_supersedes_and_logs_the_previous_audit(self):
        units, vectors = self._fixture()
        repo = _Repo(units)
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'log.jsonl'
            plans = [item for item in cu.plan(units, [vectors[unit['id']] for unit in units], 0.7)
                     if {item['canonical']['id'], *[unit['id'] for unit in item['duplicates']]} == {'u0', 'u1'}]
            changes = cu.apply(repo, plans, log)
            self.assertEqual(repo.superseded, [('u0', 'u1', 'semantic_duplicate_of_u1')])
            row = json.loads(log.read_text().splitlines()[0])
            self.assertEqual(row['unit_id'], 'u0'); self.assertEqual(row['previous_audit']['state'], 'approved')
            self.assertEqual(len(changes), 1)

    def test_unreviewed_units_are_left_alone(self):
        units = [_unit('a', state='needs_review'), _unit('b', state='not_reviewed'), _unit('c')]
        self.assertEqual([unit['id'] for unit in cu.reviewed(units)], ['c'])

    def test_worker_entry_skips_when_nothing_changed_and_never_raises(self):
        units, vectors = self._fixture()
        repo, service = _Repo(units), _Service(vectors)
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / 'state.json'
            original_log = cu.LOG; cu.LOG = Path(root) / 'log.jsonl'
            try:
                first = cu.consolidate_if_changed(repo, service, state)
                self.assertEqual(first['status'], 'consolidated'); self.assertGreaterEqual(first['applied'], 1)
                self.assertEqual(cu.consolidate_if_changed(repo, service, state)['status'], 'unchanged')
            finally:
                cu.LOG = original_log
            class Broken:
                def active_units(self): raise RuntimeError('db down')
            self.assertEqual(cu.consolidate_if_changed(Broken(), service, state)['status'], 'degraded')


if __name__ == '__main__':
    unittest.main()
