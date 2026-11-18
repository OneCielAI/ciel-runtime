"""Owned probe: when does Claude Code show the folder-trust dialog, and what does it persist?

Isolated: a temporary USERPROFILE/HOME, no CLAUDE_CONFIG_DIR, a fixture token, an
unreachable API base. Only processes this probe starts are stopped (Guard).

    python trust_probe.py <out dir>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "docs/journal/2026/11/17/fixes/work-workspace-trust/e2e-source"))
import p0_common as common  # noqa: E402
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
common.PROTECTED_PIDS_FILE = REPO / "docs/journal/2026/11/17/fixes/work-workspace-trust/e2e-source/protected-pids.json"
GUARD = common.Guard(OUT)
CLI = str(Path.home() / ".local/bin/claude.exe")
TRUST_MARKERS = ("trust", "Trust")


def keys(path: Path):
    return [str(path), path.as_posix()]


def run_case(name, home: Path, cwd: Path, projects, answer, wait=25):
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark",
                               "projects": projects if isinstance(projects, dict) else {}}), encoding="utf-8")
    if projects == "product":
        from ciel_runtime_support.claude_user_settings import trust_restart_folder
        trust_restart_folder(cwd, env={}, home=home)
    env = common.stripped_env()
    for var in ("CLAUDE_CONFIG_DIR",):
        env.pop(var, None)
    env.update(USERPROFILE=str(home), HOME=str(home), ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="",
               ANTHROPIC_BASE_URL="http://127.0.0.1:9", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               DISABLE_AUTOUPDATER="1", CIEL_RUNTIME_TEST_ISOLATED="1")
    os.chdir(cwd)
    proc = WindowsConPtySession([CLI], env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    root = GUARD.register_root(proc.pid)
    shown = False
    screen = ""
    deadline = time.time() + wait
    try:
        while time.time() < deadline:
            time.sleep(1)
            screen = common.screen_text(proc.output_tail())
            if any(marker in screen for marker in TRUST_MARKERS) and ("folder" in screen or "files" in screen):
                shown = True
                break
            if "? for shortcuts" in screen or "for shortcuts" in screen:
                break
        if shown and answer is not None:
            for key in answer:
                proc.write(key)
                time.sleep(0.7)
            time.sleep(8)
        after = common.screen_text(proc.output_tail())
    finally:
        kills = GUARD.kill_owned(root)
        proc.close()
    time.sleep(1)
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    persisted = {key: (doc.get("projects") or {}).get(key, {}).get("hasTrustDialogAccepted") for key in keys(cwd)}
    (OUT / (name + ".screen.txt")).write_text(screen.replace(str(home), "<home>"), encoding="utf-8")
    (OUT / (name + ".after.txt")).write_text(after.replace(str(home), "<home>"), encoding="utf-8")
    return {"case": name, "cwd_is_home": cwd == home, "pre_trusted": projects if isinstance(projects, str) else bool(projects), "dialog_shown": shown,
            "answered": shown and answer is not None, "persisted_after": persisted, "kills": len(kills)}


version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
results = {"claude_version": version, "cases": []}
base = Path(tempfile.mkdtemp(prefix="ciel-trust-probe-"))
try:
    for name, in_home, pre, answer in (
            ("home-untrusted", True, False, None),
            ("home-product-trust", True, "product", None),
            ("sub-product-trust", False, "product", None)):
        home = base / name
        home.mkdir()
        cwd = home if in_home else home / "work"
        cwd.mkdir(exist_ok=True)
        projects = "product" if pre == "product" else ({key: {"hasTrustDialogAccepted": True} for key in keys(cwd)} if pre else {})
        results["cases"].append(run_case(name, home, cwd, projects, answer))
finally:
    os.chdir(REPO)
    final = GUARD.final_report([])
    results["owned_survivors"] = len(final["owned_survivors"])
(OUT / "result.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
print(json.dumps(results, indent=1))
