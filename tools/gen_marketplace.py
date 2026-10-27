#!/usr/bin/env python3
"""Generate .claude-plugin/marketplace.json from every skills/<name>/SKILL.md.

The version lives only in SKILL.md ``metadata.version``. Per plugin the
generator takes name, version, author, homepage and license from SKILL.md;
the short marketplace ``description`` and ``keywords`` are kept from the
existing marketplace.json (new skills get the first sentence of their
SKILL.md description and no keywords, edit them once by hand). The
top-level ``metadata.version`` is the highest skill version. Folders whose
name starts with "_" (the template) are skipped.

    python tools/gen_marketplace.py           write the file
    python tools/gen_marketplace.py --check   exit 1 if the file is stale
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
MARKETPLACE = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DEFAULT_HEAD = {
    "name": "chuhai-skills",
    "owner": {"name": "StellarByte", "url": "https://github.com/harrison-ming"},
    "metadata": {
        "description": (
            "出海电商 Agent Skills: 面向想出海的卖家, 只读公开信息, "
            "输出带证据分级的中文报告."
        )
    },
}


def parse_frontmatter(text):
    """Tiny parser for the frontmatter shape the skills use.

    Top-level ``key: value`` and ``key: >-`` block scalars (folded with
    spaces), plus one nested mapping level (``metadata:``). Quotes around
    plain values are removed.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    data, i = {}, 1
    while i < end:
        line = lines[i]
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in (">-", ">", "|", "|-"):
            buf = []
            while i < end and (lines[i].startswith(" ") or not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            sep = "\n" if value.startswith("|") else " "
            data[key] = sep.join(b for b in buf if b)
        elif value == "":
            sub = {}
            while i < end and lines[i].startswith(" "):
                sm = re.match(r"^\s+([A-Za-z_][\w-]*):\s*(.*)$", lines[i])
                if sm:
                    sub[sm.group(1)] = unquote(sm.group(2).strip())
                i += 1
            data[key] = sub
        else:
            data[key] = unquote(value)
    return data


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def skill_dirs(skills_dir=SKILLS):
    return [
        os.path.join(skills_dir, n)
        for n in sorted(os.listdir(skills_dir))
        if not n.startswith(("_", "."))
        and os.path.isfile(os.path.join(skills_dir, n, "SKILL.md"))
    ]


def read_skill(path):
    with open(os.path.join(path, "SKILL.md"), encoding="utf-8") as fh:
        fm = parse_frontmatter(fh.read())
    meta = fm.get("metadata") or {}
    version = meta.get("version", "")
    if not VERSION_RE.match(version):
        raise ValueError("%s: metadata.version must be X.Y.Z, got %r" % (path, version))
    if fm.get("name") != os.path.basename(path):
        raise ValueError("%s: frontmatter name must equal the folder name" % path)
    return fm, meta


def first_sentence(text):
    m = re.match(r"^(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def version_key(v):
    return tuple(int(x) for x in v.split("."))


def build(existing=None, skills_dir=SKILLS):
    existing = existing or {}
    old_plugins = dict((p.get("name"), p) for p in existing.get("plugins", []))
    head_meta = dict((existing.get("metadata") or DEFAULT_HEAD["metadata"]))
    plugins, versions = [], []
    for path in skill_dirs(skills_dir):
        fm, meta = read_skill(path)
        name = fm["name"]
        old = old_plugins.get(name, {})
        versions.append(meta["version"])
        plugins.append(
            {
                "name": name,
                "description": old.get("description")
                or first_sentence(fm.get("description", "")),
                "version": meta["version"],
                "author": {"name": meta.get("author") or "StellarByte"},
                "homepage": meta.get("homepage", ""),
                "license": fm.get("license", ""),
                "keywords": old.get("keywords", []),
                "source": "./",
                "strict": False,
                "skills": ["./skills/%s" % name],
            }
        )
    if versions:
        head_meta["version"] = max(versions, key=version_key)
    return {
        "name": existing.get("name") or DEFAULT_HEAD["name"],
        "owner": existing.get("owner") or DEFAULT_HEAD["owner"],
        "metadata": head_meta,
        "plugins": plugins,
    }


def render(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate marketplace.json.")
    ap.add_argument("--check", action="store_true", help="exit 1 when stale")
    args = ap.parse_args(argv)
    current = ""
    existing = {}
    if os.path.isfile(MARKETPLACE):
        with open(MARKETPLACE, encoding="utf-8") as fh:
            current = fh.read()
        existing = json.loads(current)
    try:
        text = render(build(existing))
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    if args.check:
        if text != current:
            print("marketplace.json is stale: run python tools/gen_marketplace.py")
            return 1
        print("marketplace.json is up to date")
        return 0
    os.makedirs(os.path.dirname(MARKETPLACE), exist_ok=True)
    with open(MARKETPLACE, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("written %s" % os.path.relpath(MARKETPLACE, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
