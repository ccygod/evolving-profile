"""Automatic topic hints skip thinly supported entities; explicit search still finds the long tail."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from topic_catalog import MIN_HINT_ENTITY_SUPPORT, TopicCatalog


def entity_topic(name, count):
    return {'topic_id': 'entity:' + name, 'level': 'L1', 'title': name, 'abstract': f'有关{name}', 'overview': '', 'entities': [name],
            'source_count': count, 'source_locators': [], 'refreshed_at': '2026-11-10T00:00:00Z'}


class HintSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = TopicCatalog(Path(self.tmp.name) / 'topics.sqlite3')
        names = {'调用流水': 1, '调用链路': 5, '调用记录': 2, '调用': 1}
        self.catalog.replace_topics([entity_topic(n, c) for n, c in names.items()], {},
                                    [{'id': 'entity:' + n, 'title': n, 'source_count': c, 'latest_source_at': None} for n, c in names.items()])

    def tearDown(self):
        self.tmp.cleanup()

    def test_hints_leave_out_entities_below_the_floor(self):
        found = [row['title'] for row in self.catalog.hint_search('调用链', 8)]
        self.assertIn('调用链路', found)
        self.assertNotIn('调用记录', found)
        self.assertNotIn('调用流水', found)

    def test_a_prompt_that_names_a_thin_entity_exactly_still_hints_it(self):
        self.assertEqual(self.catalog.hint_search('调用流水', 8)[0]['title'], '调用流水')
        self.assertEqual(self.catalog.hint_search('调用', 8)[0]['title'], '调用')

    def test_explicit_search_keeps_the_long_tail(self):
        found = [row['title'] for row in self.catalog.search('调用', 8)]
        self.assertIn('调用流水', found)
        self.assertIn('调用记录', found)

    def test_the_floor_is_three_sources(self):
        self.assertEqual(MIN_HINT_ENTITY_SUPPORT, 3)


if __name__ == '__main__':
    unittest.main()
