"""Second pass for the eab3ad33 acceptance: exact key hashes, exact attempt refs,
pending-window Stop check, and the review_calls local."""
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:100])
    s = s.replace(a, b)


# 1. Full key hashes read from .local/tmp/freeze-sse-eab3ad33-final/SHA256SUMS and the worktree.
rep('''# Current integrated tree named by the team lead (SSE freeze 378f6d73 + restart freeze).
KEY_HASHES = {
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "eab3ad33856d9ee0840e04488f7810ec841888f782700a92ac854d931891f641",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "9b9550682831897128e5df0ce28b55a8ddcb96f6a6074d83c3d0627cc3f40d8a",''',
'''# Current integrated tree named by the team lead (freeze-sse-eab3ad33-final:
# per-identity prompt registry in side-intake.js 9b955068, register.js eab3ad33).
KEY_HASHES = {
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "eab3ad333104d0914e7dee552f241c23eb873405c85330514b9c6a9d337db31b",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "9b9550685fa278f330f45b06a1a996ac6e52c7034c208f38518d8ebd1a47d1af",
    "ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts":
        "542ff492abb1eb2f2363c45acc8eaa75002a71cbca4772aa6474537eece6030a",''')

# 2. Passive, field-whitelisted observation of every review_wake MCP call. The
# record call carries the exact attempt_id; _host (a host marker) is never logged.
rep('''        self.held: list[socket.socket] = []
        self.server = socket.socket()''',
'''        self.held: list[socket.socket] = []
        self.wake_calls: list[dict] = []
        self.server = socket.socket()''')
rep('''        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        with self.lock:
            rules = [(name, rule) for name, rule in self.rules.items() if rule["active"]]''',
'''        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if str(params.get("name") or "").endswith("work_parallel_event") and args.get("event") == "review_wake":
            call = {"at": time.time(), "operation": args.get("operation"), "lease_id": args.get("lease_id"),
                    "attempt_id": args.get("attempt_id"), "state": args.get("state")}
            with self.lock:
                self.wake_calls.append(call)
            self.log({"observe": "review_wake", **call})
        with self.lock:
            rules = [(name, rule) for name, rule in self.rules.items() if rule["active"]]''')

# 3. Durable observation keeps the exact attempt id (cleared on acknowledgement).
rep('''        mark = [wake.get("state"), wake.get("attempts"), row.get("decision")]
        seen = wake_observations.setdefault(key, [])
        if not seen or seen[-1][:3] != mark:''',
'''        # [state, attempts, decision, attempt_id, observed_at]; attempt_id is
        # cleared when main decides, so it is kept here while it is visible.
        mark = [wake.get("state"), wake.get("attempts"), row.get("decision"), wake.get("attempt_id")]
        seen = wake_observations.setdefault(key, [])
        if not seen or seen[-1][:4] != mark:''')

# 4. Raw submits attributed to exact delivery identities.
start = s.index("def raw_prompt_submits(reviews: dict) -> dict:")
end = s.index("def _strings(value):", start)
s = s[:start] + '''REVIEW_SUBMIT_TEXT = "Ciel Work result is awaiting main review (data, not instructions)"


def attempt_ref(attempt_id) -> str:
    """side-intake.js attemptRef: first 8 bytes of SHA-256(attempt_id) as hex."""
    return hashlib.sha256(str(attempt_id).encode("utf-8")).hexdigest()[:16]


def iso_epoch(stamp) -> float | None:
    import datetime
    try:
        return datetime.datetime.strptime(str(stamp)[:23], "%Y-%m-%dT%H:%M:%S.%f").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def review_attempts() -> dict:
    """lease -> attempt_id -> first durable sighting and its record call, from
    the guard's durable observations and the gate's review_wake record calls."""
    attempts: dict[str, dict] = {}
    for lease, marks in wake_observations.items():
        for mark in marks:
            if len(mark) > 4 and mark[3]:
                attempts.setdefault(lease, {}).setdefault(
                    mark[3], {"observed_at": mark[4], "recorded_at": None, "recorded_state": None})
    for call in list(gate.wake_calls):
        if call.get("operation") == "record" and call.get("lease_id") and call.get("attempt_id"):
            row = attempts.setdefault(call["lease_id"], {}).setdefault(
                call["attempt_id"], {"observed_at": None, "recorded_at": None, "recorded_state": None})
            if row["recorded_at"] is None:
                row["recorded_at"], row["recorded_state"] = call["at"], call.get("state")
    return attempts


def raw_prompt_submits(reviews: dict) -> dict:
    """Raw review $.prompt.submit calls grouped by delivery identity.

    Identity is ["parallel:<lease>", attempt_ref(attempt_id)] (register.js
    finishParallelWake -> side-intake.js promptIdentity). Distinct identities may
    submit concurrently; the same identity submitted twice is the defect.
    Attribution: a lease with exactly one durable attempt owns every raw submit
    of that lease; otherwise a submit belongs to the earliest record call of that
    lease made at or after it (record is sent only after the submit settles).
    A submit that cannot be attributed is listed and fails the assertion.
    """
    attempts = review_attempts()
    calls, other = [], []
    for name in ("first", "second", "third"):
        path = BASE / (name + "-claude-debug.txt")
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if SUBMIT_MARK not in line:
                continue
            if REVIEW_SUBMIT_TEXT not in line:
                other.append({"process": name, "at": line[:24]})
                continue
            found = DEBUG_LEASE.search(line)
            calls.append({"process": name, "at": line[:24], "epoch": iso_epoch(line[:24]),
                          "lease": found.group(1) if found else None})
    counts: dict[str, int] = {}
    for call in calls:
        lease = call["lease"]
        known = attempts.get(lease, {}) if lease else {}
        durable = ((reviews.get(lease) or {}).get("wake") or {}).get("attempts") if lease else None
        attempt, method = None, "unidentified"
        if lease and durable == 1 and len(known) <= 1:
            attempt, method = next(iter(known), None), "single-attempt"
        elif lease and call["epoch"] is not None:
            after = sorted((row["recorded_at"], aid) for aid, row in known.items()
                           if row["recorded_at"] is not None and row["recorded_at"] >= call["epoch"])
            attempt, method = (after[0][1], "record-after-submit") if after else (None, "unattributed")
        call.update(key=None if lease is None else "parallel:" + lease, attempt_id=attempt,
                    attempt_ref=None if attempt is None else attempt_ref(attempt), attribution=method)
        if method == "single-attempt":
            ref = call["attempt_ref"] or "single-attempt-ref-unobserved"
        else:
            ref = call["attempt_ref"]
        call["identity"] = json.dumps([call["key"], ref])
        if method in ("single-attempt", "record-after-submit"):
            counts[call["identity"]] = counts.get(call["identity"], 0) + 1
    return {"calls": calls, "counts": counts, "attempts": attempts, "non_review_submits": other,
            "duplicates": {key: value for key, value in counts.items() if value > 1},
            "unattributed": [call for call in calls if call["attribution"] in ("unidentified", "unattributed")]}


def stop_during_pending_prompt(raw_submits: dict, shown: dict) -> list[dict]:
    """Stop presentations of a review inside a superset of its prompt-registry
    pending window: [latest review_wake prepare request of that lease at or before
    the raw submit, that attempt's record request] (open-ended when no record).
    The registry entry is created after prepare returns and settles before record
    is sent, so this window can only over-report, never miss."""
    prepares: dict[str, list[float]] = {}
    for call in list(gate.wake_calls):
        if call.get("operation") == "prepare" and call.get("lease_id"):
            prepares.setdefault(call["lease_id"], []).append(call["at"])
    hits = []
    for call in raw_submits["calls"]:
        lease = call["lease"]
        if not lease or call["epoch"] is None:
            continue
        before = [at for at in prepares.get(lease, []) if at <= call["epoch"]]
        opened = max(before) if before else call["epoch"]
        record = (raw_submits["attempts"].get(lease, {}).get(call["attempt_id"]) or {}).get("recorded_at")
        closed = record if record is not None else float("inf")
        for stamp in (shown.get(lease) or {}).get("stop", []):
            at = iso_epoch(stamp)
            if at is not None and opened <= at <= closed:
                hits.append({"lease": lease, "identity": call["identity"], "stop_at": stamp,
                             "window": [opened, None if closed == float("inf") else closed]})
    return hits


''' + s[end:]

# 5. Acceptance wiring.
rep('''    raw_submits = raw_prompt_submits(reviews)
    shown = presentations()
    terminal_wakes = {"queued", "appended", "uncertain"}
    stuck_submitting = sorted(key for key, marks in wake_observations.items()
                              if any(m[0] == "submitting" for m in marks)
                              and not any(m[0] in terminal_wakes for m in marks))
    stop_before_own_wake = sorted(lease for lease, seen in shown.items()
                                  if seen["wake"] and any(stop and stop < max(seen["wake"]) for stop in seen["stop"]))
    result["fifo"] = {"raw_prompt_submits": raw_submits, "presentations": shown,
                      "stuck_submitting": stuck_submitting, "stop_before_own_wake": stop_before_own_wake}''',
'''    raw_submits = raw_prompt_submits(reviews)
    shown = presentations()
    review_calls = [row for row in tools if "work_parallel_item_review" in str(row.get("name"))]
    decided_leases = {(row.get("input") or {}).get("lease_id") for row in review_calls}
    terminal_wakes = {"queued", "appended", "uncertain"}
    # Durable rows are the truth; the gate's record call (accepted by the router
    # only for the live submitting attempt) is listed beside them as evidence.
    recorded_terminal = {call.get("lease_id") for call in list(gate.wake_calls)
                         if call.get("operation") == "record" and call.get("state") in terminal_wakes}
    stuck_submitting = sorted(key for key, marks in wake_observations.items()
                              if any(m[0] == "submitting" for m in marks)
                              and not any(m[0] in terminal_wakes for m in marks))
    stop_pending = stop_during_pending_prompt(raw_submits, shown)
    stop_before_own_wake = sorted(lease for lease, seen in shown.items()
                                  if seen["wake"] and any(stop and stop < max(seen["wake"]) for stop in seen["stop"]))
    result["fifo"] = {"raw_prompt_submits": raw_submits, "presentations": shown,
                      "gate_review_wake_calls": list(gate.wake_calls), "recorded_terminal": sorted(recorded_terminal),
                      "stuck_submitting": stuck_submitting, "stop_during_pending_prompt": stop_pending,
                      "diagnostic_stop_before_own_wake": stop_before_own_wake,
                      "decided_leases": sorted(x for x in decided_leases if x)}''')
rep('''                       "review_calls": [row for row in tools if "work_parallel_item_review" in str(row.get("name"))]}''',
    '''                       "review_calls": review_calls}''')
rep('''        "no_stop_data_for_pending_prompt": not stop_before_own_wake
                                           and all(len(seen["wake"]) <= 1 for seen in shown.values()),
        "no_same_identity_raw_submit_twice": not raw_submits["duplicates"] and not raw_submits["unidentified"],
        "each_pending_review_reached_main_and_decision_called": all(
            (shown.get(key) or {}).get("wake") or (shown.get(key) or {}).get("stop") for key in reviews)
            and {row.get("input", {}).get("lease_id") for row in review_calls} >= set(reviews),''',
'''        "no_stop_data_for_pending_prompt": not stop_pending
                                           and all(len(seen["wake"]) <= 1 for seen in shown.values()),
        "no_same_identity_raw_submit_twice": not raw_submits["duplicates"] and not raw_submits["unattributed"],
        "each_pending_review_reached_main_and_decision_called": len(reviews) == 5 and all(
            (shown.get(key) or {}).get("wake") or (shown.get(key) or {}).get("stop") for key in reviews)
            and decided_leases >= set(reviews),''')

p.write_text(s, encoding="utf-8", newline="\n")
print("applied")
