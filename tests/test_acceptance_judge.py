"""Tests for tools/acceptance/judge.py with synthetic transcripts and deliverables."""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "acceptance"))
SKILL_DIR = os.path.join(ROOT, "skills", "shopify-store-teardown")

import judge  # noqa: E402

SKILL = "shopify-store-teardown"

GOOD_REPORT = """# allbirds 对标拆解

## 一页看懂

- 这家店是真的 [已核实].
- 定价一款一价 [推测] 依据: 同款各颜色价格一致.

## 附录: 证据

- 采集状态: L1, 完成状态 complete, products.json 正常.

---

### 本报告没有覆盖的

- 需要浏览器才能看到的.
"""

CASE = {
    "id": "normal",
    "prompt": "帮我拆解一下 allbirds.com",
    "expect": {
        "deliverables": ["*.pdf|*.html", "*.csv"],
        "status_in": ["complete", "partial"],
        "must_not_contain": ["products.json", "myshopify", "L1"],
        "must_contain_any": ["[已核实]"],
        "fixed_ending": True,
    },
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def jsonl(path, events):
    write(path, "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acc-judge-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_deliverables(self, report=GOOD_REPORT, pdf=True, csv=True):
        root = os.path.join(self.tmp, "deliverables")
        run = os.path.join(root, "allbirds.com 对标拆解 2026-10-27")
        write(os.path.join(run, "原始数据", "report.md"), report)
        write(os.path.join(run, "原始数据", "report.html"), "<p>raw</p>")
        write(os.path.join(run, "原始数据", "page.csv"), "raw")
        if pdf:
            write(os.path.join(run, "Allbirds 对标拆解报告.pdf"), "%PDF-1.4")
        if csv:
            write(os.path.join(run, "商品清单.csv"), "a,b\n")
        return root


class TranscriptTests(Base):
    def test_claude_skill_invocation_and_denials(self):
        path = os.path.join(self.tmp, "t.jsonl")
        jsonl(
            path,
            [
                {"type": "system", "subtype": "init", "model": "m1"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Skill",
                                "input": {"skill": SKILL},
                            },
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {
                                    "command": "python3 .claude/skills/%s/scripts/collect.py x"
                                    % SKILL
                                },
                            },
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "done"}]},
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "final words",
                    "total_cost_usd": 1.5,
                    "permission_denials": [
                        {"tool_name": "Bash", "tool_input": {"command": "open ."}}
                    ],
                },
            ],
        )
        p = judge.parse_claude(judge.read_jsonl(path), SKILL)
        self.assertTrue(p["triggered"])
        self.assertEqual(p["model"], "m1")
        self.assertEqual(p["final_message"], "final words")
        self.assertEqual(p["permission_denials"], ["Bash: open ."])
        self.assertIn("ran skill script", p["trigger_evidence"])

    def test_claude_read_of_skill_md_counts_plugin_prefix_too(self):
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "x:" + SKILL},
                        }
                    ]
                },
            }
        ]
        self.assertTrue(judge.parse_claude(events, SKILL)["triggered"])
        other = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "other"},
                        }
                    ]
                },
            }
        ]
        self.assertFalse(judge.parse_claude(other, SKILL)["triggered"])

    def test_codex_reads_skill_and_sandbox_denial(self):
        path = os.path.join(self.tmp, "c.jsonl")
        write(
            path,
            "Reading additional input from stdin...\n"
            + "\n".join(
                json.dumps(e)
                for e in [
                    {"type": "thread.started"},
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "/bin/zsh -lc 'cat .agents/skills/%s/SKILL.md'"
                            % SKILL,
                            "exit_code": 0,
                        },
                    },
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "touch ~/x",
                            "exit_code": 1,
                            "aggregated_output": "touch: Operation not permitted",
                        },
                    },
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "bye"},
                    },
                    {"type": "turn.completed"},
                ]
            ),
        )
        p = judge.parse_codex(judge.read_jsonl(path), SKILL)
        self.assertTrue(p["triggered"])
        self.assertFalse(p["is_error"])
        self.assertEqual(p["final_message"], "bye")
        self.assertEqual(len(p["permission_denials"]), 1)

    def test_codex_windows_doubled_backslash_path_triggers(self):
        cmd = (
            '"C:\\\\WINDOWS\\\\powershell.exe" -Command "Get-Content -Raw '
            "'C:\\\\Users\\\\ming\\\\w\\\\.agents\\\\skills\\\\%s\\\\SKILL.md'\""
            % SKILL
        )
        p = judge.parse_codex(
            [
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": cmd},
                },
                {"type": "turn.completed"},
            ],
            SKILL,
        )
        self.assertTrue(p["triggered"])

    def test_codex_without_turn_completed_is_error(self):
        p = judge.parse_codex(
            [{"type": "turn.failed", "error": {"message": "boom"}}], SKILL
        )
        self.assertTrue(p["is_error"])
        self.assertIn("boom", p["errors"][0])
        self.assertFalse(p["triggered"])


class HelperTests(Base):
    def test_deliverables_ignore_raw_dirs(self):
        root = self.make_deliverables(pdf=False, csv=False)
        missing, matched = judge.match_deliverables(root, ["*.pdf|*.html", "*.csv"])
        self.assertEqual(missing, ["*.pdf|*.html", "*.csv"])
        self.assertEqual(matched["*.csv"], [])

    def test_deliverables_alternatives(self):
        root = self.make_deliverables(pdf=False)
        run = os.listdir(root)[0]
        write(os.path.join(root, run, "x 对标拆解报告.html"), "<h2>一页看懂</h2>")
        missing, _ = judge.match_deliverables(root, ["*.pdf|*.html", "*.csv"])
        self.assertEqual(missing, [])

    def test_split_body_and_status(self):
        body, appendix = judge.split_body(GOOD_REPORT)
        self.assertNotIn("products.json", body)
        self.assertIn("products.json", appendix)
        self.assertEqual(judge.extract_status(GOOD_REPORT), "complete")
        self.assertEqual(judge.extract_status("状态: `failed`"), "failed")
        self.assertIsNone(judge.extract_status("nothing here"))

    def test_html_report_fallback(self):
        root = os.path.join(self.tmp, "d")
        write(
            os.path.join(root, "r", "报告.html"),
            "<h2>一页看懂</h2><p>真的 [已核实]</p><h2>附录</h2><p>myshopify</p>",
        )
        path, text = judge.find_report(root)
        self.assertTrue(path.endswith(".html"))
        body, _ = judge.split_body(text)
        self.assertIn("[已核实]", body)
        self.assertNotIn("myshopify", body)

    def test_fixed_ending_marker_from_check_report(self):
        self.assertEqual(judge.fixed_ending_marker(SKILL_DIR, {}), "本报告没有覆盖的")
        self.assertEqual(
            judge.fixed_ending_marker(SKILL_DIR, {"fixed_ending_text": "x"}), "x"
        )


class VerdictTests(Base):
    def parsed(self, triggered=True, final=""):
        return {
            "triggered": triggered,
            "final_message": final,
            "permission_denials": [],
        }

    def test_good_run_passes(self):
        root = self.make_deliverables()
        v = judge.judge_agent_run(CASE, self.parsed(), root, SKILL_DIR, {})
        self.assertTrue(v["passed"], v["reasons"])
        self.assertEqual(v["status"], "complete")
        self.assertEqual(v["report_source"], "deliverable")
        self.assertEqual(v["check_report"]["exit_code"], 0)

    def test_tech_word_before_appendix_and_missing_ending_fail(self):
        bad = GOOD_REPORT.replace("这家店是真的", "products.json 显示真的").replace(
            "本报告没有覆盖的", "其他"
        )
        root = self.make_deliverables(report=bad)
        v = judge.judge_agent_run(CASE, self.parsed(), root, SKILL_DIR, {})
        self.assertFalse(v["passed"])
        joined = " | ".join(v["reasons"])
        self.assertIn("technical words before appendix: products.json", joined)
        self.assertIn("fixed ending missing", joined)
        self.assertIn("check_report.py exit 1", joined)

    def test_untriggered_no_files_uses_final_message(self):
        root = os.path.join(self.tmp, "empty")
        os.makedirs(root)
        v = judge.judge_agent_run(
            CASE, self.parsed(False, "请问你想了解什么?"), root, SKILL_DIR, {}
        )
        self.assertFalse(v["passed"])
        self.assertEqual(v["report_source"], "final_message")
        joined = " | ".join(v["reasons"])
        self.assertIn("skill not triggered", joined)
        self.assertIn("missing deliverable *.csv", joined)
        self.assertIn("status None", joined)

    def test_status_by_runner_accepts_graceful_failure(self):
        failed = GOOD_REPORT.replace("完成状态 complete", "完成状态 failed")
        root = self.make_deliverables(report=failed)
        case = json.loads(json.dumps(CASE))
        case["expect"]["status_by_runner"] = {"codex-nonet": ["failed"]}
        v = judge.judge_agent_run(case, self.parsed(), root, SKILL_DIR, {})
        self.assertIn("status failed not in", " | ".join(v["reasons"]))
        v = judge.judge_agent_run(
            case, self.parsed(), root, SKILL_DIR, {}, runner="codex-nonet"
        )
        self.assertTrue(v["passed"], v["reasons"])


class HarnessOptionTests(unittest.TestCase):
    def test_fixed_ending_per_language(self):
        self.assertEqual(
            judge.fixed_ending_marker(SKILL_DIR, {}, "en"),
            "What this report does not cover",
        )
        self.assertEqual(
            judge.fixed_ending_marker(SKILL_DIR, {}, "zh"), "本报告没有覆盖的"
        )
        acc = {"fixed_ending_text": {"zh": "甲", "en": "B"}}
        self.assertEqual(judge.fixed_ending_marker(SKILL_DIR, acc, "en"), "B")
        self.assertEqual(judge.fixed_ending_marker(SKILL_DIR, acc), "甲")

    def test_effective_expect(self):
        case = {
            "expect": {
                "status_in": ["complete"],
                "must_contain_any": ["[已核实]"],
                "status_by_runner": {"codex-nonet": ["failed", "partial"]},
                "expect_by_runner": {"codex-nonet": {"must_contain_any": []}},
            }
        }
        plain = judge.effective_expect(case, "claude")
        self.assertEqual(plain["status_in"], ["complete"])
        self.assertNotIn("status_by_runner", plain)
        nonet = judge.effective_expect(case, "codex-nonet")
        self.assertEqual(nonet["status_in"], ["failed", "partial"])
        self.assertEqual(nonet["must_contain_any"], [])
        self.assertEqual(case["expect"]["status_in"], ["complete"])  # not mutated

    def test_leak_dirs_cover_known_and_declared_roots(self):
        import run

        names = [os.path.basename(p) for p in run.leak_dirs(SKILL_DIR)]
        for name in ("出海拆解报告", "出海体检报告", "Store checks", "出海测算报告"):
            self.assertIn(name, names)
        tmp = tempfile.mkdtemp(prefix="leak-")
        try:
            os.makedirs(os.path.join(tmp, "scripts"))
            with open(
                os.path.join(tmp, "scripts", "skill_config.py"), "w", encoding="utf-8"
            ) as fh:
                fh.write('ROOT_NAME = {"zh": "出海新报告", "en": "New checks"}\n')
            self.assertEqual(run.skill_root_names(tmp), ["出海新报告", "New checks"])
            names = [os.path.basename(p) for p in run.leak_dirs(tmp)]
            self.assertIn("New checks", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_harness_env_disables_open(self):
        import run

        env = run.base_env("/w", "/shim")
        self.assertEqual(env["CHUHAI_NO_OPEN"], "1")


if __name__ == "__main__":
    unittest.main()
