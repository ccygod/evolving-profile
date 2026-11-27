"""Agent-facing projection of tool results: content stays, audit detail goes to the ledger."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import host_projection as hp


def _block(value):
    return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}], 'isError': False}


def _text(result):
    return json.loads(result['content'][0]['text'])


RECALL = {
    'research_id': 'r-1', 'next_offset': 8, 'next_action': {'tool': 'read_research'}, 'query_completion': 'candidate_set_read',
    'memories': [{'id': 'm-1', 'type': 'world', 'text': '车牌：津A', 'state': 'valid', 'mentioned_at': '2026-11-01',
                  'document_id': 'd-1', 'chunk_id': 'c' * 80, 'relevance': {'level': 'strong', 'score': 0.9},
                  'relevance_match_signals': {'x': 'y' * 1200}, 'source_locator': {'tool': 'read_source', 'memory_id': 'm-1'},
                  'metadata': {'source_role': 'user', 'origin_agent': 'codex', 'retention_item_ids': 'z' * 500}}],
    'relevance_audit': {'big': 'a' * 20000}, 'facet_receipts': [{'q': 1}], 'jev_review': {'status': 'ok'},
    'tool_call_id': 'mcp:1', 'observability_binding': {'state': 'unknown_check_id'}, 'adapter_build_sha256': 'f' * 64,
    'observability_warning': 'attribution unverified', 'unavailable': [], 'filtered': [],
    'scenario_followup': {'decision': 'agent_decides', 'reason': 'x', 'scenarios': [{'scenario_id': 's1', 'navigation_title': 't', 'extra': 'e' * 900}]},
}


class ProjectionTests(unittest.TestCase):
    def test_recall_keeps_content_and_next_steps_and_drops_audit_detail(self):
        out = hp.project('user_recall', RECALL)
        self.assertEqual(out['memories'][0], {'id': 'm-1', 'type': 'world', 'text': '车牌：津A', 'state': 'valid',
                                              'mentioned_at': '2026-11-01', 'document_id': 'd-1',
                                              'source_role': 'user', 'origin_agent': 'codex', 'relevance': 'strong'})
        for key in ('research_id', 'next_offset', 'next_action', 'query_completion', 'observability_warning'):
            self.assertIn(key, out)
        for key in ('relevance_audit', 'facet_receipts', 'jev_review', 'tool_call_id', 'unavailable'):
            self.assertNotIn(key, out)
        self.assertEqual(out['observability_binding'], {'state': 'unknown_check_id'})
        self.assertEqual(out['scenario_followup']['scenarios'], [{'scenario_id': 's1', 'navigation_title': 't'}])
        self.assertLess(len(json.dumps(out)), len(json.dumps(RECALL)) // 10)

    def test_preference_keeps_what_loaded_and_the_host_receipt_check_need(self):
        unit = {'id': 'u-1', 'revision': 'r1', 'content_sha256': 'h', 'text': '修复后复测', 'applies_when': ['修复后'],
                'scope': {'user_id': 'me', 'domains': [], 'project_ids': ['p1']}, 'evidence_manifest': [{'a': 1}],
                'selection_reason': {'tier': 'core', 'lexical': 0.1, 'applicability_risks': ['domain_condition_unmentioned']},
                'preference_audit': {'state': 'approved', 'validity_kind': 'context_sensitive'}, 'may_authorize_action': False}
        value = {'included': [unit], 'preference_candidates': [unit], 'stable_profile': [], 'coverage': 'complete_active_set',
                 'next_cursor': None, 'deferred': [{'id': 'u-9', 'reason': 'budget'}],
                 'delivery_receipt': {'occurrence_id': 'o-1', 'record_ids': ['u-1'], 'content_sha256': 's', 'guidance_items': [unit], 'task': {'x': 1}}}
        out = hp.project('user_preference', value)
        self.assertEqual(out['included'], [{'id': 'u-1', 'revision': 'r1', 'content_sha256': 'h', 'text': '修复后复测',
                                            'applies_when': ['修复后'], 'scope': {'project_ids': ['p1']}, 'match': 'core',
                                            'applicability_risks': ['domain_condition_unmentioned'], 'validity': 'context_sensitive'}])
        self.assertNotIn('preference_candidates', out)
        self.assertEqual(out['delivery_receipt'], {'occurrence_id': 'o-1', 'record_ids': ['u-1'], 'content_sha256': 's'})
        self.assertEqual(out['deferred'], [{'id': 'u-9', 'reason': 'budget'}])

    def test_catalog_listing_is_trimmed_but_drill_down_is_not(self):
        row = {'topic_id': 't-1', 'title': 'EP', 'abstract': 'a', 'overview': 'o' * 900, 'child_previews': ['p' * 3000],
               'children': ['c1', 'c2'], 'source_locators': [{'m': 1}] * 5, 'coverage': {'total': 9, 'sampled': 3, 'semantics': 's' * 70},
               'pending_changes': 0, 'review': {'state': 'needs_review', 'reason': 'mixed'}}
        listed = hp.project('catalog_list', {'items': [row], 'coverage_totals': {'knowledge_pages': 7}})['items'][0]
        self.assertNotIn('child_previews', listed); self.assertNotIn('pending_changes', listed)
        self.assertEqual(listed['children_count'], 2); self.assertEqual(listed['source_locator_count'], 5)
        self.assertEqual(listed['coverage'], {'total': 9, 'sampled': 3}); self.assertEqual(listed['review']['state'], 'needs_review')
        self.assertLessEqual(len(listed['overview']), hp.CATALOG_OVERVIEW_CHARS + 1)
        read = hp.project('catalog_read', {'items': [row]})['items'][0]
        self.assertEqual(read['child_previews'], row['child_previews']); self.assertEqual(len(read['overview']), 900)

    def test_layer_labels_and_read_only_promise_survive(self):
        out = hp.project('rag_search', {'results': [], 'plane': 'external_rag', 'stage': 'retrieve',
                                        'long_term_model_created': False, 'tool_call_id': 'x'})
        self.assertEqual(out, {'plane': 'external_rag', 'stage': 'retrieve', 'long_term_model_created': False})

    def test_unknown_tools_only_lose_audit_keys(self):
        out = hp.project('read_source', {'source': {'text': '原文' * 500}, 'memory': {'id': 'm'}, 'tool_call_id': 'x', 'mcp_request_id': 7})
        self.assertEqual(out, {'source': {'text': '原文' * 500}, 'memory': {'id': 'm'}})


class CompactResultTests(unittest.TestCase):
    def test_compacts_in_place_and_reports_it(self):
        result = _block(RECALL)
        self.assertTrue(hp.compact_result('user_recall', result))
        self.assertNotIn('relevance_audit', _text(result))

    def test_verbose_and_environment_switch_return_the_full_value(self):
        result = _block(RECALL)
        self.assertFalse(hp.compact_result('user_recall', result, verbose=True))
        self.assertEqual(_text(result), RECALL)
        os.environ['EVOLVING_PROFILE_MCP_RESPONSE'] = 'full'
        try:
            self.assertFalse(hp.compact_result('user_recall', result))
        finally:
            del os.environ['EVOLVING_PROFILE_MCP_RESPONSE']

    def test_errors_and_non_json_text_arrive_whole(self):
        failed = {'content': [{'type': 'text', 'text': json.dumps({'error': 'x', 'tool_call_id': 't'})}], 'isError': True}
        self.assertFalse(hp.compact_result('user_recall', failed)); self.assertIn('tool_call_id', _text(failed))
        plain = {'content': [{'type': 'text', 'text': 'not json'}], 'isError': False}
        self.assertFalse(hp.compact_result('user_recall', plain)); self.assertEqual(plain['content'][0]['text'], 'not json')


class HostReceiptCompatibilityTests(unittest.TestCase):
    def test_projected_preference_result_still_passes_the_host_receipt_fields(self):
        value = {'included': [{'id': 'u-1', 'revision': 'r', 'text': 't'}],
                 'delivery_receipt': {'occurrence_id': 'o-1', 'record_ids': ['u-1'], 'content_sha256': 's', 'kind': 'k'}}
        payload = hp.project('user_preference', value)
        embedded = payload['delivery_receipt']
        record_ids = [str(item.get('id')) for item in payload.get('included') or [] if item.get('id')]
        self.assertTrue(embedded['occurrence_id']); self.assertEqual(record_ids, list(embedded.get('record_ids') or []))
        self.assertEqual(embedded['content_sha256'], 's')


if __name__ == '__main__':
    unittest.main()
