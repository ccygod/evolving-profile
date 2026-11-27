"""Supplied material in a user turn is stored as quotation, not user testimony."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.retention_queue import non_testimony_ranges
from lib.supplied_material import supplied_material_ranges


def marked(text, ranges):
    return ''.join(text[a:b] for a, b in ranges)


class SuppliedMaterialTests(unittest.TestCase):
    def test_ordinary_typed_and_dictated_messages_are_all_user_words(self):
        for text in ('右边房的窗户不对，不是落地窗！应该是4扇普通窗户',
                     '1. 预算控制在两千万以内\n2. 先出初步设计\n3. 周五前给我',
                     '你给我找找啊，这个暗黑破坏神2里俘获的暗金色宝宝的排名。' * 40):  # long but one line
            self.assertEqual(supplied_material_ranges(text), [], text[:20])

    def test_fenced_blocks_tag_blocks_and_table_rows_are_material(self):
        text = '看下这个报错\n```\nTraceback ...\nValueError: x\n```\n还有这个表\n| 型号 | 数量 |\n| A | 4 |\n帮我分析'
        rest = text
        for a, b in reversed(supplied_material_ranges(text)):
            rest = rest[:a] + rest[b:]
        self.assertIn('看下这个报错', rest); self.assertIn('帮我分析', rest)
        self.assertNotIn('Traceback', rest); self.assertNotIn('型号', rest)
        event = '<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>'
        self.assertEqual(marked(event, supplied_material_ranges(event)), event)
        self.assertEqual(marked('31\t服务器\t台\t4', supplied_material_ranges('31\t服务器\t台\t4')), '31\t服务器\t台\t4')

    def test_long_tall_message_keeps_only_short_opening_and_closing_as_user_words(self):
        body = '\n'.join(f'第{i}条 监控点位建设说明，覆盖楼内楼道区域与外围，保存时间不少于三十天。' for i in range(60))
        text = '把下面的材料整理成表格：\n\n' + body + '\n\n日期只写到月份。'
        ranges = supplied_material_ranges(text)
        material = marked(text, ranges)
        self.assertIn('第30条', material)
        self.assertNotIn('把下面的材料整理成表格', material); self.assertNotIn('日期只写到月份', material)

    def test_inline_mention_of_a_tag_is_not_material(self):
        self.assertEqual(supplied_material_ranges('这个 <system-reminder> 标签是干什么的？'), [])

    def test_ranges_are_sorted_merged_and_inside_the_text(self):
        text = '```\na\n```\n| a | b |\n' + '<r>x</r>'
        ranges = supplied_material_ranges(text)
        self.assertEqual(ranges, sorted(ranges))
        for (a, b), (c, d) in zip(ranges, ranges[1:]):
            self.assertLess(b, c)
        self.assertTrue(all(0 <= a < b <= len(text) for a, b in ranges))


class NonTestimonyRangeTests(unittest.TestCase):
    def test_supplied_material_applies_to_user_role_only(self):
        # A table row is not a quotation, so only the supplied-material rule can mark it.
        text = '结果如下\n| 型号 | 数量 |\n| A | 4 |'
        self.assertTrue(any('型号' in text[a:b] for a, b in non_testimony_ranges('user', text)))
        self.assertEqual(non_testimony_ranges('assistant', text), [])

    def test_explicit_quotes_still_count_for_every_role(self):
        text = '他说“下周交付”，我没同意'
        for role in ('user', 'assistant'):
            self.assertEqual([text[a:b] for a, b in non_testimony_ranges(role, text)], ['“下周交付”'])


if __name__ == '__main__':
    unittest.main()
