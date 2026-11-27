"""Entry signal-to-noise: host envelopes, short follow-ups, per-session delivery."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guidance_entry
from guidance_entry import SessionDeliveryLedger, render_entry, reset_session_delivery
from lib.content import extract_task_user_request, extract_user_request


def _result():
    unit = {'id': 'u-1', 'revision': 'r1', 'text': '修复后复测同题', 'applies_when': ['修复后']}
    stable = {'id': 's-1', 'revision': 'r1', 'text': '先锁定目标和验收标准'}
    return {'included': [unit], 'stable_profile': [stable], 'model_sections': [], 'deferred': [], 'coverage': 'complete_active_set'}


class HostEnvelopeTests(unittest.TestCase):
    def test_leading_system_reminders_are_not_the_request(self):
        prompt = '<system-reminder>\nscratch workspace notes\n</system-reminder><system-reminder>more</system-reminder>\n你看看EP接入的怎么样了'
        self.assertEqual(extract_user_request(prompt), '你看看EP接入的怎么样了')
        self.assertEqual(extract_task_user_request(prompt), '你看看EP接入的怎么样了')

    def test_task_notification_is_a_host_event_not_a_prompt(self):
        event = ('[SYSTEM NOTIFICATION - NOT USER INPUT]\nautomated event\n\n<task-notification>\n<task-id>b1</task-id>\n'
                 '<status>completed</status>\n<summary>done</summary>\n</task-notification>')
        self.assertEqual(extract_task_user_request(event), '')
        self.assertEqual(extract_user_request('<task-notification><task-id>b1</task-id><status>ok</status></task-notification>'), '')

    def test_quoted_or_partial_envelopes_stay_user_content(self):
        quoted = '这个 <system-reminder> 标签是干什么的？'
        self.assertEqual(extract_task_user_request(quoted), quoted)
        mixed = '解释一下 <task-notification><task-id>b1</task-id><status>ok</status></task-notification>'
        self.assertEqual(extract_task_user_request(mixed), mixed)
        unclosed = '<system-reminder>没有闭合 帮我看看'
        self.assertEqual(extract_task_user_request(unclosed), unclosed)


class ShortFollowupTests(unittest.TestCase):
    def _request(self, prompt, context):
        original = guidance_entry._active_context
        guidance_entry._active_context = lambda hook_input, value: context
        try:
            return guidance_entry.build_request({'session_id': 's'}, prompt, 'entry:x')
        finally:
            guidance_entry._active_context = original

    def test_short_message_in_active_session_continues_the_task(self):
        request = self._request('加重试', '最近用户前文：\n查 worker 报错')
        self.assertTrue(request['task']['continuation'])
        self.assertIn('worker', request['task']['context_summary'])

    def test_short_message_without_prior_context_is_standalone(self):
        self.assertFalse(self._request('加重试', '')['task']['continuation'])

    def test_long_unrelated_message_does_not_inherit_context(self):
        request = self._request('帮我写一份面向高校领导的智慧教室共建方案，重点讲清楚建设必要性和预算构成', '最近用户前文：\n查 worker 报错')
        self.assertFalse(request['task']['continuation'])
        self.assertEqual(request['task']['context_summary'], '')

    def test_topic_switch_and_greeting_are_never_continuations(self):
        self.assertFalse(self._request('换个话题，天气如何', '最近用户前文：\nx')['task']['continuation'])
        self.assertFalse(self._request('谢谢', '最近用户前文：\nx')['task']['continuation'])


class SessionDeliveryTests(unittest.TestCase):
    def test_unchanged_guidance_is_sent_once_per_session(self):
        with tempfile.TemporaryDirectory() as root:
            first = SessionDeliveryLedger(root, 'session-a')
            text, rendered = render_entry(_result(), include_instruction=False, ledger=first); first.save()
            self.assertIn('修复后复测同题', text); self.assertIn('先锁定目标', text)
            second = SessionDeliveryLedger(root, 'session-a')
            text, rendered = render_entry(_result(), include_instruction=False, ledger=second); second.save()
            self.assertEqual(text, '')
            self.assertEqual(sorted(rendered['already_delivered_this_session']), ['s-1', 'u-1'])

    def test_new_item_is_sent_and_repeats_are_only_counted(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = SessionDeliveryLedger(root, 's'); render_entry(_result(), include_instruction=False, ledger=ledger); ledger.save()
            result = _result(); result['included'].append({'id': 'u-2', 'revision': 'r1', 'text': '建议按推荐度排序'})
            ledger = SessionDeliveryLedger(root, 's')
            text, _ = render_entry(result, include_instruction=False, ledger=ledger)
            self.assertIn('建议按推荐度排序', text); self.assertNotIn('修复后复测同题', text)
            self.assertIn('另有 2 条本会话此前已送达', text)

    def test_changed_revision_other_session_and_reset_deliver_again(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = SessionDeliveryLedger(root, 's'); render_entry(_result(), include_instruction=False, ledger=ledger); ledger.save()
            changed = _result(); changed['included'][0]['revision'] = 'r2'
            self.assertIn('修复后复测同题', render_entry(changed, include_instruction=False, ledger=SessionDeliveryLedger(root, 's'))[0])
            self.assertIn('修复后复测同题', render_entry(_result(), include_instruction=False, ledger=SessionDeliveryLedger(root, 'other'))[0])
            reset_session_delivery('s', root)
            self.assertIn('修复后复测同题', render_entry(_result(), include_instruction=False, ledger=SessionDeliveryLedger(root, 's'))[0])

    def test_guidance_is_refreshed_after_many_prompts(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = SessionDeliveryLedger(root, 's'); render_entry(_result(), include_instruction=False, ledger=ledger); ledger.save()
            for _ in range(guidance_entry.REDELIVER_AFTER_PROMPTS - 1):
                ledger = SessionDeliveryLedger(root, 's'); text, _ = render_entry(_result(), include_instruction=False, ledger=ledger); ledger.save()
                self.assertEqual(text, '')
            ledger = SessionDeliveryLedger(root, 's')
            self.assertIn('修复后复测同题', render_entry(_result(), include_instruction=False, ledger=ledger)[0])

    def test_without_ledger_rendering_is_unchanged(self):
        text, rendered = render_entry(_result(), include_instruction=False)
        self.assertIn('修复后复测同题', text); self.assertEqual(rendered['already_delivered_this_session'], [])


if __name__ == '__main__':
    unittest.main()
