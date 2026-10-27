"""Run owned native acceptance against one exact published snapshot.

Usage: run-installed.py <snapshot_dir> [case ...]
Each case runs the same proof script used for the source acceptance, pointed at
the snapshot. Decisive result.json/PNG/status files are copied into
evidence/installed-<case>/ and judged from their recorded checks, not only from
the process exit code.
"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "ciel_runtime.py").exists())
DAY = REPO / "docs/journal/2026/10/26"
SNAP = Path(sys.argv[1]).resolve()
assert (SNAP / "ciel_runtime.py").is_file(), SNAP
OUT = HERE.parent
TEMP = Path("C:/Users/djlov/AppData/Local/Temp")
SEND = OUT / "installed-sendmessage"
cases = {
 "compact-sse": ("diagnostics/compact-retry/evidence/repro/shared_recovery_e2e.py", ["--runtime-root", str(SNAP), "--label", "installed-shared"], {}, ("repo", "run-installed-shared-")),
 "hybrid": ("fixes/work-hybrid-contract/evidence/e2e_hybrid.py", [], {"WORK_PROOF_REPO": str(SNAP)}, ("temp", "work-hybrid-")),
 "shell": ("fixes/work-shell-permissions/evidence/e2e_shell.py", [], {"WORK_PROOF_REPO": str(SNAP)}, ("temp", "work-shell-proof-")),
 "handoff": ("diagnostics/edward-sse-injection/evidence/e2e_handoff.py", [], {"HANDOFF_PROOF_REPO": str(SNAP), "HANDOFF_LAUNCHER": "1"}, ("temp", "ciel-handoff-")),
 "work-stop": ("diagnostics/ara-work-queue/evidence/e2e_stop_notice.py", [], {"WORK_PROOF_REPO": str(SNAP)}, ("temp", "ara-work-stop-")),
 "codex": ("fixes/codex-durable-recovery/evidence/native_e2e.py", [str(SNAP), str(OUT / "installed-codex")], {}, None),
 "sendmessage-cielairouter": ("fixes/sendmessage-policy/evidence/probe.py", ["--provider", "cielairouter"], {"SENDMESSAGE_PROOF_REPO": str(SNAP), "SENDMESSAGE_PROOF_OUT": str(SEND)}, ("send", "run-installed-cielairouter-default-")),
 "sendmessage-vllm": ("fixes/sendmessage-policy/evidence/probe.py", ["--provider", "vllm"], {"SENDMESSAGE_PROOF_REPO": str(SNAP), "SENDMESSAGE_PROOF_OUT": str(SEND)}, ("send", "run-installed-vllm-default-")),
}
selected = sys.argv[2:] or list(cases)
SEND.mkdir(exist_ok=True)


def newest(where, prefix, since):
    base = {"repo": DAY / "diagnostics/compact-retry/evidence/repro", "temp": TEMP, "send": SEND}[where]
    rows = [p for p in base.glob(prefix + "*") if p.is_dir() and p.stat().st_mtime >= since]
    return max(rows, key=lambda p: p.stat().st_mtime) if rows else None


def sendmessage_verdict(run):
    data = json.loads((run / "result.json").read_text(encoding="utf-8"))
    rows = data.get("requests") or []
    parents = [r for r in rows if r.get("tag") == "parent"]
    acked = False
    for r in parents:
        body = (run / f"request-{r['n']:03}.json").read_text(encoding="utf-8")
        acked = acked or "OWNED-CHILD-ACK-721" in body
    return {"exit": data.get("exit"), "error": data.get("error"), "paths": sorted({r["path"] for r in rows}),
            "sendmessage_advertised": any("SendMessage" in (r.get("tools") or []) for r in parents),
            "child_received": any(r.get("tag") == "child" and r.get("marker_seen") for r in rows),
            "child_ack": acked}


def judge(name, run):
    if run is None:
        return {"passed": False, "reason": "no run directory"}
    if name.startswith("sendmessage"):
        v = sendmessage_verdict(run)
        v["passed"] = v["exit"] == 0 and not v["error"] and v["sendmessage_advertised"] and v["child_received"] and v["child_ack"]
        return v
    result = run / "result.json"
    if not result.is_file():
        return {"passed": False, "reason": "no result.json"}
    data = json.loads(result.read_text(encoding="utf-8"))
    if name == "compact-sse":
        admission = data.get("admission") or {}
        checks = {"compact_dispatched_once": data.get("status") == "compact_dispatched_once",
                  "one_bounded_retry": data.get("compact_requests") == 2,
                  "phase_verified": admission.get("phase") == "verified",
                  "queued_sse_replied_once": data.get("queue_replied_once") is True and data.get("queue_model_requests") == 1,
                  "live_unchanged": data.get("live_unchanged") is True}
        return {"checks": checks, "passed": all(checks.values())}
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    verdict = {"checks": checks, "error": data.get("error"), "verification_failed": data.get("verification_failed")}
    if "status" in data:
        verdict["status"] = data.get("status")
    verdict["passed"] = (not data.get("error") and not data.get("verification_failed")
                         and (all(checks.values()) if checks else True))
    return verdict


results = []
results_file = OUT / "installed-results.json"
if results_file.is_file():
    results = [r for r in json.loads(results_file.read_text(encoding="utf-8")) if r["case"] not in selected]
for name in selected:
    rel, args, extra, locate = cases[name]
    env = os.environ.copy(); env.update(extra)
    log = OUT / ("installed-" + name + ".txt")
    since = time.time() - 1
    with log.open("w", encoding="utf-8") as stream:
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(DAY / rel), *args], env=env, stdout=stream, stderr=subprocess.STDOUT)
    run = (OUT / "installed-codex") if locate is None else newest(*locate, since)
    verdict = judge(name, run) if name != "codex" else {"passed": proc.returncode == 0}
    keep = OUT / ("installed-" + name)
    if run is not None and locate is not None and locate[0] == "temp":
        keep.mkdir(exist_ok=True)
        for f in list(run.glob("*.png")) + [run / x for x in ("result.json", "live-before.json", "live-after.json", "requests-summary.json")]:
            if f.is_file():
                shutil.copy2(f, keep / f.name)
    row = {"case": name, "exit_code": proc.returncode, "run": str(run) if run else None, "log": str(log), **verdict}
    results.append(row)
    results_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(name, proc.returncode, "PASS" if verdict["passed"] else "FAIL", flush=True)
raise SystemExit(0 if all(r["passed"] for r in results if r["case"] in selected) else 1)
