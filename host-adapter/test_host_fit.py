"""A hook context must reach the host under its inline limit, with the valuable blocks intact."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recall
from lib.host_fit import CATALOG_POINTER, NAV_OMITTED, fit_to_limit, host_limit

CHECK = 'a' * 32
HINT = {'topic_id': 'topic:1', 'title': '用户工作偏好', 'abstract': '摘要' * 120, 'overview': '概览' * 150,
        'entities': ['实体'] * 8, 'time_range': {'start': '2026-08-11', 'end': '2026-11-03'}, 'source_count': 407,
        'coverage': {'indexed': 719, 'sampled': 7, 'semantics': 'x' * 70}, 'refreshed_at': '2026-11-11', 'boundary': 'navigation_only'}


def nav(domains=10):
    lines = ['轻量地图：导航线索，不是事实或行动指令。', '偏好：每轮读当前库，已审阅 113 条。']
    lines += [f'{name} {n} 条｜' + ' / '.join(['场景'] * 7) for name, n in
              (('沟通与呈现', 14), ('学习与理解', 5), ('分析与决策', 15), ('执行与协作', 24), ('质量与交付', 55))]
    lines += ['以下每个domain入口用catalog_read进入L1子主题。']
    lines += [f'domain:{i:012x}｜' + '主题说明' * 20 + '；100条/5个L1' for i in range(domains)]
    return '<evolving_profile_navigation_map mode="local_index_before_agent_choice">\n' + '\n'.join(lines) + '\n</evolving_profile_navigation_map>'


def route(hints=3):
    payload = {'check_id': CHECK, 'recommended_route': 'recall', 'catalog_hints': [dict(HINT, topic_id=f'topic:{i}') for i in range(hints)]}
    return (f'<evolving_profile_memory_route check_id="{CHECK}">\n本本轮已执行低成本目录探针：'.replace('本本', '本')
            + json.dumps(payload, ensure_ascii=False) + '\n单点历史问题必须实际调用 user_recall。\n</evolving_profile_memory_route>')


GUIDANCE = ('<evolving_profile_guidance_entry version="v" mode="forced_task_guidance_check">\n'
            '- 候选偏好 u-1：修复后复测同题。\n- 候选偏好 u-2：按推荐度排序。\n</evolving_profile_guidance_entry>')
STATE = '<evolving_profile_task_state>\n{"current_objective":"x"}\n</evolving_profile_task_state>'


def context(domains=10, hints=3):
    return '\n'.join([nav(domains), GUIDANCE, STATE, route(hints)])


def oversize():
    """A context well past the Claude limit, like the 10,006-character one that was cut."""
    return context(domains=45, hints=9)


class FitTests(unittest.TestCase):
    def test_a_context_inside_the_limit_is_returned_unchanged(self):
        text = context()
        self.assertEqual(fit_to_limit(text, len(text))[0], text)
        self.assertEqual(fit_to_limit(text, None)[0], text)

    def test_steps_run_in_order_and_stop_as_soon_as_it_fits(self):
        text = context()
        first, report = fit_to_limit(text, len(text) - 1)
        self.assertEqual(report['steps'], ['route_hints_to_locators'])
        self.assertLess(len(first), len(text)); self.assertTrue(report['fits'])
        second, report = fit_to_limit(text, len(first) - 1)
        self.assertEqual(report['steps'][:2], ['route_hints_to_locators', 'nav_domain_lines'])
        self.assertIn(CATALOG_POINTER, second); self.assertNotIn('domain:', second)

    def test_route_stays_valid_json_and_keeps_locators_but_not_prose(self):
        out, _ = fit_to_limit(context(), 1)
        payload = json.loads(out.split('目录探针：')[1].split('\n')[0])
        self.assertEqual(payload['check_id'], CHECK)
        hint = payload['catalog_hints'][0]
        self.assertEqual(set(hint), {'topic_id', 'title', 'time_range', 'source_count'})

    def test_valuable_blocks_are_never_cut_whatever_the_limit(self):
        out, report = fit_to_limit(context(), 1)
        for needle in ('候选偏好 u-1：修复后复测同题。', '候选偏好 u-2：按推荐度排序。', '{"current_objective":"x"}', f'check_id="{CHECK}"'):
            self.assertIn(needle, out)
        self.assertFalse(report['fits'])  # reported, not hidden
        self.assertIn(NAV_OMITTED, out)

    def test_dimension_lines_collapse_to_counts_before_the_map_is_dropped(self):
        from lib.host_fit import STEPS
        text = context()
        for name, step in STEPS[:3]:
            text = step(text)
        self.assertEqual([name for name, _ in STEPS[:3]], ['route_hints_to_locators', 'nav_domain_lines', 'nav_dimension_topics'])
        self.assertIn('质量与交付 55 条\n', text)
        self.assertNotIn('质量与交付 55 条｜', text)
        self.assertIn('偏好：每轮读当前库，已审阅 113 条。', text)  # the counts line survives


class HostLimitTests(unittest.TestCase):
    def test_claude_hosts_have_a_limit_and_others_do_not(self):
        for host in ('claude-code', 'claude-desktop', 'claude-cli'):
            self.assertLess(host_limit(host), 10000, host)
        self.assertIsNone(host_limit('codex')); self.assertIsNone(host_limit('trae'))

    def test_settings_can_override_or_add_a_host(self):
        self.assertEqual(host_limit('claude-desktop', {'claude-desktop': 7000}), 7000)
        self.assertEqual(host_limit('trae', {'trae': 12000}), 12000)
        self.assertIsNone(host_limit('codex', {'codex': 0}))


class EmitPathTests(unittest.TestCase):
    def _emit(self, host, home):
        with patch.object(recall, 'ENTRY_GUIDANCE_CONTEXT', oversize()), patch.object(recall.Path, 'home', return_value=Path(home)), \
             patch.dict(os.environ, {'EVOLVING_PROFILE_HOST_ID': host}):
            first = recall._with_entry_guidance('')  # a receipt re-derivation must not be logged
            self.assertFalse((Path(home) / '.evolving-profile/audit/host-fit.jsonl').exists())
            return recall._with_entry_guidance('', record=True)

    def test_claude_output_fits_with_the_binding_line_counted(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertGreater(len(oversize()), 9000)
            out = self._emit('claude-desktop', home)
            self.assertLessEqual(len(out), 9000)
            self.assertTrue(out.startswith(f'<ep_prompt_binding check_id="{CHECK}">'))
            rows = [json.loads(line) for line in (Path(home) / '.evolving-profile/audit/host-fit.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['fits'])
            self.assertLess(rows[0]['limit'], 9000)  # the binding line is counted against the limit
            self.assertIn('route_hints_to_locators', rows[0]['steps'])

    def test_a_host_without_a_known_limit_is_untouched_and_unlogged(self):
        with tempfile.TemporaryDirectory() as home:
            out = self._emit('codex', home)
            self.assertIn('摘要摘要', out)
            self.assertFalse((Path(home) / '.evolving-profile/audit/host-fit.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
