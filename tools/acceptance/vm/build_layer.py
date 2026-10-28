#!/usr/bin/env python3
"""Build the agent-clients layer in a Windows test VM, then (optionally) snapshot it.

    python3 tools/acceptance/vm/build_layer.py --vm <test VM> --installers <dir> \
        [--user ming] [--snapshot agent-clients-YYYY-MM-DD]

<dir> holds the pinned installers and a layer.json describing them:
    {"python_tag": "3.14", "codex_npm": "@openai/codex@0.164.0",
     "claude_version": "2.1.312",
     "files": [{"role": "python_manager", "name": "python-manager-26.4.msix"},
               {"role": "node_msi", "name": "node-v24.22.1-arm64.msi"},
               {"role": "git_exe", "name": "Git-2.56.0.4-arm64.exe"},
               {"role": "claude_exe", "name": "claude-2.1.312-win32-arm64.exe"}]}
SHA256 and sizes are computed here and checked again inside the guest.
Runs guest/build-client-layer.ps1 as the interactive user; trusts only the
result.json it writes. Never touches protected VMs (prl.PROTECTED).
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prl  # noqa: E402
import transfer  # noqa: E402

GUEST_DIR = r"C:\skilltest\build"
TASK = "SkilltestBuildClientLayer"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(installers):
    with open(os.path.join(installers, "layer.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    for f in spec["files"]:
        p = os.path.join(installers, f["name"])
        f["sha256"] = sha256_file(p)
        f["size"] = os.path.getsize(p)
    return spec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", required=True)
    ap.add_argument("--installers", required=True)
    ap.add_argument("--user", default="ming")
    ap.add_argument("--snapshot", help="take this snapshot after a good build")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    vm = prl.VM(args.vm)
    vm._mutable()
    spec = manifest(args.installers)
    serve = os.path.join(args.installers, "_serve")
    shutil.rmtree(serve, ignore_errors=True)
    os.makedirs(serve)
    for f in spec["files"]:
        os.link(
            os.path.join(args.installers, f["name"]), os.path.join(serve, f["name"])
        )
    shutil.copy(os.path.join(HERE, "guest", "build-client-layer.ps1"), serve)
    with open(os.path.join(serve, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)

    print("waiting for guest readiness", flush=True)
    vm.wait_ready(args.user)
    with transfer.RoundServer(serve, os.path.join(args.installers, "_collect")) as srv:
        base = srv.get_base
        vm.ps_json(
            "$ErrorActionPreference='Stop'; $d=%s; "
            "Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue; "
            "New-Item -ItemType Directory -Force $d | Out-Null; "
            "Invoke-WebRequest -UseBasicParsing -Uri %s -OutFile (Join-Path $d 'build-client-layer.ps1'); "
            "'{\"ok\":true}'"
            % (prl.ps_quote(GUEST_DIR), prl.ps_quote(base + "/build-client-layer.ps1")),
            tries=1,
        )
        vm.run_as_user(
            TASK,
            args.user,
            GUEST_DIR + r"\build-client-layer.ps1",
            '-Base "%s" -Out "%s"' % (base, GUEST_DIR),
        )
        deadline = time.time() + args.timeout
        raw = None
        while time.time() < deadline:
            raw = vm.read_file(GUEST_DIR + r"\result.json")
            if raw:
                break
            time.sleep(15)
    if not raw:
        print("FAIL: no result.json from guest (inconclusive)")
        return 2
    res = json.loads(raw.decode("utf-8-sig"))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    vm.ps(
        "Unregister-ScheduledTask -TaskName %s -Confirm:$false -ErrorAction SilentlyContinue"
        % prl.ps_quote(TASK),
        tries=1,
    )
    if not res.get("ok"):
        return 1
    if args.snapshot:
        snap = vm.snapshot(args.snapshot, "agent clients layer, no credentials")
        print("snapshot %s %s" % (args.snapshot, snap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
