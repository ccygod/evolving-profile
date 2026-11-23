import unittest

from lib.retain_tags import host_family, retain_tags

BASE = ["codex", "project:abc", "observation:v2-live"]


class RetainTagsTest(unittest.TestCase):
    def test_claude_batch_drops_codex_tag_and_gets_family(self):
        tags = retain_tags(BASE, "claude-desktop")
        self.assertNotIn("codex", tags)
        self.assertIn("host:claude-desktop", tags)
        self.assertIn("host_family:claude", tags)
        self.assertIn("project:abc", tags)

    def test_codex_batch_keeps_codex(self):
        tags = retain_tags(BASE, "codex")
        self.assertIn("codex", tags)
        self.assertIn("host_family:codex", tags)

    def test_mixed_origin(self):
        tags = retain_tags(BASE, "claude-desktop,codex")
        self.assertIn("host_family:claude", tags)
        self.assertIn("host_family:codex", tags)
        self.assertEqual(len(tags), len(set(tags)))

    def test_family(self):
        self.assertEqual(host_family("claude-code"), "claude")
        self.assertEqual(host_family("workbuddy"), "workbuddy")



if __name__ == "__main__":
    unittest.main()
