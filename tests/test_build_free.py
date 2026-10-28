"""tools/build_free.py: UTF-8 name flag, round trip, exclusions, determinism."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_free  # noqa: E402

CN_DIR = "原始数据"


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class BuildFreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="build-free-")
        self.skill = os.path.join(self.tmp, "demo-skill")
        write(os.path.join(self.skill, "SKILL.md"), "---\nname: demo-skill\n---\n")
        write(os.path.join(self.skill, "scripts", "run.py"), "print(1)\n")
        write(os.path.join(self.skill, "assets", CN_DIR, "raw", "说明.txt"), "中文\n")
        write(os.path.join(self.skill, "acceptance.json"), "{}\n")
        write(os.path.join(self.skill, "tests", "test_x.py"))
        write(os.path.join(self.skill, "scripts", "__pycache__", "run.pyc"))
        write(os.path.join(self.skill, "scripts", ".DS_Store"))
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, name="a.zip"):
        path = os.path.join(self.out, name)
        build_free.build_one(self.skill, path)
        return path

    def test_non_ascii_entries_have_utf8_flag(self):
        with zipfile.ZipFile(self.build()) as zf:
            infos = zf.infolist()
        non_ascii = [i for i in infos if not i.filename.isascii()]
        self.assertTrue(non_ascii)
        for info in non_ascii:
            self.assertTrue(info.flag_bits & 0x800, info.filename)

    def test_extraction_round_trips_names(self):
        dest = os.path.join(self.tmp, "x")
        with zipfile.ZipFile(self.build()) as zf:
            zf.extractall(dest)
        path = os.path.join(dest, "demo-skill", "assets", CN_DIR, "raw", "说明.txt")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "中文\n")

    def test_exclusions_and_layout(self):
        with zipfile.ZipFile(self.build()) as zf:
            names = sorted(zf.namelist())
        self.assertEqual(
            names,
            [
                "demo-skill/SKILL.md",
                "demo-skill/assets/%s/raw/说明.txt" % CN_DIR,
                "demo-skill/scripts/run.py",
            ],
        )

    def test_deterministic(self):
        a, b = self.build("a.zip"), self.build("b.zip")
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_archive_without_flag_fails_verify(self):
        """Info-ZIP style: UTF-8 name bytes but bit 11 unset."""
        path = os.path.join(self.out, "legacy.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("demo-skill/SKILL.md", "x")
            zf.writestr("demo-skill/cafe1.txt", "x")
        with open(path, "rb") as fh:
            data = fh.read()
        # same length, so headers stay valid
        data = data.replace(b"cafe1", "caf\u00e9".encode("utf-8"))
        with open(path, "wb") as fh:
            fh.write(data)
        with zipfile.ZipFile(path) as zf:
            self.assertFalse(any(i.flag_bits & 0x800 for i in zf.infolist()))
        with self.assertRaises(build_free.BuildError):
            build_free.verify(path, "demo-skill")


class RealSkillsTest(unittest.TestCase):
    def test_every_skill_builds_and_ships_ascii_names(self):
        """Shipped names stay ASCII: some unzip tools ignore the UTF-8 flag."""
        tmp = tempfile.mkdtemp(prefix="build-free-real-")
        try:
            for name in build_free.skill_names():
                path = os.path.join(tmp, name + ".zip")
                entries = build_free.build_one(
                    os.path.join(build_free.SKILLS, name), path
                )
                self.assertIn(name + "/SKILL.md", entries)
                bad = [e for e in entries if not e.isascii()]
                self.assertEqual(bad, [], name)
                self.assertFalse(any("/tests/" in e for e in entries), name)
                self.assertNotIn(name + "/acceptance.json", entries)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_template_not_shipped(self):
        self.assertNotIn("_template", build_free.skill_names())


if __name__ == "__main__":
    unittest.main()
