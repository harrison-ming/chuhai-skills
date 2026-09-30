"""Frontmatter guard: SKILL.md must parse under strict YAML parsers.

Lenient parsers (Claude) accept ``key: text with: colon``; strict ones
(PyYAML, the ``skills`` CLI, some domestic agents) reject it and silently
drop the skill. Plain scalars must not contain ": " or " #".
"""

import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frontmatter_lines(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        raise AssertionError("%s: missing frontmatter" % path)
    return parts[1].splitlines()


class SkillFrontmatterTest(unittest.TestCase):
    def test_plain_scalars_are_strict_yaml_safe(self):
        files = glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
        self.assertTrue(files)
        for path in files:
            keys = set()
            for line in frontmatter_lines(path):
                m = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s?(.*)$", line)
                if not m:
                    continue
                if not m.group(1):
                    keys.add(m.group(2))
                value = m.group(3)
                if not value or value[0] in "\"'>|[{":
                    continue
                self.assertNotIn(": ", value, "%s: %s" % (path, line))
                self.assertNotIn(" #", value, "%s: %s" % (path, line))
            self.assertTrue({"name", "description"} <= keys, path)


if __name__ == "__main__":
    unittest.main()
