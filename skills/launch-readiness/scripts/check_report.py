#!/usr/bin/env python3
"""Step 4: self-check report.md before deliver.py (same rules deliver runs).

CLI:
    python check_report.py "<run folder>" [--lang zh|en] [--json]

The language comes from --lang, else from summary.json, else zh. Exit code:
0 = no hard errors, 1 = hard errors, 2 = report.md not found. Rules: see
_shared/checks.py; skill-specific words and rules: skill_config.py.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_config as cfg  # noqa: E402
from _shared import checks, i18n, output  # noqa: E402


def detect_lang(path, lang=None):
    """--lang wins; else summary.json "lang" in either language layout."""
    if lang:
        return i18n.norm_lang(lang)
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    for code in i18n.LANGS:
        for cand in (
            os.path.join(path, output.raw_dirname(code), "summary.json"),
            os.path.join(path, "summary.json"),
        ):
            try:
                with open(cand, encoding="utf-8") as fh:
                    return i18n.norm_lang(json.load(fh).get("lang"))
            except (OSError, ValueError, AttributeError):
                continue
    return i18n.DEFAULT_LANG


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Self-check report.md before delivery.")
    ap.add_argument("path", help="run folder, its raw-data folder, or report.md")
    ap.add_argument("--lang", choices=i18n.LANGS)
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    lang = detect_lang(args.path, args.lang)
    forward = [args.path] + (["--json"] if args.json else [])
    return checks.main(forward, cfg.profile(lang))


if __name__ == "__main__":
    sys.exit(main())
