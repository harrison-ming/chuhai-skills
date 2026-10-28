"""Delivery folders and local-desktop helpers.

Layout used by every skill (folder names come from the skill and ``lang``)::

    <root>/                          e.g. Documents/出海拆解报告
        <subject> <label> <date>/    one run, e.g. "example.com 对标拆解 2026-09-30"
            <report>.pdf | .html     what the seller opens
            <table>.csv              Excel / WPS friendly
            原始数据/ (en: data/)     raw data, README, report.md, report.html

The root resolves as ``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox
outputs > ``<Documents>/<root name>``.
Python 3.8+, standard library only.
"""

import datetime
import locale
import os
import shutil
import subprocess
import sys

from . import i18n

ENV_OUTPUT = "CHUHAI_OUTPUT_DIR"
SANDBOX_OUTPUTS = "/mnt/user-data/outputs"
ILLEGAL_CHARS = '\\/:*?"<>|'
LINUX_BROWSERS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
]


def raw_dirname(lang=None):
    return i18n.text("raw_dirname", lang)


def readme_name(lang=None):
    return i18n.text("readme_name", lang)


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
            candidates.append(os.path.join(onedrive, "文档"))  # 文档
        home = profile
    else:
        candidates.append(os.path.join(home, "Documents"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return home


def resolve_output_root(
    cli_root=None,
    root_name="report",
    environ=None,
    platform=None,
    home=None,
    sandbox=SANDBOX_OUTPUTS,
):
    """Resolve the delivery root folder (not created here).

    Order: ``--out-root`` > $CHUHAI_OUTPUT_DIR > cloud sandbox outputs >
    ``<Documents>/<root_name>``. The env var names the root itself.
    """
    environ = os.environ if environ is None else environ
    if cli_root:
        return os.path.abspath(os.path.expanduser(cli_root))
    env_root = (environ.get(ENV_OUTPUT) or "").strip()
    if env_root:
        return os.path.abspath(os.path.expanduser(env_root))
    if sandbox and _is_writable_dir(sandbox):
        return os.path.join(sandbox, root_name)
    return os.path.join(documents_dir(platform, environ, home), root_name)


def sanitize_filename(name, fallback="store"):
    """Drop characters Windows forbids in file names and trim dots/spaces."""
    out = "".join(
        " " if (c in ILLEGAL_CHARS or ord(c) < 32) else c for c in str(name or "")
    )
    out = " ".join(out.split()).strip(" .")
    return out[:120].strip(" .") or fallback


def run_dir_name(subject, label, date=None):
    """``<subject> <label> <YYYY-MM-DD>``, file-name safe."""
    date = date or datetime.date.today()
    if not isinstance(date, str):
        date = date.isoformat()
    return sanitize_filename("%s %s %s" % (subject, label, date))


def create_run_dir(root, subject, label, date=None):
    """Create and return a fresh ``<root>/<subject> <label> <date>[ (N)]``."""
    os.makedirs(root, exist_ok=True)
    base = run_dir_name(subject, label, date)
    n = 1
    while True:
        name = base if n == 1 else "%s (%d)" % (base, n)
        path = os.path.join(root, name)
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            n += 1


def locate_run(path, lang=None):
    """Return (run_dir, raw_dir). Accepts the raw-data folder itself too."""
    name = raw_dirname(lang)
    path = os.path.abspath(os.path.expanduser(path))
    raw = os.path.join(path, name)
    if not os.path.isdir(raw) and os.path.basename(path) == name:
        return os.path.dirname(path), path
    return path, raw


def write_notepad_text(path, text):
    """UTF-8 with BOM + CRLF so old Windows Notepad shows Chinese correctly."""
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(text)
    return path


def write_readme(raw_dir, text, lang=None):
    return write_notepad_text(os.path.join(raw_dir, readme_name(lang)), text)


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


NO_OPEN_ENV = "CHUHAI_NO_OPEN"


def can_open_gui(path=None, platform=None, environ=None):
    """False in a cloud sandbox, without a display, or when CHUHAI_NO_OPEN is
    set to a true value (automated runs must not pop file-manager windows)."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    if (environ.get(NO_OPEN_ENV) or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
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


def decode_text(data):
    """Decode a text file someone else wrote (the agent's report.md, a pasted
    page, a user's CSV): BOM first (UTF-8, or UTF-16 from Windows PowerShell
    5.1 ``Out-File``), then UTF-8, then the machine's ANSI code page
    (``Set-Content`` / Notepad "ANSI" / Excel CSV, cp936 on Chinese Windows),
    then GB18030; never raises."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    encs = ["utf-8"]
    try:
        ansi = locale.getpreferredencoding(False)
    except Exception:  # noqa: BLE001 - any locale trouble: skip that guess
        ansi = None
    if ansi and ansi.lower().replace("-", "") not in ("utf8", "ascii"):
        encs.append(ansi)
    encs.append("gb18030")
    for enc in encs:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_text(path):
    """Text of ``path`` via ``decode_text`` (raises OSError like open())."""
    with open(path, "rb") as fh:
        return decode_text(fh.read())


def safe_console():
    """Never crash on a console that cannot encode some character (old Windows)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
