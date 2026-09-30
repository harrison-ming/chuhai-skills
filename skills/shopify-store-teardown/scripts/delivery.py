"""Shared delivery helpers for collect.py and deliver.py.

Resolves where the user-facing ``出海拆解报告`` folder lives, names the
per-run folder, cleans file names, locates a Chromium-family browser for
PDF printing and opens a folder in the local file manager.

Python 3.8+, standard library only.
"""

import datetime
import os
import shutil
import subprocess
import sys

ROOT_NAME = "出海拆解报告"
RAW_DIRNAME = "原始数据"
README_NAME = "说明.txt"
REPORT_SUFFIX = " 对标拆解报告"
CSV_NAME = "商品清单.csv"
ENV_OUTPUT = "CHUHAI_OUTPUT_DIR"
SANDBOX_OUTPUTS = "/mnt/user-data/outputs"
ILLEGAL_CHARS = '\\/:*?"<>|'

README_TEXT = (
    "这是生成报告用的原始数据, 普通用户可以不看.\n"
    '报告正文在上一级文件夹里的 PDF ("...对标拆解报告.pdf", '
    "找不到浏览器时是 .html) 里.\n"
    '商品清单在上一级文件夹的 "商品清单.csv", 可以用 Excel 或 WPS 直接打开.\n'
    "这里的 raw/, summary.json, products.tsv, evidence.jsonl, report.md, "
    "report.html 供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
)
COMPARE_MARKER = "compare.json"
README_TEXT_COMPARE = (
    "这是生成多店对比报告用的数据, 普通用户可以不看.\n"
    "对比报告在上一级文件夹里的 PDF (找不到浏览器时是 .html) 里, "
    "表格在上一级文件夹的 .csv 文件里, 可以用 Excel 或 WPS 直接打开.\n"
    "这里的 compare.md, compare.json, report.md, report.html "
    "供 AI 助手复核和重新生成报告, 请不要改名或删除.\n"
)


def _is_writable_dir(path):
    return bool(path) and os.path.isdir(path) and os.access(path, os.W_OK)


def documents_dir(platform=None, environ=None, home=None):
    """Return the user's Documents folder, falling back to the home folder."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = home or os.path.expanduser("~")
    candidates = []
    if platform.startswith("win"):
        profile = environ.get("USERPROFILE") or home
        candidates.append(os.path.join(profile, "Documents"))
        onedrive = environ.get("OneDrive") or environ.get("ONEDRIVE")
        if onedrive:
            candidates.append(os.path.join(onedrive, "Documents"))
            candidates.append(os.path.join(onedrive, "文档"))
        home = profile
    else:
        candidates.append(os.path.join(home, "Documents"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return home


def resolve_output_root(
    cli_root=None, environ=None, platform=None, home=None, sandbox=SANDBOX_OUTPUTS
):
    """Resolve the delivery root folder (not created here).

    Order: ``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox outputs >
    ``<Documents>/出海拆解报告``.
    """
    environ = os.environ if environ is None else environ
    if cli_root:
        return os.path.abspath(os.path.expanduser(cli_root))
    env_root = (environ.get(ENV_OUTPUT) or "").strip()
    if env_root:
        return os.path.abspath(os.path.expanduser(env_root))
    if sandbox and _is_writable_dir(sandbox):
        return os.path.join(sandbox, ROOT_NAME)
    return os.path.join(documents_dir(platform, environ, home), ROOT_NAME)


def run_dir_name(domain, date=None):
    date = date or datetime.date.today()
    if not isinstance(date, str):
        date = date.isoformat()
    return sanitize_filename("%s 对标拆解 %s" % (domain, date))


def create_run_dir(root, domain, date=None):
    """Create and return a fresh ``<root>/<domain> 对标拆解 <date>[ (N)]``."""
    os.makedirs(root, exist_ok=True)
    base = run_dir_name(domain, date)
    n = 1
    while True:
        name = base if n == 1 else "%s (%d)" % (base, n)
        path = os.path.join(root, name)
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            n += 1


def sanitize_filename(name, fallback="store"):
    """Drop characters Windows forbids in file names and trim dots/spaces."""
    out = "".join(
        " " if (c in ILLEGAL_CHARS or ord(c) < 32) else c for c in str(name or "")
    )
    out = " ".join(out.split()).strip(" .")
    return out[:120].strip(" .") or fallback


def report_basename(shop_name, domain):
    return sanitize_filename(shop_name or domain) + REPORT_SUFFIX


def write_readme(raw_dir):
    """Write 说明.txt; a multi-store compare folder gets its own wording."""
    path = os.path.join(raw_dir, README_NAME)
    compare = os.path.isfile(os.path.join(raw_dir, COMPARE_MARKER))
    # BOM + CRLF so old Notepad versions show Chinese correctly.
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(README_TEXT_COMPARE if compare else README_TEXT)
    return path


def browser_candidates(platform=None, environ=None, home=None):
    """Absolute browser paths to probe, in preference order."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = home or os.path.expanduser("~")
    out = []
    if platform.startswith("win"):
        pf86 = environ.get("ProgramFiles(x86)") or environ.get("PROGRAMFILES(X86)")
        pf = environ.get("ProgramFiles") or environ.get("PROGRAMFILES")
        local = environ.get("LocalAppData") or environ.get("LOCALAPPDATA")
        for base in (pf86, pf, local):
            if base:
                out.append(
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
                )
        for base in (pf, pf86, local):
            if base:
                out.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                )
    elif platform == "darwin":
        apps = [
            "Google Chrome.app/Contents/MacOS/Google Chrome",
            "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "Chromium.app/Contents/MacOS/Chromium",
        ]
        for base in ("/Applications", os.path.join(home, "Applications")):
            for app in apps:
                out.append(os.path.join(base, app))
    return out


LINUX_BROWSERS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
]


def find_browser(platform=None, environ=None, home=None, which=shutil.which):
    """Return the path of a Chromium-family browser or None."""
    platform = platform or sys.platform
    for path in browser_candidates(platform, environ, home):
        if os.path.isfile(path):
            return path
    if not platform.startswith("win") and platform != "darwin":
        for name in LINUX_BROWSERS:
            found = which(name)
            if found:
                return found
    return None


def in_cloud_sandbox(path=None):
    if path and os.path.abspath(path).startswith(SANDBOX_OUTPUTS):
        return True
    return os.path.isdir(SANDBOX_OUTPUTS)


def can_open_gui(path=None, platform=None, environ=None):
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    if in_cloud_sandbox(path):
        return False
    if platform.startswith("win") or platform == "darwin":
        return True
    return bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))


def open_folder(path):
    """Best effort: show ``path`` in the file manager. Returns True if launched."""
    if not can_open_gui(path):
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            opener = shutil.which("xdg-open")
            if not opener:
                return False
            subprocess.Popen(
                [opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        return True
    except (OSError, AttributeError):
        return False


def safe_console():
    """Never crash on a console that cannot encode some character (old Windows)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
