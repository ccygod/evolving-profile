"""The evaluation and audit tools are small, but a threshold decision will rest on their numbers."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[2] / 'bin'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless((BIN / 'ep-eval-injection.py').is_file(), 'tools not deployed')
class EvalMathTests(unittest.TestCase):
    def setUp(self):
        self.ev = load('ep_eval_injection', 'ep-eval-injection.py')
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def labels(self, rows):
        path = Path(self.tmp.name) / 'labels.jsonl'
        path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
        return path

    def test_precision_and_recall_count_only_labelled_prompts(self):
        selection = {'p1': ['a', 'b'], 'p2': ['c'], 'p3': ['z']}
        rows = [{'prompt': 'p1', 'relevant_unit_ids': ['a', 'x']}, {'prompt': 'p2', 'relevant_unit_ids': []},
                {'prompt': 'p3', 'relevant_unit_ids': None}]  # p3 unlabelled
        with patch.object(self.ev, 'select', side_effect=lambda repo, prompt, mode: [{'id': i} for i in selection[prompt]]):
            result = self.ev.score(None, self.labels(rows), 'centered')
        self.assertEqual(result['labelled_prompts'], 2)
        self.assertEqual(result['precision'], round(1 / 3, 3))  # a is right; b and c are not
        self.assertEqual(result['recall'], 0.5)                  # a found, x missed
        self.assertEqual(result['selected_where_nothing_was_wanted'], 1)

    def test_an_empty_label_is_a_real_answer_not_a_missing_one(self):
        with patch.object(self.ev, 'select', return_value=[]):
            result = self.ev.score(None, self.labels([{'prompt': 'p', 'relevant_unit_ids': []}]), 'centered')
        self.assertEqual(result['labelled_prompts'], 1)
        self.assertIsNone(result['precision'])

    def test_replayed_prompts_skip_host_events_and_duplicates(self):
        path = Path(self.tmp.name) / 'ingress.jsonl'
        previews = ['给 worker 加重试', '给 worker 加重试', '<task-notification>x</task-notification>', '[SYSTEM NOTIFICATION] y', '修复整个测试集', 'ab']
        path.write_text('\n'.join(json.dumps({'prompt_preview': p}, ensure_ascii=False) for p in previews), encoding='utf-8')
        with patch.object(self.ev, 'INGRESS', path):
            self.assertEqual(self.ev.real_prompts(), ['给 worker 加重试', '修复整个测试集'])


@unittest.skipUnless((BIN / 'ep-audit-attribution.py').is_file(), 'tools not deployed')
class AuditTests(unittest.TestCase):
    def test_preference_evidence_flags_weak_support_only(self):
        audit = load('ep_audit_attribution', 'ep-audit-attribution.py')
        ref = lambda doc, quote='先核对来源。': {'stored_role': 'user', 'origin': 'user_direct', 'statement_kind': 'request',
                                                   'human_author_verified': False, 'document_id': doc, 'quote': quote}
        units = [{'id': 'two-docs', 'text': 't', 'evidence_refs': [ref('d1'), ref('d2')]},
                 {'id': 'one-doc', 'text': 't', 'evidence_refs': [ref('d1'), ref('d1')]},
                 {'id': 'pasted', 'text': 't', 'evidence_refs': [ref('d1', '| 型号 | 数量 |\n| A | 4 |'), ref('d2')]},
                 {'id': 'assistant', 'text': 't', 'evidence_refs': [dict(ref('d1'), stored_role='assistant'), ref('d2')]}]
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        result = audit.preference_evidence(units)
        flags = {row['id']: row['flags'] for row in result['flagged_units']}
        self.assertNotIn('two-docs', flags)
        self.assertEqual(flags['one-doc'], ['single_source_document'])
        self.assertEqual(flags['pasted'], ['structural_quote'])
        self.assertEqual(flags['assistant'], ['not_user_direct'])


if __name__ == '__main__':
    unittest.main()
