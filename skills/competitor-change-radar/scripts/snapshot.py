"""Snapshot storage for one store: collect, import, index, retention.

Layout (names per language, see skill_config)::

    <root>/雷达/<domain>/
        radar.json                     index (radar/index/1), rebuilt from snapshots
        快照/<id>/snapshot.json         normalized snapshot (normalize.py)
        快照/<id>/snapshot-meta.json    source, capture time, archive stamps
        快照/<id>/原始数据/raw/          teardown file names
        <report folders>

Snapshot ids are UTC dates (``2026-10-27``), ``_2``, ``_3`` for extra runs on
the same day, ``-archive`` for web-archive baselines and ``-teardown`` for
imported shopify-store-teardown runs. New snapshots are written into
``.<id>.partial/`` first and renamed when complete; stale ``.partial``
folders made by this tool are removed on the next run. Nothing else is ever
deleted: raw/ of older snapshots is packed into raw.zip (checked) and only
then the plain folder is removed.
Python 3.8+, standard library only.
"""

import datetime
import json
import os
import re
import shutil
import sys
import zipfile
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import normalize  # noqa: E402
import skill_config as cfg  # noqa: E402
import summarize  # noqa: E402
from _shared import fetch, output  # noqa: E402

INDEX_SCHEMA = "radar/index/1"
INDEX_FILE = "radar.json"
KEEP_RAW_RECENT = 4
SITEMAP_CHILD_CAP = 20
ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:_(\d+))?(-archive|-teardown)?$")


def utc_today(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.date().isoformat()


def domain_of(store):
    _scheme, host = fetch.normalize_input(store)
    return fetch.strip_www(host)


def radar_root(root, lang):
    return os.path.join(root, cfg.RADAR_DIR[lang])


def find_store_dir(store, lang, cli_root=None, environ=None):
    """(store_dir, root). Falls back to the scheduled-runs root when the
    normal root has no history for this store but that one has."""
    environ = os.environ if environ is None else environ
    domain = domain_of(store)
    root = output.resolve_output_root(cli_root, cfg.ROOT_NAME[lang], environ=environ)
    store_dir = os.path.join(radar_root(root, lang), domain)
    if cli_root or (environ.get(output.ENV_OUTPUT) or "").strip():
        return store_dir, root
    if not os.path.isdir(os.path.join(store_dir, cfg.SNAP_DIR[lang])):
        alt_root = os.path.expanduser(cfg.SCHEDULED_ROOT)
        alt = os.path.join(radar_root(alt_root, lang), domain)
        if os.path.isdir(os.path.join(alt, cfg.SNAP_DIR[lang])):
            return alt, alt_root
    return store_dir, root


def snap_root(store_dir, lang):
    return os.path.join(store_dir, cfg.SNAP_DIR[lang])


def sort_key(sid):
    m = ID_RE.match(sid)
    if not m:
        return ("9999", 0, sid)
    return (m.group(1), int(m.group(2) or 1), sid)


def list_snapshots(store_dir, lang):
    """[(id, dir, snapshot dict or None)] oldest first; .partial skipped."""
    base = snap_root(store_dir, lang)
    out = []
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if name.startswith(".") or not os.path.isdir(path):
            continue
        try:
            snap = normalize.load_snapshot(path)
        except ValueError:
            snap = None
        out.append((name, path, snap))
    out.sort(key=lambda x: (x[2] or {}).get("captured_at_utc") or sort_key(x[0])[0])
    return out


def clean_partials(store_dir, lang):
    base = snap_root(store_dir, lang)
    removed = []
    if os.path.isdir(base):
        for name in os.listdir(base):
            if name.startswith(".") and name.endswith(".partial"):
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
                removed.append(name)
    return removed


def next_id(store_dir, lang, day, suffix=""):
    base = snap_root(store_dir, lang)
    n = 1
    while True:
        sid = day + ("" if n == 1 else "_%d" % n) + suffix
        if not os.path.exists(os.path.join(base, sid)):
            return sid
        n += 1


def write_meta(snap_dir, meta):
    with open(os.path.join(snap_dir, normalize.META_FILE), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def finish(tmp_dir, final_dir, source):
    """Summarize + normalize inside the temp folder, then rename atomically."""
    data_dir = normalize.find_raw_parent(tmp_dir)
    if data_dir is not None and not os.path.isfile(
        os.path.join(data_dir, "summary.json")
    ):
        summarize.summarize_run(data_dir)
    normalize.write_snapshot(tmp_dir, source)
    os.replace(tmp_dir, final_dir)
    return normalize.load_snapshot(final_dir)


# ---------------------------------------------------------------------------
# live capture
# ---------------------------------------------------------------------------


class RadarCollector(collect.Collector):
    """Slim teardown run: homepage, meta, all product pages, collections,
    five policy pages; sitemap only when product data is incomplete; site-age
    lookups only on the first snapshot of a store."""

    def __init__(self, target, out_dir, fetcher, max_pages=100, offsite=False):
        collect.Collector.__init__(
            self, target, out_dir, "radar", max_pages, not offsite, fetcher
        )

    def run(self):
        os.makedirs(self.raw, exist_ok=True)
        self.fetch("homepage", self.base + "/", "homepage.html", "html")
        final = self.requests[-1].get("final_url") or ""
        parts = urlsplit(final)
        if parts.netloc and parts.scheme in ("http", "https"):
            new_base = "%s://%s" % (parts.scheme, parts.netloc)
            if new_base != self.base:
                self.base = new_base
        self.fetch("meta_json", self.base + "/meta.json", "meta.json", "json")
        self.collect_products()
        products_ok = bool(self.products) and self._pagination_done()
        if not products_ok:
            self.collect_sitemap()
        if self.requests and any(
            r["key"] in ("homepage", "products_json") and r["result"] == "ok"
            for r in self.requests
        ):
            self.collect_collections()
            for name in summarize.POLICY_NAMES:
                self.fetch(
                    "policy_" + name.replace("-", "_"),
                    "%s/policies/%s" % (self.base, name),
                    "policy-%s.html" % name,
                    "html",
                )
            if not self.skip_offsite:
                self.collect_offsite()
        elif all(r.get("http_status") is None for r in self.requests):
            # Nothing answered: two off-site lookups tell "this machine is
            # offline" (network_unavailable) from "this address is dead".
            self.collect_offsite()
        self.write_status()

    def _pagination_done(self):
        prod = [r for r in self.requests if r["key"] == "products_json"]
        return bool(prod) and prod[-1]["result"] == "ok"

    def collect_sitemap(self):
        saved = collect.SITEMAP_CHILD_CAP
        collect.SITEMAP_CHILD_CAP = SITEMAP_CHILD_CAP
        try:
            collect.Collector.collect_sitemap(self)
        finally:
            collect.SITEMAP_CHILD_CAP = saved


def capture_live(
    store,
    store_dir,
    lang,
    fetcher=None,
    max_pages=100,
    force=False,
    now=None,
    log=print,
):
    """Take today's snapshot (or reuse today's complete one). Returns (id, snap, reused)."""
    base = snap_root(store_dir, lang)
    os.makedirs(base, exist_ok=True)
    clean_partials(store_dir, lang)
    day = utc_today(now)
    existing = list_snapshots(store_dir, lang)
    today = [
        s
        for s in existing
        if sort_key(s[0])[0] == day
        and ID_RE.match(s[0])
        and not ID_RE.match(s[0]).group(3)
    ]
    if not force:
        for sid, _path, snap in reversed(today):
            if snap and snap.get("status") == "complete":
                return sid, snap, True
    first = not any(s[2] and s[2].get("source") == "live" for s in existing)
    sid = next_id(store_dir, lang, day)
    tmp = os.path.join(base, ".%s.partial" % sid)
    data_dir = os.path.join(tmp, output.raw_dirname(lang))
    os.makedirs(data_dir)
    fetcher = fetcher or collect.Fetcher(timeout=20.0, delay=0.5, lang=lang)
    col = RadarCollector(store, data_dir, fetcher, max_pages=max_pages, offsite=first)
    log(("开始抓取 %s" if lang == "zh" else "Capturing %s") % col.base)
    col.run()
    write_meta(
        tmp,
        {
            "domain": col.domain,
            "snapshot_id": sid,
            "captured_at_utc": col.started,
            "source": "live",
            "tool_version": cfg.TOOL_VERSION,
        },
    )
    snap = finish(tmp, os.path.join(base, sid), "live")
    return sid, snap, False


# ---------------------------------------------------------------------------
# shopify-store-teardown import
# ---------------------------------------------------------------------------

TEARDOWN_RE = re.compile(
    r"^(?P<domain>\S+) 对标拆解 (?P<date>\d{4}-\d{2}-\d{2})(?: \(\d+\))?$"
)


def find_teardown_runs(root, domain):
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        m = TEARDOWN_RE.match(name)
        if not m or m.group("domain") != domain:
            continue
        data = os.path.join(root, name, "原始数据")
        if os.path.isdir(os.path.join(data, "raw")):
            out.append((m.group("date"), os.path.join(root, name), data))
    return out


def import_teardown(root, store_dir, domain, lang, log=print):
    """Import the latest earlier teardown run as a baseline. Returns id or None."""
    runs = find_teardown_runs(root, domain)
    if not runs:
        return None
    done = {(s[2] or {}).get("imported_from") for s in list_snapshots(store_dir, lang)}
    date, run_dir, data = runs[-1]
    if date >= utc_today() or run_dir in done:
        return None
    base = snap_root(store_dir, lang)
    os.makedirs(base, exist_ok=True)
    sid = next_id(store_dir, lang, date, "-teardown")
    tmp = os.path.join(base, ".%s.partial" % sid)
    dest = os.path.join(tmp, output.raw_dirname(lang))
    os.makedirs(dest)
    shutil.copytree(os.path.join(data, "raw"), os.path.join(dest, "raw"))
    if os.path.isfile(os.path.join(data, "summary.json")):
        shutil.copy2(os.path.join(data, "summary.json"), dest)
    summary = normalize.load_json(os.path.join(dest, "summary.json"), {}) or {}
    write_meta(
        tmp,
        {
            "domain": domain,
            "snapshot_id": sid,
            "captured_at_utc": summary.get("captured_at_utc") or date + "T00:00:00Z",
            "source": "teardown_import",
            "imported_from": run_dir,
        },
    )
    finish(tmp, os.path.join(base, sid), "teardown_import")
    log(
        (
            "已导入之前的对标拆解作为基线: %s"
            if lang == "zh"
            else "Imported earlier teardown as baseline: %s"
        )
        % sid
    )
    return sid


# ---------------------------------------------------------------------------
# index and retention
# ---------------------------------------------------------------------------


def load_index(store_dir):
    return normalize.load_json(os.path.join(store_dir, INDEX_FILE), {}) or {}


def write_index(store_dir, lang, domain, report=None, next_due=None, cadence=None):
    """Rebuild radar.json from the snapshot folders, keep reports and cadence."""
    old = load_index(store_dir)
    snaps = []
    for sid, _path, snap in list_snapshots(store_dir, lang):
        if not snap:
            continue
        snaps.append(
            {
                "id": sid,
                "captured_at_utc": snap.get("captured_at_utc"),
                "source": snap.get("source"),
                "status": snap.get("status"),
                "products_count": len(snap.get("products") or {}),
                "pagination_complete": (snap.get("coverage") or {}).get(
                    "pagination_complete"
                ),
            }
        )
    reports = list(old.get("reports") or [])
    if report and report not in reports:
        reports.append(report)
    index = {
        "schema": INDEX_SCHEMA,
        "domain": domain,
        "lang": lang,
        "cadence_days": cadence or old.get("cadence_days") or cfg.CADENCE_DAYS,
        "next_due": next_due or old.get("next_due"),
        "snapshots": snaps,
        "reports": reports,
    }
    os.makedirs(store_dir, exist_ok=True)
    with open(os.path.join(store_dir, INDEX_FILE), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return index


def pack_old_raw(store_dir, lang, keep=KEEP_RAW_RECENT):
    """Zip raw/ of snapshots other than the first and the last ``keep``."""
    snaps = [s for s in list_snapshots(store_dir, lang) if s[2]]
    packed = []
    for sid, path, _snap in snaps[1:-keep] if len(snaps) > keep + 1 else []:
        data = normalize.find_raw_parent(path)
        if data is None:
            continue
        raw = os.path.join(data, "raw")
        target = os.path.join(data, "raw.zip")
        if not os.path.isdir(raw) or os.path.exists(target):
            continue
        files = []
        for base, _dirs, names in os.walk(raw):
            for n in names:
                files.append(os.path.join(base, n))
        tmp = target + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(files):
                zf.write(f, os.path.relpath(f, data))
        with zipfile.ZipFile(tmp) as zf:
            ok = zf.testzip() is None and len(zf.namelist()) == len(files)
        if not ok:
            os.remove(tmp)
            continue
        os.replace(tmp, target)
        shutil.rmtree(raw)
        packed.append(sid)
    return packed
