"""Every shipped skill: SKILL.md conventions, acceptance.json schema,
half-width punctuation, and the marketplace in sync with SKILL.md versions.

Strict YAML matters: lenient parsers (Claude) accept ``key: text with: colon``;
strict ones (PyYAML, the ``skills`` CLI, some domestic agents) reject it and
silently drop the skill.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skill_rules  # noqa: E402

gen_marketplace = skill_rules.gen_marketplace


class SkillFolderTest(unittest.TestCase):
    def test_there_are_skills_and_template_is_excluded(self):
        names = [os.path.basename(d) for d in skill_rules.real_skills()]
        self.assertIn("shopify-store-teardown", names)
        self.assertNotIn("_template", names)

    def test_skill_md(self):
        for d in skill_rules.real_skills():
            self.assertEqual(skill_rules.check_skill_md(d), [], d)

    def test_acceptance_json(self):
        for d in skill_rules.real_skills():
            self.assertEqual(skill_rules.check_acceptance(d), [], d)

    def test_halfwidth_punctuation(self):
        for d in skill_rules.real_skills():
            self.assertEqual(skill_rules.check_halfwidth(d), [], d)

    def test_marketplace_generated(self):
        self.assertEqual(gen_marketplace.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
