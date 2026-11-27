"""The startup instruction is a short decision guide; the long clauses stay one call away."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import memory_usage_instructions as m

# Every route the guide sends the Agent to must still be a tool that exists.
ROUTE_TOOLS = ('user_preference', 'user_recall', 'user_research', 'read_research', 'agent_recall', 'agent_research',
               'read_source', 'find_sources', 'search_scenario_summary', 'refresh_runtime_guidance',
               'read_memory_instructions', 'catalog_list', 'catalog_search', 'catalog_read')


class InstructionContractTests(unittest.TestCase):
    def test_startup_text_stays_short_and_complete_text_is_a_superset(self):
        self.assertLess(len(m.CORE_TEXT), 2600)  # v1.9.6 was 4109
        self.assertIn(m.CORE_TEXT, m.LONG_TEXT)
        self.assertIn(m.DETAIL_RULES, m.LONG_TEXT)

    def test_every_route_names_the_tool_to_call(self):
        for tool in ROUTE_TOOLS:
            self.assertIn(tool, m.CORE_TEXT, tool)

    def test_decision_order_and_authority_are_stated(self):
        for phrase in ('当前用户要求 > 当前权威来源', '不授权任何行动', '自足任务', '不查'):
            self.assertIn(phrase, m.CORE_TEXT, phrase)

    def test_a_status_question_is_not_a_history_question(self):
        self.assertRegex(m.CORE_TEXT, r'当前的页面、端口、版本、配置或链路状态：先读取当前运行状态，不查历史Bank')

    def test_call_receipts_are_reported_only_when_relevant(self):
        self.assertIn('与历史无关的轮次不必附调用流水', m.CORE_TEXT)
        self.assertNotRegex(m.CORE_TEXT, r'最终回答必须按真实回执逐项对账')

    def test_the_text_points_to_the_long_form(self):
        self.assertIn('read_memory_instructions', m.CORE_TEXT)

    def test_version_and_hash_follow_the_text(self):
        self.assertTrue(m.VERSION.startswith('memory-use.v2'))
        self.assertEqual(m.content_sha256(), __import__('hashlib').sha256(m.CORE_TEXT.encode()).hexdigest())
        self.assertIn(m.VERSION, m.instruction_block('claude-desktop'))


if __name__ == '__main__':
    unittest.main()
