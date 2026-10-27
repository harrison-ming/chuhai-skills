#!/usr/bin/env python3
"""竞品变化雷达 / competitor change radar: command-line entry.

    python radar.py snap <store> [--force] [--no-archive] [--archive-date D]
    python radar.py diff <store> [--from ID] [--to ID]
    python radar.py run <store>            snap, then diff (what most users need)
    python radar.py status <store> [--rebuild]
    python radar.py demo                   offline demo with bundled synthetic data
    python radar.py renormalize <snapshot folder>
    python radar.py report-dir <store>     print the newest report folder

Common options: --lang zh|en, --out-root DIR (default: $CHUHAI_OUTPUT_DIR,
else the cloud sandbox outputs, else Documents/出海拆解报告), --quiet.
Only ``snap`` / ``run`` use the network; everything else is offline.
Python 3.8+, standard library only.
"""

import argparse
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import archive  # noqa: E402
import diff as diffmod  # noqa: E402
import draft  # noqa: E402
import normalize  # noqa: E402
import skill_config as cfg  # noqa: E402
import snapshot  # noqa: E402
from _shared import i18n, output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_SRC = os.path.join(HERE, "..", "assets", "demo")
GOOD = ("complete",)
USABLE = ("complete", "partial")

MSG = {
    "zh": {
        "reuse": "今天已经抓过 %s (完整), 直接用, 不再联网. 需要重抓加 --force.",
        "snap": "快照 %s: %s, %d 款商品, 状态 %s",
        "archive_skip": "网页历史存档: %s (不影响其余流程)",
        "no_snap": "还没有任何快照. 先运行: python radar.py snap %s",
        "folder": "报告文件夹: %s",
        "next": '下一步: 把 %s 复制成 %s, 写完 \'需要关注的信号\' 并改顺句子, 然后运行:\n  python %s "%s"\n  python %s "%s"',
        "baseline": "只有一份可用快照, 这次出的是基线报告; 下次建议 %s 再跑.",
        "next_due": "下次建议 %s 再跑.",
        "short": "提示: 和上一份间隔不到一天, 变化少属正常.",
        "status_head": "店铺: %s  文件夹: %s",
        "status_row": "  %-22s %-16s %-11s %4s 款  %s",
        "status_due": "下次建议: %s (节奏 %s 天)",
        "demo": "演示数据已放到 %s (演示文件夹每次重新生成).",
        "bad_store": "错误: 无法解析店铺地址: %s",
        "rebuilt": "已从快照重建 radar.json.",
        "network": "提示: 所有请求都是网络错误. 可能网址不存在, 也可能当前环境不能联网; 不能联网时按 SKILL.md 的模式 B 用网页读取工具取数.",
    },
    "en": {
        "reuse": "Already captured today (%s, complete); reusing it without network. Use --force to capture again.",
        "snap": "Snapshot %s: %s, %d products, status %s",
        "archive_skip": "Web archive: %s (the rest is unaffected)",
        "no_snap": "No snapshot yet. Run first: python radar.py snap %s",
        "folder": "Report folder: %s",
        "next": 'Next: copy %s to %s, write \'Signals to watch\' and smooth the wording, then run:\n  python %s "%s"\n  python %s "%s"',
        "baseline": "Only one usable snapshot, so this is a baseline report; next suggested run %s.",
        "next_due": "Next suggested run: %s.",
        "short": "Note: less than a day since the last snapshot; few changes are normal.",
        "status_head": "Store: %s  folder: %s",
        "status_row": "  %-22s %-16s %-11s %4s products  %s",
        "status_due": "Next suggested run: %s (every %s days)",
        "demo": "Demo data placed in %s (rebuilt on every demo run).",
        "bad_store": "Error: cannot parse the store address: %s",
        "rebuilt": "radar.json rebuilt from the snapshots.",
        "network": "Note: every request failed with a network error. The address may not exist, or this environment has no internet; without internet use mode B in SKILL.md.",
    },
}


def say(args, text):
    if not getattr(args, "quiet", False):
        print(text)


def add_common(p):
    p.add_argument("--lang", choices=i18n.LANGS, default="zh")
    p.add_argument(
        "--out-root", help="delivery root (default: Documents/%s)" % cfg.ROOT_NAME["zh"]
    )
    p.add_argument("--quiet", action="store_true")


def date_add(day, days):
    d = datetime.date(int(day[:4]), int(day[5:7]), int(day[8:10]))
    return (d + datetime.timedelta(days=days)).isoformat()


def pick_pair(snaps, from_id=None, to_id=None):
    """Choose (from, to) among [(id, dir, snap)]; from may be None (baseline)."""
    snaps = [s for s in snaps if s[2]]
    if not snaps:
        return None, None
    by = {s[0]: s for s in snaps}
    to = by.get(to_id) if to_id else snaps[-1]
    if to is None:
        raise SystemExit("unknown snapshot: %s" % to_id)
    if from_id:
        if from_id not in by:
            raise SystemExit("unknown snapshot: %s" % from_id)
        return by[from_id], to
    earlier = [
        s
        for s in snaps
        if s is not to
        and (s[2].get("captured_at_utc") or "") <= (to[2].get("captured_at_utc") or "")
    ]
    for wanted in (GOOD, USABLE, None):
        cands = [s for s in earlier if wanted is None or s[2].get("status") in wanted]
        if cands:
            return cands[-1], to
    return None, to


def unique_dir(parent, name, overwrite=False):
    path = os.path.join(parent, output.sanitize_filename(name))
    if overwrite and os.path.isdir(path):
        shutil.rmtree(path)
    n = 1
    base = path
    while os.path.exists(path):
        n += 1
        path = "%s (%d)" % (base, n)
    os.makedirs(path)
    return path


def ref_of(store_dir, entry):
    sid, path, _snap = entry
    data = normalize.find_raw_parent(path) or path
    return {
        "rel": os.path.relpath(path, store_dir).replace(os.sep, "/"),
        "raw": os.path.basename(data),
    }


def write_report(store_dir, lang, a, b, demo=False, thresholds=None, log=print):
    """Diff (or baseline) into a fresh report folder. Returns (dir, data)."""
    m = MSG[lang]
    cadence = snapshot.load_index(store_dir).get("cadence_days") or cfg.CADENCE_DAYS
    to_day = (b[2].get("captured_at_utc") or b[0])[:10]
    next_due = date_add(to_day, cadence)
    ctx = {"lang": lang, "next_due": next_due, "demo": demo}
    attempt = (
        normalize.load_json(os.path.join(store_dir, archive.ATTEMPT_FILE), {}) or {}
    )
    if attempt.get("result") and attempt.get("result") != "ok":
        ctx["archive_note"] = attempt.get("note") or attempt.get("result")
    if a is not None:
        data = diffmod.compute_diff(
            a[2], b[2], ref_of(store_dir, a), ref_of(store_dir, b), thresholds
        )
        name = cfg.DIFF_DIR[lang] % (a[0], b[0])
        md = draft.diff_md(data, ctx)
        tables = draft.diff_tables(data, lang)
        kind = "diff"
    else:
        data = diffmod.baseline(b[2], ref_of(store_dir, b))
        name = cfg.BASELINE_DIR[lang] % b[0]
        md = draft.baseline_md(data, ctx)
        tables = draft.baseline_tables(data, lang)
        if data["status"] == "failed":
            tables = {}
        kind = "baseline"
    report_dir = unique_dir(store_dir, name, overwrite=demo)
    raw = os.path.join(report_dir, output.raw_dirname(lang))
    os.makedirs(raw)
    files = {
        "diff.json" if kind == "diff" else "baseline.json": data,
        "tables.json": tables,
    }
    files["run.json"] = {
        "skill": cfg.NAME,
        "version": cfg.TOOL_VERSION,
        "lang": lang,
        "kind": kind,
        "domain": data["domain"],
        "shop_name": data.get("shop_name"),
        "status": data["status"],
        "demo": demo,
        "from": a[0] if a else None,
        "to": b[0],
        "next_due": next_due,
        "store_dir": store_dir,
    }
    for fname, obj in files.items():
        with open(os.path.join(raw, fname), "w", encoding="utf-8") as fh:
            fh.write(diffmod.dumps(obj))
    with open(os.path.join(raw, "diff.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    output.write_readme(raw, cfg.README_TEXT[lang], lang)
    snapshot.write_index(
        store_dir,
        lang,
        data["domain"],
        report=os.path.basename(report_dir),
        next_due=next_due,
    )
    log(m["folder"] % report_dir)
    if kind == "baseline":
        log(m["baseline"] % next_due)
    else:
        if "interval_short" in data["warnings"]:
            log(m["short"])
        log(m["next_due"] % next_due)
    here = HERE
    log(
        m["next"]
        % (
            os.path.join(raw, "diff.md"),
            os.path.join(raw, "report.md"),
            os.path.join(here, "check_report.py"),
            report_dir,
            os.path.join(here, "deliver.py"),
            report_dir,
        )
    )
    return report_dir, data


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def resolve(args):
    try:
        return snapshot.find_store_dir(args.store, args.lang, args.out_root)
    except ValueError as e:
        print(MSG[args.lang]["bad_store"] % e, file=sys.stderr)
        raise SystemExit(2)


def cmd_snap(args, store_dir=None, root=None):
    m = MSG[args.lang]
    log = (lambda *_a: None) if args.quiet else print
    if store_dir is None:
        store_dir, root = resolve(args)
    domain = snapshot.domain_of(args.store)
    os.makedirs(store_dir, exist_ok=True)
    had = [s for s in snapshot.list_snapshots(store_dir, args.lang) if s[2]]
    if not had and not args.no_import:
        snapshot.import_teardown(root, store_dir, domain, args.lang, log=log)
    sid, snap, reused = snapshot.capture_live(
        args.store,
        store_dir,
        args.lang,
        max_pages=args.max_pages,
        force=args.force,
        log=log,
    )
    if reused:
        say(args, m["reuse"] % sid)
    say(args, m["snap"] % (sid, snap["source"], len(snap["products"]), snap["status"]))
    if snap.get("fetch_error") == "network":
        say(args, m["network"])
    today = sid[:10]
    older = [
        s
        for s in snapshot.list_snapshots(store_dir, args.lang)
        if s[2] and s[0][:10] < today
    ]
    if (
        not older
        and not args.no_archive
        and snap["status"] not in ("not_shopify", "blocked")
    ):
        _aid, info = archive.capture_archive(
            args.store, store_dir, args.lang, args.archive_date, log=log
        )
        if info.get("result") != "ok":
            say(args, m["archive_skip"] % (info.get("note") or info.get("result")))
    snapshot.pack_old_raw(store_dir, args.lang)
    snapshot.write_index(store_dir, args.lang, domain)
    return 0


def cmd_diff(args, store_dir=None):
    if store_dir is None:
        store_dir, _root = resolve(args)
    snaps = snapshot.list_snapshots(store_dir, args.lang)
    a, b = pick_pair(snaps, args.from_id, args.to_id)
    if b is None:
        print(MSG[args.lang]["no_snap"] % args.store, file=sys.stderr)
        return 1
    thresholds = None
    if getattr(args, "thresholds", None):
        with open(args.thresholds, encoding="utf-8") as fh:
            thresholds = json.load(fh)
        # radar-thresholds.json (Pro) nests diff settings under "diff"
        if isinstance(thresholds, dict) and isinstance(thresholds.get("diff"), dict):
            thresholds = thresholds["diff"]
        thresholds = {
            k: v for k, v in thresholds.items() if k in diffmod.DEFAULT_THRESHOLDS
        }
    write_report(
        store_dir,
        args.lang,
        a,
        b,
        thresholds=thresholds,
        log=(lambda *_x: None) if args.quiet else print,
    )
    return 0


def cmd_run(args):
    store_dir, root = resolve(args)
    rc = cmd_snap(args, store_dir, root)
    if rc:
        return rc
    return cmd_diff(args, store_dir)


def cmd_status(args):
    m = MSG[args.lang]
    store_dir, _root = resolve(args)
    if args.rebuild or not os.path.isfile(os.path.join(store_dir, snapshot.INDEX_FILE)):
        if os.path.isdir(store_dir):
            snapshot.write_index(store_dir, args.lang, snapshot.domain_of(args.store))
            if args.rebuild:
                print(m["rebuilt"])
    idx = snapshot.load_index(store_dir)
    print(m["status_head"] % (snapshot.domain_of(args.store), store_dir))
    if not idx.get("snapshots"):
        print(m["no_snap"] % args.store)
        return 0
    for s in idx["snapshots"]:
        print(
            m["status_row"]
            % (
                s["id"],
                s["source"],
                s["status"],
                s["products_count"],
                s["captured_at_utc"],
            )
        )
    print(m["status_due"] % (idx.get("next_due") or "-", idx.get("cadence_days")))
    return 0


def cmd_demo(args):
    m = MSG[args.lang]
    root = output.resolve_output_root(args.out_root, cfg.ROOT_NAME[args.lang])
    store_dir = os.path.join(snapshot.radar_root(root, args.lang), cfg.DEMO_DOMAIN)
    if os.path.isdir(store_dir):
        shutil.rmtree(store_dir)  # the only folder this tool ever overwrites
    base = snapshot.snap_root(store_dir, args.lang)
    shutil.copytree(os.path.abspath(DEMO_SRC), base)
    for name in sorted(os.listdir(base)):
        normalize.write_snapshot(os.path.join(base, name))
    say(args, m["demo"] % store_dir)
    snapshot.write_index(store_dir, args.lang, cfg.DEMO_DOMAIN)
    snaps = snapshot.list_snapshots(store_dir, args.lang)
    a, b = pick_pair(snaps)
    write_report(
        store_dir,
        args.lang,
        a,
        b,
        demo=True,
        log=(lambda *_x: None) if args.quiet else print,
    )
    return 0


def cmd_renormalize(args):
    snap = normalize.write_snapshot(os.path.abspath(args.snapshot_dir))
    print(
        "%s: %d products, status %s"
        % (snap["snapshot_id"], len(snap["products"]), snap["status"])
    )
    return 0


def cmd_report_dir(args):
    store_dir, _root = resolve(args)
    reports = snapshot.load_index(store_dir).get("reports") or []
    if not reports:
        return 1
    print(os.path.join(store_dir, reports[-1]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description="Competitor change radar for Shopify stores."
    )
    sub = ap.add_subparsers(dest="cmd")
    for name in ("snap", "run"):
        p = sub.add_parser(name)
        p.add_argument("store")
        add_common(p)
        p.add_argument(
            "--force", action="store_true", help="capture again even if done today"
        )
        p.add_argument(
            "--no-archive", action="store_true", help="skip the web-archive baseline"
        )
        p.add_argument(
            "--archive-date", help="target date of the archive baseline, YYYY-MM-DD"
        )
        p.add_argument(
            "--no-import",
            action="store_true",
            help="do not import earlier teardown runs",
        )
        p.add_argument("--max-pages", type=int, default=100)
        if name == "run":
            p.add_argument("--from", dest="from_id")
            p.add_argument("--to", dest="to_id")
            p.add_argument("--thresholds", help=argparse.SUPPRESS)
    p = sub.add_parser("diff")
    p.add_argument("store")
    add_common(p)
    p.add_argument("--from", dest="from_id")
    p.add_argument("--to", dest="to_id")
    p.add_argument("--thresholds", help=argparse.SUPPRESS)
    p = sub.add_parser("status")
    p.add_argument("store")
    add_common(p)
    p.add_argument("--rebuild", action="store_true")
    p = sub.add_parser("demo")
    add_common(p)
    p = sub.add_parser("renormalize")
    p.add_argument("snapshot_dir")
    p = sub.add_parser("report-dir")
    p.add_argument("store")
    add_common(p)
    return ap


def main(argv=None):
    output.safe_console()
    ap = build_parser()
    args = ap.parse_args(argv)
    commands = {
        "snap": cmd_snap,
        "diff": cmd_diff,
        "run": cmd_run,
        "status": cmd_status,
        "demo": cmd_demo,
        "renormalize": cmd_renormalize,
        "report-dir": cmd_report_dir,
    }
    if args.cmd not in commands:
        ap.print_help()
        return 2
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
