#!/usr/bin/env python3
"""Create skills/<name>/ from skills/_template/.

    python tools/new_skill.py payments-readiness \\
        --title-zh 收款上线体检 --title-en "Payments readiness check" \\
        [--root-zh 出海体检报告] [--root-en "Store checks"]

Copies the template (including its synced scripts/_shared/), fills the
placeholders (never inside scripts/_shared/) and refuses to overwrite an
existing folder. Afterwards:
write references/method.md and the scripts, adjust tests and fixtures,
fill acceptance.json, then run ``python tools/gen_marketplace.py`` and
``python tools/run_tests.py``. See CONTRIBUTING.md.
Python 3.8+, standard library only.
"""

import argparse
import datetime
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
TEMPLATE = "_template"
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
TEXT_SUFFIXES = (".md", ".py", ".json", ".html", ".txt", ".csv", ".tsv", ".xml")
REVIEW_DAYS = 90
# _shared/ is a synced copy: never rewritten (sync_shared.py --check)
SKIP_DIRS = ("__pycache__", "_shared")
DEFAULT_ROOT = {"zh": "出海体检报告", "en": "Store checks"}


def fill(text, values):
    for key, value in values.items():
        text = text.replace("__%s__" % key, value)
    return text


def leftovers(folder):
    """[(relative path, placeholder)] still present after filling."""
    out = []
    for base, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if not name.endswith(TEXT_SUFFIXES):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                for m in PLACEHOLDER_RE.finditer(fh.read()):
                    out.append((os.path.relpath(path, folder), m.group(0)))
    return out


def create(name, title_zh, title_en, root_zh=None, root_en=None, skills_dir=SKILLS):
    if not NAME_RE.match(name):
        raise ValueError(
            "skill name must be lower-case words joined by '-', e.g. tracking-audit"
        )
    for title in (title_zh, title_en):
        if not title or ":" in title or "#" in title:
            raise ValueError("titles must be non-empty and contain no ':' or '#'")
    dest = os.path.join(skills_dir, name)
    if os.path.exists(dest):
        raise ValueError("already exists: %s" % dest)
    review_by = datetime.date.today() + datetime.timedelta(days=REVIEW_DAYS)
    values = {
        "SKILL_NAME": name,
        "TITLE_ZH": title_zh,
        "TITLE_EN": title_en,
        "ROOT_ZH": root_zh or DEFAULT_ROOT["zh"],
        "ROOT_EN": root_en or DEFAULT_ROOT["en"],
        "REVIEW_BY": review_by.isoformat(),
    }
    shutil.copytree(
        os.path.join(SKILLS, TEMPLATE),
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    for base, dirs, names in os.walk(dest):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in names:
            if not fname.endswith(TEXT_SUFFIXES):
                continue
            path = os.path.join(base, fname)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            new = fill(text, values)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
    left = leftovers(dest)
    if left:
        raise ValueError("unfilled placeholders: %s" % left[:5])
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create a skill from skills/_template.")
    ap.add_argument("name", help="folder and frontmatter name, e.g. tracking-audit")
    ap.add_argument("--title-zh", required=True)
    ap.add_argument("--title-en", required=True)
    ap.add_argument(
        "--root-zh", help="delivery root folder (zh), default %s" % DEFAULT_ROOT["zh"]
    )
    ap.add_argument(
        "--root-en", help="delivery root folder (en), default %s" % DEFAULT_ROOT["en"]
    )
    ap.add_argument("--skills-dir", default=SKILLS, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        dest = create(
            args.name,
            args.title_zh,
            args.title_en,
            args.root_zh,
            args.root_en,
            args.skills_dir,
        )
    except (ValueError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    print("created %s" % os.path.relpath(dest, ROOT))
    print("next: references/method.md -> scripts -> tests -> acceptance.json,")
    print("      then python tools/gen_marketplace.py && python tools/run_tests.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
