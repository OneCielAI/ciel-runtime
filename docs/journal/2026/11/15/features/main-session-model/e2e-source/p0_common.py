"""Shared owned-process machinery for the P0 real-console preflights (not acceptance).

Guard (lead ruling 2026-11-12): protected identities are (pid, creation UTC)
pairs with all creation-fenced descendants: the lead-listed identities that are
present, plus every claude.exe and every py/python process whose command line
matches ciel[-_]runtime outside this driver's own tree. The process table is
enumerated again before every single taskkill. Only identities this driver
created are killed, leaves first, through pinned handles, never with /T.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

NO_WINDOW = 0x08000000
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
# Lead-supplied protected identities. Before the 2026-11-13 10:03Z reboot they
# were fixed (pid, creation) pairs; after it the lead names live pids only
# (routers and claude.exe sessions), in protected-pids.json beside this file.
# Each listed pid that is present is protected under the creation time the
# process table shows when the Guard starts (a reused pid is protected too:
# over-protection only). Missing file or no listed pid present: refuse to run.
PROTECTED_PIDS_FILE = Path(__file__).resolve().parent / "protected-pids.json"
STATIC_PROTECTED: set = set()


def load_protected(rows: list[dict]) -> set:
    try:
        listed = json.loads(PROTECTED_PIDS_FILE.read_text(encoding="utf-8"))["pids"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError("protected-pids.json missing or unreadable; refusing to run blind") from error
    pids = {int(pid) for pid in listed}
    STATIC_PROTECTED.clear()
    STATIC_PROTECTED.update(ident(row) for row in rows if int(row["ProcessId"]) in pids)
    return pids
LAUNCHER_NAME = re.compile(r"^(py|pyw|python[0-9.]*|pythonw)\.exe$", re.I)
CIEL_COMMAND = re.compile(r"ciel[-_]runtime", re.I)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ps_rows() -> list[dict]:
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
              "@{n='CreationDateUtc';e={$_.CreationDate.ToUniversalTime().ToString('o')}},CommandLine | "
              "ConvertTo-Json -Depth 3 -Compress")
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL)
    if done.returncode:
        raise RuntimeError("process query failed: " + done.stderr[:300])
    value = json.loads(done.stdout, strict=False) if done.stdout.strip() else []
    return value if isinstance(value, list) else [value]


LOAD_NAMES = ("conhost.exe", "node.exe", "git.exe", "claude.exe", "python.exe", "py.exe", "bash.exe", "uv.exe")


def system_load(rows: list[dict] | None = None) -> dict:
    """Machine-wide process counts, recorded in each result header (lead rule:
    heavy suites run one at a time; 0xC0000142 was seen under 793 processes / 71 conhost)."""
    rows = ps_rows() if rows is None else rows
    names = [str(row.get("Name") or "").lower() for row in rows]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "processes": len(rows),
            **{name.split(".")[0]: names.count(name) for name in LOAD_NAMES}}


MOD_VERDICT = re.compile(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*")
MOD_SETTLED = re.compile(r"hooks module ciel-work@\S+ ([A-Za-z][\w.:-]*) settled in ")


def plugin_load_verdict(debug: Path, plugin: str) -> dict:
    """The loader's verdict for any one plugin's hooks module, with the hooks it settled."""
    text = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
    name = re.escape(plugin)
    verdicts = [m.group(0)[:1200] for m in re.finditer(r"hooks module " + name + r"@\S+ (loaded|failed to load)[^\n]*", text)]
    # A settle line can name several modules of one chain: "hooks module a@inline,
    # b@inline, c@inline tool.check settled in" (measured, run-w-L1r-router-…094400).
    settled = sorted(set(m.group(2) for m in re.finditer(
        r"hooks module ((?:[\w.-]+@\w+, )*)" + r"(?:(?<=module )|(?<=, ))" + name + r"@\w+(?:, [\w.-]+@\w+)* ([A-Za-z][\w.:-]*) settled in ",
        text)))
    failed = [line for line in verdicts if "failed to load" in line]
    loaded = any(" loaded" in line.split("@", 1)[-1][:80] for line in verdicts)
    return {"plugin": plugin, "loaded": loaded, "failed": failed, "hooks_ran": settled}


def mod_load_verdict(debug: Path) -> dict:
    """The loader's own verdict for ciel-work in one claude.exe debug log.

    ok needs a positive observation: a "loaded" line AND a ciel-work session
    hook settling (classic.SessionStart or session.start ran; the measured
    revival run shows classic.SessionStart while session.start was still
    awaiting its pump). A missing failure line is not enough.
    """
    text = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
    verdicts = [m.group(0)[:1200] for m in MOD_VERDICT.finditer(text)]
    settled = sorted(set(m.group(1) for m in MOD_SETTLED.finditer(text)))
    failed = [line for line in verdicts if "failed to load" in line]
    loaded = any(line.split(" ", 3)[3].startswith("loaded") for line in verdicts if len(line.split(" ", 3)) > 3)
    return {"loaded": loaded, "failed": failed, "hooks_ran": settled,
            "ok": loaded and not failed and bool({"session.start", "classic.SessionStart"} & set(settled))}


def wait_mod_loaded(debug: Path, timeout: float = 60.0) -> dict:
    """Wait for a decisive verdict; returns as soon as the mod failed or ran session.start."""
    end = time.time() + timeout
    verdict = mod_load_verdict(debug)
    while time.time() < end and not verdict["failed"] and not verdict["ok"]:
        time.sleep(0.5)
        verdict = mod_load_verdict(debug)
    return verdict


class ModNotLoaded(RuntimeError):
    """The product mod did not load and run in a real claude.exe: the run is invalid."""


def ident(row: dict) -> tuple[int, str]:
    return int(row["ProcessId"]), str(row["CreationDateUtc"])


def strict_tree(rows: list[dict], roots, stop=frozenset()) -> list[dict]:
    by_pid = {int(row["ProcessId"]): row for row in rows}
    accepted = {pid: by_pid[pid] for pid, created in roots if pid in by_pid and ident(by_pid[pid]) == (pid, created)}
    while True:
        added = 0
        for row in rows:
            pid, parent = int(row["ProcessId"]), int(row["ParentProcessId"])
            if pid in accepted or parent not in accepted or ident(accepted[parent]) in stop:
                continue
            if str(row["CreationDateUtc"]) > str(accepted[parent]["CreationDateUtc"]):
                accepted[pid] = row
                added += 1
        if not added:
            return list(accepted.values())


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ACCESS = 0x0800 | 0x1000 | 0x00100000


def filetime_iso(ft: wintypes.FILETIME) -> str:
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    sec, frac = divmod(ticks, 10_000_000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec - 11644473600)) + ".%06d0Z" % (frac // 10)


class Pinned:
    def __init__(self, pid: int, created: str):
        self.pid, self.created = pid, created
        self.handle = _k32.OpenProcess(_ACCESS, False, pid)
        if self.handle:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _k32.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]) or filetime_iso(times[0]) != created:
                _k32.CloseHandle(self.handle)
                self.handle = None

    def alive(self) -> bool:
        return bool(self.handle) and _k32.WaitForSingleObject(self.handle, 0) == 0x102

    def wait(self, ms: int):
        if self.handle:
            _k32.WaitForSingleObject(self.handle, ms)

    def suspend(self) -> bool:
        return bool(self.handle) and _ntdll.NtSuspendProcess(self.handle) == 0

    def resume(self) -> bool:
        return bool(self.handle) and _ntdll.NtResumeProcess(self.handle) == 0

    def close(self):
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


class Guard:
    def __init__(self, out: Path):
        self.out = out
        rows = ps_rows()
        listed = load_protected(rows)
        self.owner = next(ident(row) for row in rows if int(row["ProcessId"]) == os.getpid())
        self.roots0 = self.protected_roots(rows, set())
        self.protected0 = self.protected_identities(rows, set())
        present = {ident(row) for row in rows}
        self.report = {
            "owner": self.owner,
            "listed_pids": sorted(listed),
            "static_present": sorted(key for key in STATIC_PROTECTED if key in present),
            "static_missing_logged": sorted(pid for pid in listed if pid not in {key[0] for key in STATIC_PROTECTED}),
            "dynamic_roots": [{"identity": key, "name": next(r.get("Name") for r in rows if ident(r) == key)}
                              for key in self.roots0 if key not in STATIC_PROTECTED],
            "protected_identity_count": len(self.protected0),
            "owner_in_protected_closure": self.owner in self.protected0,
            "protected_identities": sorted(
                ({"pid": int(r["ProcessId"]), "created": str(r["CreationDateUtc"]), "name": r.get("Name"),
                  "parent": int(r["ParentProcessId"])} for r in rows if ident(r) in self.protected0),
                key=lambda item: (item["created"], item["pid"])),
        }
        self.report["owner_parent_chain"] = self.parent_chain(rows, self.owner)
        if not self.report["static_present"]:
            raise RuntimeError("no lead-supplied protected identity is present; refusing to run blind")
        self.roots: list[tuple[int, str]] = []

    def parent_chain(self, rows, start) -> list[dict]:
        """The driver's ancestors as the table shows them, with where the chain ends and why."""
        by_pid = {int(r["ProcessId"]): r for r in rows}
        chain, seen = [], set()
        row = by_pid.get(start[0])
        while row is not None and int(row["ProcessId"]) not in seen:
            seen.add(int(row["ProcessId"]))
            key = ident(row)
            entry = {"pid": key[0], "created": key[1], "name": row.get("Name"), "parent_pid": int(row["ParentProcessId"]),
                     "protected": key in self.protected0, "protected_root": key in self.roots0}
            chain.append(entry)
            parent = by_pid.get(int(row["ParentProcessId"]))
            if parent is None:
                entry["chain_end"] = "parent pid absent from the process table"
                break
            if not str(parent["CreationDateUtc"]) < key[1]:
                entry["chain_end"] = ("parent pid now belongs to a process created at " + str(parent["CreationDateUtc"])
                                      + ", after this one: pid reused, creation fence stops the chain")
                break
            row = parent
        return chain

    def owned_chain(self, rows, key, root) -> str | None:
        """None when key is root or a creation-fenced descendant of it and root's parent is the driver."""
        by_pid = {int(r["ProcessId"]): r for r in rows}
        row = by_pid.get(key[0])
        if row is None or ident(row) != key:
            return "exact identity not present"
        for _hop in range(64):
            if ident(row) == root:
                parent = by_pid.get(int(row["ParentProcessId"]))
                if parent is None or ident(parent) != self.owner:
                    return "owned root's parent is no longer the driver"
                return None
            parent = by_pid.get(int(row["ParentProcessId"]))
            if parent is None:
                return "parent absent: reparented or orphaned, not provably ours"
            if not str(parent["CreationDateUtc"]) < str(row["CreationDateUtc"]):
                return "parent pid reused (created after child)"
            row = parent
        return "chain longer than 64 hops"

    def protected_roots(self, rows, owned) -> list[tuple[int, str]]:
        found = []
        for row in rows:
            key = ident(row)
            if key == getattr(self, "owner", None) or key in owned:
                continue
            name = str(row.get("Name") or "")
            command = str(row.get("CommandLine") or "")
            if key in STATIC_PROTECTED or name.lower() == "claude.exe" or (
                    LAUNCHER_NAME.match(name) and CIEL_COMMAND.search(command)):
                found.append(key)
        return found

    def protected_identities(self, rows, owned) -> set:
        return {ident(row) for row in strict_tree(rows, self.protected_roots(rows, owned), stop={self.owner})}

    def register_root(self, pid: int) -> tuple[int, str]:
        for _ in range(50):
            row = next((r for r in ps_rows() if int(r["ProcessId"]) == pid), None)
            if row:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("owned process was not observable")
        root = ident(row)
        # Only an identity this driver created: direct child of the driver, created after it.
        if root in self.protected0 or int(row["ParentProcessId"]) != self.owner[0] or root[1] <= self.owner[1]:
            raise RuntimeError("owned root is not a process created by this driver")
        self.roots.append(root)
        return root

    def _record(self, record):
        with open(self.out / "kills.jsonl", "a", encoding="utf-8") as sink:
            sink.write(json.dumps(record) + "\n")

    def kill_owned(self, root: tuple[int, str]) -> list[dict]:
        if root not in self.roots:
            raise RuntimeError("kill root was not registered as created by this driver")
        rows = ps_rows()
        owned_rows = strict_tree(rows, [root])
        owned = {ident(row) for row in owned_rows}
        overlap = owned & (self.protected_identities(rows, owned) | self.protected0)
        if overlap:
            raise RuntimeError("owned/protected process overlap: " + repr(sorted(overlap)))
        for row in owned_rows:
            reason = self.owned_chain(rows, ident(row), root)
            if reason:
                raise RuntimeError("owned tree identity " + repr(ident(row)) + " failed the chain check: " + reason)
        pinned = {}
        for row in owned_rows:
            pin = Pinned(*ident(row))
            if pin.handle:
                pinned[ident(row)] = (pin, row)
        for pin, _ in pinned.values():
            pin.suspend()
        records = []
        try:
            pids = {key[0] for key in pinned}
            for _round in range(200):
                live = {key: value for key, value in pinned.items() if value[0].alive()}
                if not live:
                    break
                parents = {int(row["ParentProcessId"]) for _, row in live.values()}
                leaves = [key for key in live if key[0] not in parents & pids] or list(live)
                for key in leaves:
                    pin, row = live[key]
                    rows = ps_rows()
                    present = {ident(r) for r in rows}
                    owned_now = {ident(r) for r in strict_tree(rows, [root])} | set(pinned)
                    protected_now = self.protected_identities(rows, owned_now) | self.protected0
                    record = {"at": time.time(), "root": root, "identity": key, "name": row.get("Name"),
                              "present": key in present, "protected": key in protected_now}
                    if key in protected_now:
                        record["refused"] = "target is or descends from a protected identity"
                        records.append(record); self._record(record)
                        raise RuntimeError("kill refused for protected identity " + repr(key))
                    if key not in present:
                        record["skipped"] = "absent from process table; no signal sent"
                        pin.wait(5000)
                        records.append(record); self._record(record)
                        continue
                    # Fresh check: still this exact identity, still in the driver's strict
                    # owned tree with the expected parent chain back to the driver.
                    chain_fault = self.owned_chain(rows, key, root)
                    record["owned_chain"] = chain_fault or "ok"
                    if chain_fault:
                        record["refused"] = "owned-chain check failed: " + chain_fault
                        records.append(record); self._record(record)
                        raise RuntimeError("kill refused for " + repr(key) + ": " + chain_fault)
                    if not pin.alive() or pin.created != key[1]:
                        record["refused"] = "pinned handle no longer matches the identity"
                        records.append(record); self._record(record)
                        raise RuntimeError("kill refused for " + repr(key) + ": pinned handle mismatch")
                    command = ["taskkill", "/PID", str(pin.pid), "/F"]
                    done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                          creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
                    record.update(command=command, rc=done.returncode)
                    records.append(record); self._record(record)
                    pin.wait(3000)
            return records
        finally:
            for pin, _ in pinned.values():
                if pin.alive():
                    pin.resume()
                pin.close()

    def final_report(self, kills: list[dict]) -> dict:
        rows = ps_rows()
        present = {ident(row) for row in rows}
        killed = {tuple(row["identity"]) for row in kills if "command" in row}
        return {"owned_survivors": [ident(row) for row in strict_tree(rows, self.roots)],
                "killed_protected_overlap": sorted(killed & self.protected0),
                "protected_roots_absent_at_end": sorted(key for key in self.roots0 if key not in present)}


def stripped_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
    for key in [k for k in os.environ if k.upper().startswith(PREFIXES)]:
        os.environ.pop(key, None)
    return env


def snapshot_source(source: Path, owned: Path) -> Path:
    target = owned / "snapshot"
    shutil.copytree(source / "ciel_runtime_support", target / "ciel_runtime_support",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sys.path.insert(0, str(target))
    return target


MANIFEST_NAMES = (
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js",
    "ciel_runtime_support/claude_mods/ciel-work-gate/hooks/register.js",
    "ciel_runtime_support/work_parallel_gate_presence.py",
    "ciel_runtime_support/work_parallel_mcp.py",
    "ciel_runtime_support/work_parallel_finalization.py",
    "ciel_runtime_support/work_http_controller.py",
    "ciel_runtime_support/work_web_ui.py",
    "ciel_runtime_support/windows_conpty.py",
    "ciel_runtime_support/work_parallel.py",
    "ciel_runtime_support/work_parallel_workspace.py",
    "ciel_runtime_support/work_parallel_stranded.py",
    "ciel_runtime_support/work_parallel_failures.py",
    "ciel_runtime_support/work_mcp_tools.py",
    "ciel_runtime_support/channel_mcp_http_controller.py")


def manifest(snapshot: Path, source: Path, label: str | None = None) -> dict:
    # The run label (lead rule): P0_LABEL, e.g. "interim-backend"; default "interim".
    label = label or os.environ.get("P0_LABEL") or "interim"
    digests = {name: sha(snapshot / name) for name in MANIFEST_NAMES if (snapshot / name).exists()}
    return {"label": label, "digests": digests, "missing": [n for n in MANIFEST_NAMES if not (snapshot / n).exists()],
            "matches_source_at_copy": all((source / name).exists() and sha(source / name) == value
                                          for name, value in digests.items())}


def screen_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\\\)", "", text)
    text = re.sub(r"\x1b.", "", text)
    return "\n".join(line.rstrip() for line in text.replace("\r", "\n").split("\n") if line.strip())
