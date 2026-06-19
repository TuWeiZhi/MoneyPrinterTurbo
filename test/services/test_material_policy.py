import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import material_policy


class TestMaterialPolicy(unittest.TestCase):
    def test_auto_policy_avoids_people_for_chinese_language(self):
        policy = material_policy.resolve_material_policy(
            video_language="zh-CN",
            video_subject="AI productivity",
            video_script="",
        )

        self.assertTrue(policy.is_chinese_content)
        self.assertTrue(policy.avoid_people)
        self.assertEqual(policy.reason, "chinese_content_auto")

    def test_auto_policy_detects_china_specific_keywords(self):
        policy = material_policy.resolve_material_policy(
            video_subject="\u4e0a\u6d77\u4e00\u65e5\u6e38",
            video_script="\u5750\u5730\u94c1\u53bb\u770b\u5916\u6ee9",
        )

        self.assertTrue(policy.is_chinese_content)
        self.assertTrue(policy.is_china_context)
        self.assertTrue(policy.avoid_people)

    def test_global_locale_keeps_default_people_allowed(self):
        policy = material_policy.resolve_material_policy(
            video_language="zh-CN",
            video_subject="\u4e0a\u6d77\u4e00\u65e5\u6e38",
            material_locale="global",
        )

        self.assertFalse(policy.is_china_context)
        self.assertFalse(policy.avoid_people)

    def test_explicit_people_filter_overrides_auto(self):
        allow_policy = material_policy.resolve_material_policy(
            video_language="zh-CN",
            people_filter="allow",
        )
        avoid_policy = material_policy.resolve_material_policy(
            video_language="en-US",
            people_filter="avoid",
        )

        self.assertFalse(allow_policy.avoid_people)
        self.assertTrue(avoid_policy.avoid_people)

    def test_adapt_search_terms_replaces_people_words(self):
        policy = material_policy.resolve_material_policy(
            video_language="zh-CN",
            video_subject="\u804c\u573a\u6548\u7387",
        )

        terms = material_policy.adapt_search_terms_for_policy(
            ["businessman office", "city skyline"], policy
        )

        self.assertEqual(terms, ["office desk no people", "city skyline no people"])


if __name__ == "__main__":
    unittest.main()
