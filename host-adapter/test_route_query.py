"""The route probe judges intent from the current message, not from the context the Hook appends."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from system_probe import ROUTE_CONTEXT_MARK, plan_history, split_route_query

HEADER = '最近用户前文（仅消解本轮指代，不继承旧授权）：\n'


def combined(current, *earlier):
    return current + ROUTE_CONTEXT_MARK + HEADER + '\n'.join(earlier)


class SplitTests(unittest.TestCase):
    def test_splits_message_from_context_and_drops_the_entry_header(self):
        self.assertEqual(split_route_query(combined('加重试', '查 worker 报错')), ('加重试', '查 worker 报错'))

    def test_no_marker_means_no_context(self):
        self.assertEqual(split_route_query('加重试'), ('加重试', ''))


class RouteIntentTests(unittest.TestCase):
    def test_entry_boilerplate_does_not_turn_follow_ups_into_live_audits(self):
        # "本轮" appears in the header the entry itself prepends.
        for current in ('加重试', '路由和说明重写', '还剩哪些建议没执行呢', '保留召回优先'):
            plan = plan_history(combined(current, '修复测试集'))
            self.assertEqual(plan['recommended_route'], 'recall', current)
            self.assertNotEqual(plan['minimum_action'], 'live_audit', current)

    def test_a_real_audit_request_is_still_a_live_audit_with_or_without_context(self):
        for text in ('检查上一轮的调用记录，有没有调用 recall', '看看本轮的工具回执'):
            self.assertEqual(plan_history(text)['recommended_route'], 'live_audit', text)
            self.assertEqual(plan_history(combined(text, '修复测试集'))['recommended_route'], 'live_audit', text)

    def test_audit_words_in_earlier_turns_do_not_make_the_next_message_an_audit(self):
        plan = plan_history(combined('加重试', '检查上一轮的调用记录，有没有调用 recall'))
        self.assertEqual(plan['recommended_route'], 'recall')

    def test_context_still_supplies_anchors_for_a_short_follow_up(self):
        plan = plan_history(combined('加重试', '查 guidance worker 报错'))
        self.assertTrue(any('worker' in term for term in plan['focus_terms']), plan['focus_terms'])
        self.assertNotIn('必要前文', plan['query'])  # the marker is routing plumbing, not query text
        self.assertIn('worker', plan['query'])

    def test_a_self_contained_message_is_judged_on_its_own(self):
        self.assertEqual(plan_history(combined('17乘以23等于多少？', '修复测试集'))['recommended_route'], 'skip')

    def test_memory_prohibition_in_the_message_is_still_honoured(self):
        plan = plan_history(combined('本轮禁用长期记忆，只计算26加37', '修复测试集'))
        self.assertEqual(plan['recommended_route'], 'skip')


class ContextFilterTests(unittest.TestCase):
    def test_host_events_are_not_earlier_user_turns(self):
        import json, tempfile
        import guidance_entry
        event = ('[SYSTEM NOTIFICATION - NOT USER INPUT]\nautomated\n\n<task-notification>\n<task-id>b1</task-id>\n'
                 '<status>completed</status>\n</task-notification>')
        with tempfile.TemporaryDirectory() as root:
            ingress = Path(root) / 'ingress.jsonl'
            ingress.write_text('\n'.join(json.dumps({'session_id': 's', 'prompt_preview': p}) for p in ('修复测试集', event)))
            original = guidance_entry.PROMPT_INGRESS
            guidance_entry.PROMPT_INGRESS = ingress
            try:
                context = guidance_entry._active_context({'session_id': 's'}, '加重试')
            finally:
                guidance_entry.PROMPT_INGRESS = original
        self.assertIn('修复测试集', context)
        self.assertNotIn('task-notification', context)


if __name__ == '__main__':
    unittest.main()


class AdmissionTurnIdentityTests(unittest.TestCase):
    """A host that sends no turn id must still produce a complete admission binding."""

    def test_codex_turn_id_is_kept(self):
        import recall
        self.assertEqual(recall.admission_turn_id({'turn_id': 't-1'}, 'inv'), 't-1')

    def test_a_host_without_a_turn_id_uses_the_prompt_invocation(self):
        import recall
        for hook_input in ({}, {'turn_id': None}, {'turn_id': ' '}):
            self.assertEqual(recall.admission_turn_id(hook_input, 'abc123'), 'invocation:abc123')
        self.assertEqual(recall.admission_turn_id({}, ''), '')

    def test_header_and_verification_context_agree(self):
        import recall
        hook_input = {'session_id': 's'}
        headers = recall.controller_turn_headers(hook_input, 'rev', 'shadow', 'abc123')
        self.assertEqual(headers['X-Memory-Turn-Id'], recall.admission_turn_id(hook_input, 'abc123'))

    def test_controller_accepts_the_binding_the_hook_now_sends(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
        from lib.admission_contract import CONTEXT_FIELDS, make_decision
        import recall
        context = {'session_id': 's', 'turn_id': recall.admission_turn_id({}, 'abc123'), 'hook_invocation_id': 'abc123',
                   'execution_id': 'e', 'prompt_sha256': 'p', 'policy_version': 'v', 'source_revision': 'r'}
        self.assertTrue(all(context[field] for field in CONTEXT_FIELDS))
        make_decision({'id': 'm', 'type': 'world', 'text': 't'}, context, 'admit', 'qualified')  # must not raise
        with self.assertRaisesRegex(ValueError, 'turn_id'):
            make_decision({'id': 'm', 'type': 'world', 'text': 't'}, dict(context, turn_id=''), 'admit', 'qualified')


class FindSourcesArgumentTests(unittest.TestCase):
    def setUp(self):
        import evolving_profile_controller_mcp as mcp
        self.check = mcp.find_sources_arguments

    def test_a_query_instead_of_terms_says_what_to_pass(self):
        with self.assertRaisesRegex(ValueError, r'does not take query.*terms=\[\.\.\.\].*user_recall'):
            self.check({'query': '接入 Codex'})

    def test_missing_or_empty_terms_are_explained(self):
        for args in ({}, {'terms': []}, {'terms': ['']}, {'terms': 'abc'}, {'terms': [1]}):
            with self.assertRaisesRegex(ValueError, 'needs terms'):
                self.check(args)

    def test_limits_and_enums(self):
        with self.assertRaisesRegex(ValueError, 'at most 8'):
            self.check({'terms': ['a'] * 9})
        with self.assertRaisesRegex(ValueError, 'role'):
            self.check({'terms': ['a'], 'role': 'bot'})
        with self.assertRaisesRegex(ValueError, 'match'):
            self.check({'terms': ['a'], 'match': 'some'})

    def test_a_valid_call_passes_including_pagination(self):
        self.check({'terms': ['接入 Codex', 'Codex 接入'], 'role': 'user', 'match': 'all', 'limit': 5, 'cursor': 'c', 'check_id': 'x'})
