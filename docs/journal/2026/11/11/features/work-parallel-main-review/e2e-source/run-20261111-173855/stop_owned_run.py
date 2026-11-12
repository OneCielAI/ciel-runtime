"""Stop one owned E2E run from outside its runner (team-lead hold, freeze not final).

Owned tree: creation-fenced descent from the detached root's exact identity.
Protected: creation-fenced descent from the protected roots' identities in
live-before.json plus that snapshot's exact identities. Every owned identity is
pinned by handle (creation time verified), suspended, then killed leaves-first
with taskkill /PID n /F (no /T). An identity in both sets is refused.
"""
import ctypes as ct
import json
import subprocess
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

RUN = Path(sys.argv[1])
ROOT = (int(sys.argv[2]), sys.argv[3])
OUT = RUN / "stop-kills.jsonl"
NO_WINDOW = 0x08000000
k32 = ct.WinDLL("kernel32", use_last_error=True)
nt = ct.WinDLL("ntdll")
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
nt.NtSuspendProcess.argtypes = [wt.HANDLE]
nt.NtResumeProcess.argtypes = [wt.HANDLE]


def log(row):
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n")


def rows():
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
              "@{n='CreationDateUtc';e={$_.CreationDate.ToUniversalTime().ToString('o')}},CommandLine "
              "| ConvertTo-Json -Depth 3")
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL)
    if done.returncode:
        raise RuntimeError("process query failed: " + done.stderr[:300])
    return json.loads(done.stdout)


def ident(row):
    return int(row["ProcessId"]), str(row["CreationDateUtc"])


def fenced(all_rows, roots):
    accepted = {}
    by = {int(r["ProcessId"]): r for r in all_rows}
    for pid, created in roots:
        if pid in by and ident(by[pid]) == (pid, created):
            accepted[pid] = by[pid]
    while True:
        added = 0
        for r in all_rows:
            pid, parent = int(r["ProcessId"]), int(r["ParentProcessId"])
            if pid not in accepted and parent in accepted and str(r["CreationDateUtc"]) > str(accepted[parent]["CreationDateUtc"]):
                accepted[pid] = r
                added += 1
        if not added:
            return list(accepted.values())


def ft_iso(ft):
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    seconds, fraction = divmod(ticks, 10_000_000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds - 11644473600)) + ".%06d0Z" % (fraction // 10)


def pin(key):
    handle = k32.OpenProcess(0x0800 | 0x1000 | 0x00100000, False, key[0])
    if not handle:
        return None
    times = [wt.FILETIME() for _ in range(4)]
    if not k32.GetProcessTimes(handle, *[ct.byref(t) for t in times]) or ft_iso(times[0]) != key[1]:
        k32.CloseHandle(handle)
        return None
    return handle


before = json.loads((RUN / "live-before.json").read_text(encoding="utf-8"))["protected"]
snap_ids = {ident(r) for r in before}
roots = [ident(r) for r in before if int(r["ProcessId"]) in (23728, 39984, 21912, 9008, 44500, 20744)]
pins, rows_by = {}, {}
for sweep in range(60):
    current = rows()
    protected = {ident(r) for r in fenced(current, roots)} | snap_ids
    live_roots = [k for k, h in pins.items() if k32.WaitForSingleObject(h, 0) == 0x102] or [ROOT]
    new = [r for r in fenced(current, live_roots) if ident(r) not in pins]
    if not new and sweep:
        break
    for r in sorted(new, key=lambda x: str(x["CreationDateUtc"])):
        key = ident(r)
        if key in protected:
            log({"refused_protected": key, "name": r.get("Name")})
            for h in pins.values():
                nt.NtResumeProcess(h)
            raise SystemExit("protected identity inside owned tree; resumed and stopped: %r" % (key,))
        handle = pin(key)
        if handle:
            pins[key], rows_by[key] = handle, r
            nt.NtSuspendProcess(handle)
log({"pinned": [{"pid": k[0], "creation": k[1], "parent": rows_by[k]["ParentProcessId"], "name": rows_by[k]["Name"],
                 "command_line": str(rows_by[k].get("CommandLine") or "")[:300]} for k in sorted(pins)]})
while True:
    live = {k: h for k, h in pins.items() if k32.WaitForSingleObject(h, 0) == 0x102}
    if not live:
        break
    parents = {int(rows_by[k]["ParentProcessId"]) for k in live}
    leaves = [k for k in live if k[0] not in parents] or list(live)
    for key in leaves:
        cmd = ["taskkill", "/PID", str(key[0]), "/F"]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL)
        dead = k32.WaitForSingleObject(live[key], 5000) == 0
        log({"target": {"pid": key[0], "creation": key[1], "name": rows_by[key]["Name"]}, "command": cmd,
             "rc": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "dead": dead})
final = rows()
left = [ident(r) for r in fenced(final, [k for k in pins if k in {ident(x) for x in final}] or [ROOT])]
log({"done": True, "pinned": len(pins), "remaining": left})
for h in pins.values():
    k32.CloseHandle(h)
print(json.dumps({"pinned": len(pins), "remaining": left}))
