#!/usr/bin/env python3
"""One command for the whole profit-model pipeline.

    python profit.py init --product "<商品名>" [--lang zh|en] [--out-root DIR]
        Creates the delivery folder and a blank inputs.csv in its raw-data
        folder; prints both paths. Write the inputs there, then run:

    python profit.py run "<run folder | inputs.csv>" [--lang zh|en (default:
                         English when the inputs use English headers)]
                         [--out-root DIR] [--no-fx-fetch] [--no-open]
                         [--html-only]
        validate -> (ECB rate only when no rate was given) -> compute ->
        report -> self-check -> PDF/HTML + CSV. A CSV outside a run folder
        gets a new run folder (named after the first product).

Exit code 0 when the files were delivered, whatever the result status
(complete / partial / failed is printed and written into the report).
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deliver  # noqa: E402
import pm_calc  # noqa: E402
import pm_fx  # noqa: E402
import pm_inputs  # noqa: E402
import pm_report  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import i18n, output  # noqa: E402

TEMPLATE = {
    "zh": os.path.join(HERE, "..", "templates", "inputs-template.zh.csv"),
    "en": os.path.join(HERE, "..", "templates", "inputs-template.en.csv"),
}
PLACEHOLDER = {"zh": "我的商品", "en": "My product"}
MSG = {
    "zh": {
        "init": "运行目录: %s\n输入文件: %s\n把输入写进这个文件 (一行一个数, 不知道的留空), 然后运行 run.",
        "status": "完成状态: %s",
        "blocked": '没算出来, 需要补的见报告 "请核对" 一节.',
        "fx": "没给汇率, 已取欧洲央行参考汇率 (%s).",
        "fx_fail": "没给汇率, 也没取到欧洲央行参考汇率; 报告会请用户补汇率.",
        "line": "%s: 每单贡献 %s, 广告后 %s, 盈亏平衡 ROAS %s, 底线价 %s, 目标价 %s",
    },
    "en": {
        "init": "Run folder: %s\nInputs file: %s\nWrite the inputs into it (one per row, leave unknowns blank), then use run.",
        "status": "Status: %s",
        "blocked": 'Nothing computed; see "Please check" in the report.',
        "fx": "No rate given; ECB reference rate used (%s).",
        "fx_fail": "No rate given and the ECB rate could not be fetched; the report asks for one.",
        "line": "%s: contribution %s, after ads %s, break-even ROAS %s, floor %s, target %s",
    },
}


def root_for(out_root, lang):
    return output.resolve_output_root(out_root, cfg.ROOT_NAME[lang])


def cmd_init(args):
    lang = args.lang
    root = root_for(args.out_root, lang)
    run = output.create_run_dir(root, args.product, cfg.RUN_LABEL[lang])
    raw = os.path.join(run, output.raw_dirname(lang))
    os.makedirs(raw, exist_ok=True)
    with open(TEMPLATE[lang], encoding="utf-8-sig") as fh:
        text = fh.read().replace(PLACEHOLDER[lang], args.product.replace(",", " "))
    dest = os.path.join(raw, "inputs.csv")
    with open(dest, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)
    print(MSG[lang]["init"] % (run, dest))
    return 0


def first_product(csv_path):
    try:
        rows = pm_inputs.read_rows(csv_path)
    except (OSError, ValueError):
        return None
    for r in rows:
        name = r["product"]
        if name and pm_inputs.F.norm(name) not in [
            pm_inputs.F.norm(t) for t in pm_inputs.F.STORE_TOKENS
        ]:
            return name
    return None


def locate(path, lang, out_root):
    """(run_dir, raw_dir) for a run folder or an inputs CSV path."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        parent = os.path.dirname(path)
        if (
            os.path.basename(parent) in ("原始数据", "data")
            and os.path.basename(path) == "inputs.csv"
        ):
            return os.path.dirname(parent), parent
        product = first_product(path) or os.path.splitext(os.path.basename(path))[0]
        run = output.create_run_dir(
            root_for(out_root, lang), product, cfg.RUN_LABEL[lang]
        )
        raw = os.path.join(run, output.raw_dirname(lang))
        os.makedirs(raw, exist_ok=True)
        shutil.copyfile(path, os.path.join(raw, "inputs.csv"))
        return run, raw
    run, raw = output.locate_run(path, lang)
    if not os.path.isfile(os.path.join(raw, "inputs.csv")):
        alt = [
            d
            for d in ("原始数据", "data")
            if os.path.isfile(os.path.join(run, d, "inputs.csv"))
        ]
        if alt:
            raw = os.path.join(run, alt[0])
    return run, raw


def detect_lang(path):
    """No --lang: English when the inputs use English headers or the run
    folder uses the English data folder, else Chinese."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        if os.path.isfile(
            os.path.join(path, "data", "inputs.csv")
        ) and not os.path.isdir(os.path.join(path, "原始数据")):
            return "en"
        return "zh"
    try:
        text = pm_inputs.read_text(path)
    except OSError:
        return "zh"
    for line in text.splitlines():
        if line.strip():
            return (
                "zh" if "项目" in line else ("en" if "item" in line.lower() else "zh")
            )
    return "zh"


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def cmd_run(args):
    lang = args.lang or detect_lang(args.path)
    msg = MSG[lang]
    run, raw = locate(args.path, lang, args.out_root)
    csv_path = os.path.join(raw, "inputs.csv")
    if not os.path.isfile(csv_path):
        print("error: %s not found" % csv_path, file=sys.stderr)
        return 1
    pm_inputs.ensure_utf8(csv_path)
    shutil.copyfile(
        pm_inputs.pm_fees.TABLE_PATH, os.path.join(raw, "fee_tables.snapshot.json")
    )
    try:
        norm = pm_inputs.normalize(csv_path, product_hint=None)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    fx_missing = any(i["code"] == "fx_missing" for i in norm["issues"])
    if fx_missing and not args.no_fx_fetch:
        data, _err = pm_fx.fetch_rates()
        if data:
            write_json(os.path.join(raw, "fx.json"), data)
            norm = pm_inputs.normalize(csv_path, fx_fetched=data)
            print(msg["fx"] % data["date"])
        else:
            print(msg["fx_fail"])
    write_json(os.path.join(raw, "inputs.normalized.json"), norm)
    result = pm_calc.compute(norm)
    write_json(os.path.join(raw, "result.json"), result)
    pm_report.render(raw, lang)
    flags = ["--lang", lang]
    if args.no_open:
        flags.append("--no-open")
    if args.html_only:
        flags.append("--html-only")
    rc = deliver.main([run] + flags)
    print("")
    print(msg["status"] % result["status"])
    for s in result["skus"]:
        if s["blocked"]:
            continue
        mt, p = s["metrics"], s["price"]
        print(
            msg["line"]
            % (
                s["name"],
                mt["cm0"]["value"],
                mt["cm1"]["value"] if s["has_ads"] else "-",
                mt["roas_be"]["value"],
                p["floor"],
                p["target"],
            )
        )
    if result["status"] == "failed":
        print(msg["blocked"])
    return rc


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="profit-model pipeline")
    sub = ap.add_subparsers(dest="cmd")
    p_init = sub.add_parser("init", help="create a run folder and blank inputs.csv")
    p_init.add_argument("--product", required=True)
    p_init.add_argument("--lang", default="zh", choices=i18n.LANGS)
    p_init.add_argument("--out-root")
    p_run = sub.add_parser("run", help="compute and deliver")
    p_run.add_argument("path")
    p_run.add_argument("--lang", choices=i18n.LANGS, help="default: from the inputs")
    p_run.add_argument("--out-root")
    p_run.add_argument("--no-fx-fetch", action="store_true")
    p_run.add_argument("--no-open", action="store_true")
    p_run.add_argument("--html-only", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "run":
        return cmd_run(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
