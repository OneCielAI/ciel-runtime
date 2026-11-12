import sys
from pathlib import Path
p=Path(sys.argv[1]); s=p.read_text(encoding='utf-8')

def rep(a,b):
 global s
 assert s.count(a)==1,a[:80]; s=s.replace(a,b)
rep('''    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "0d1b4afcff4c4d44436209d750411f36825a22486e519e7f4de89242a5d968c8",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "194fa123ca8e88ed7ddf0f12d6b1d7e5424efa827415fbaa8f1f0da7ffb721a4",''',
'''    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "eab3ad33856d9ee0840e04488f7810ec841888f782700a92ac854d931891f641",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "9b9550682831897128e5df0ce28b55a8ddcb96f6a6074d83c3d0627cc3f40d8a",''')
# Replace old flight analyzer with raw-submit identity extraction. A debug line contains lease,
# while exact attempt ref is reconstructed from finalization wake attempt_id when available.
start=s.index('def prompt_submit_flights() -> dict:')
end=s.index('def _strings(value):',start)
block='''def raw_prompt_submits(reviews: dict) -> dict:
    """Raw $.prompt.submit calls, grouped by delivery identity.

    The product identity is parallel:<lease> plus SHA-256(attempt_id)[:16].
    Distinct review identities may submit concurrently. Only a repeated raw
    submit of the same identity is a defect. Debug lines prove each raw submit;
    the finalization row supplies the exact attempt id/ref.
    """
    refs = {}
    for lease, row in reviews.items():
        attempt = (row.get("wake") or {}).get("attempt_id")
        refs[lease] = None if not attempt else hashlib.sha256(str(attempt).encode()).hexdigest()[:16]
    calls = []
    for name in ("first", "second", "third"):
        path = BASE / (name + "-claude-debug.txt")
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if SUBMIT_MARK not in line:
                continue
            found = DEBUG_LEASE.search(line)
            lease = found.group(1) if found else None
            calls.append({"process": name, "at": line[:24], "lease": lease,
                          "key": None if lease is None else "parallel:" + lease,
                          "attempt_ref": refs.get(lease)})
    counts = {}
    for call in calls:
        identity = json.dumps([call["key"], call["attempt_ref"]])
        call["identity"] = identity
        counts[identity] = counts.get(identity, 0) + 1
    return {"calls": calls, "counts": counts,
            "duplicates": {key: value for key, value in counts.items() if value > 1},
            "unidentified": [call for call in calls if call["lease"] is None]}


def _strings(value):
'''
s=s[:start]+block+s[end+len('def _strings(value):\n'):]
rep('''    flights = prompt_submit_flights()
    shown = presentations()
    stuck_submitting = sorted(key for key, marks in wake_observations.items()
                              if any(m[0] == "submitting" for m in marks)
                              and not any(m[0] in ("queued", "appended") for m in marks))
    stop_before_own_wake = sorted(lease for lease, seen in shown.items()
                                  if seen["wake"] and any(stop and stop < max(seen["wake"]) for stop in seen["stop"]))
    result["fifo"] = {"prompt_submit_flights": flights, "presentations": shown,
                      "stuck_submitting": stuck_submitting, "stop_before_own_wake": stop_before_own_wake}''',
'''    raw_submits = raw_prompt_submits(reviews)
    shown = presentations()
    terminal_wakes = {"queued", "appended", "uncertain"}
    stuck_submitting = sorted(key for key, marks in wake_observations.items()
                              if any(m[0] == "submitting" for m in marks)
                              and not any(m[0] in terminal_wakes for m in marks))
    stop_before_own_wake = sorted(lease for lease, seen in shown.items()
                                  if seen["wake"] and any(stop and stop < max(seen["wake"]) for stop in seen["stop"]))
    result["fifo"] = {"raw_prompt_submits": raw_submits, "presentations": shown,
                      "stuck_submitting": stuck_submitting, "stop_before_own_wake": stop_before_own_wake}''')
rep('''        "five_reviews_each_woken_exactly_once": len(reviews) == 5 and all(a == 1 for a in wake_attempts.values()),
        "wake_delivery_append_or_prompt": all(any(mark[0] in ("queued", "appended") for mark in
                                                  wake_observations.get(key, [])) for key in reviews),
        "no_duplicate_wake_messages": all(len(v) <= 1 for v in messages.values()),
        "no_wake_left_submitting": not stuck_submitting,
        "no_stop_data_for_pending_prompt": not stop_before_own_wake
                                           and all(len(seen["wake"]) <= 1 for seen in shown.values()),
        "at_most_one_prompt_submit_in_flight": all(row["peak_in_flight"] <= 1 for row in flights.values()),
        "main_read_tests_and_decided": all(name in tool_text for name in ("test_pass.py", "test_fail.py"))
                                       and len([r for r in reviews.values() if r.get("decision")]) == 5,''',
'''        "five_reviews_each_woken_exactly_once": len(reviews) == 5 and all(a == 1 for a in wake_attempts.values()),
        "wake_delivery_terminal": all(any(mark[0] in terminal_wakes for mark in wake_observations.get(key, []))
                                      for key in reviews),
        "no_duplicate_wake_messages": all(len(v) <= 1 for v in messages.values()),
        "no_wake_left_submitting": not stuck_submitting,
        "no_stop_data_for_pending_prompt": not stop_before_own_wake
                                           and all(len(seen["wake"]) <= 1 for seen in shown.values()),
        "no_same_identity_raw_submit_twice": not raw_submits["duplicates"] and not raw_submits["unidentified"],
        "each_pending_review_reached_main_and_decision_called": all(
            (shown.get(key) or {}).get("wake") or (shown.get(key) or {}).get("stop") for key in reviews)
            and {row.get("input", {}).get("lease_id") for row in review_calls} >= set(reviews),
        "main_read_tests_and_decided": all(name in tool_text for name in ("test_pass.py", "test_fail.py"))
                                       and len([r for r in reviews.values() if r.get("decision")]) == 5,''')
# Explicit restart duplicate-row check.
rep('''        "checkpoint_a_completion_before_review": a_all_submitted
            and all(v["decision"] is None and (v["wake"] or {}).get("attempts") == 0
                    for v in result["checkpoint_a"]["reviews_at_kill"].values())
            and result["checkpoints"]["a_kill"]["proc_exited"],''',
'''        "checkpoint_a_completion_before_review": a_all_submitted
            and all(v["decision"] is None and (v["wake"] or {}).get("attempts") == 0
                    for v in result["checkpoint_a"]["reviews_at_kill"].values())
            and result["checkpoints"]["a_kill"]["proc_exited"],
        "checkpoint_a_converged_no_duplicate_review_rows": len({row.get("lease_id") for row in reviews.values()}) == len(reviews)
            and set(result["checkpoint_a"]["reviews_at_kill"]).issubset(reviews),''')
p.write_text(s,encoding='utf-8',newline='\n')
print('applied')
