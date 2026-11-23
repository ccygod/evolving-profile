import datetime as dt
import unittest

from lib.coverage_intent import TZ, detect, instruction, resolve_window

NOW = dt.datetime(2026, 11, 21, 10, 0, tzinfo=TZ)  # Saturday


class CoverageIntentTest(unittest.TestCase):
    def window(self, text):
        return (resolve_window(text, NOW) or {}).get("start"), (resolve_window(text, NOW) or {}).get("end")

    def test_explicit_range(self):
        self.assertEqual(self.window("2026-11-15 到 2026-11-21"), ("2026-11-15", "2026-11-21"))
        self.assertEqual(self.window("11月15日到21日"), ("2026-11-15", "2026-11-21"))

    def test_month_range_rolls_to_previous_year_when_future(self):
        self.assertEqual(self.window("8–10月"), ("2026-08-01", "2026-10-31"))
        self.assertEqual(self.window("12月份"), ("2025-12-01", "2025-12-31"))

    def test_relative_in_beijing_time(self):
        self.assertEqual(self.window("本周"), ("2026-11-16", "2026-11-21"))
        self.assertEqual(self.window("上周"), ("2026-11-09", "2026-11-15"))
        self.assertEqual(self.window("上个月"), ("2026-10-01", "2026-10-31"))
        self.assertEqual(self.window("梳理 1 个月的工作"), ("2026-10-23", "2026-11-21"))
        self.assertEqual(self.window("昨天"), ("2026-11-20", "2026-11-20"))

    def test_detect_routes_inventory_and_windows(self):
        self.assertIsNotNone(detect("最新给我梳理 1 个月的工作情况", NOW))
        self.assertIsNotNone(detect("具身智能做过哪些工作，全部列出", NOW))
        self.assertIsNotNone(detect("上周和客户聊了哪些", NOW))

    def test_locator_questions_route_to_session_inventory(self):
        for text in ("之前涉及教师培训大纲是哪个对话来着", "用EP快速找一下之前做的教师和学生培训的大纲放哪了",
                     "上次那个报价表放在哪了", "我们什么时候聊过 GB10 的采购"):
            c = detect(text, NOW)
            self.assertIsNotNone(c, text)
            self.assertTrue(c["locator"], text)
        text = instruction(detect("之前涉及教师培训大纲是哪个对话来着", NOW))
        self.assertIn('"group_by": "session"', text)
        self.assertIn("不要只给语义最相近的一个", text)

    def test_detect_ignores_self_contained_prompts(self):
        for text in ("帮我翻译这句话", "今天天气怎么样", "把这个函数改一下", "这个月", "这个变量放哪个文件比较好"):
            self.assertIsNone(detect(text, NOW), text)

    def test_instruction_names_exhaustive_and_dates(self):
        text = instruction(detect("本周做了什么", NOW))
        self.assertIn("ep_exhaustive_search", text)
        self.assertIn("2026-11-16", text)


if __name__ == "__main__":
    unittest.main()
