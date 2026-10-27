"""skills/_template/ stays valid: a skill generated from it passes every
folder rule and its own synthetic tests, on the running Python."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skill_rules  # noqa: E402

sys.path.insert(0, os.path.join(skill_rules.ROOT, "tools"))
import new_skill  # noqa: E402

TEMPLATE = os.path.join(skill_rules.ROOT, "skills", "_template")


class TemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuhai-template-")
        cls.skill = new_skill.create(
            "demo-check",
            "演示体检",
            "Demo check",
            skills_dir=cls.tmp,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generated_skill_passes_folder_rules(self):
        self.assertEqual(skill_rules.check_skill_md(self.skill), [])
        self.assertEqual(skill_rules.check_acceptance(self.skill), [])
        self.assertEqual(skill_rules.check_halfwidth(self.skill), [])
        self.assertEqual(new_skill.leftovers(self.skill), [])

    def test_generated_skill_tests_pass(self):
        tests = os.path.join(self.skill, "tests")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", tests, "-t", tests],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        out = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("OK", out)

    def test_template_has_required_files(self):
        for rel in (
            "SKILL.md",
            "acceptance.json",
            "references/method.md",
            "references/report-template.zh.md",
            "references/report-template.en.md",
            "scripts/collect.py",
            "scripts/compute.py",
            "scripts/check_report.py",
            "scripts/deliver.py",
            "scripts/skill_config.py",
            "scripts/_shared/__init__.py",
            "tests/test_skill.py",
        ):
            self.assertTrue(os.path.isfile(os.path.join(TEMPLATE, rel)), rel)
        self.assertEqual(skill_rules.check_halfwidth(TEMPLATE), [])

    def test_refuses_bad_or_existing_names(self):
        for bad in ("Demo", "demo", "demo_check", "-x"):
            with self.assertRaises(ValueError):
                new_skill.create(bad, "a", "b", skills_dir=self.tmp)
        with self.assertRaises(ValueError):
            new_skill.create("demo-check", "a", "b", skills_dir=self.tmp)
        with self.assertRaises(ValueError):
            new_skill.create("demo-two", "a: b", "b", skills_dir=self.tmp)

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = new_skill.main(
                ["demo-three", "--title-zh", "三", "--title-en", "Three"]
                + ["--skills-dir", self.tmp]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(self.tmp, "demo-three", "SKILL.md"))
        )


if __name__ == "__main__":
    unittest.main()
