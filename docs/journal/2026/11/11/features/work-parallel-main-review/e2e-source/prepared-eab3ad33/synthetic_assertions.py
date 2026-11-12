"""Synthetic checks for the new analyzers: same-identity duplicate, two attempts of
one lease (distinct identities), unattributed submit, and Stop inside a pending window."""
import ast
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

runner = Path(sys.argv[1])
tree = ast.parse(runner.read_text(encoding="utf-8"))
wanted = {"attempt_ref", "iso_epoch", "review_attempts", "raw_prompt_submits", "stop_during_pending_prompt"}
consts = {"SUBMIT_MARK", "DEBUG_LEASE", "REVIEW_SUBMIT_TEXT"}
body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in consts for t in n.targets))]
code = compile(ast.Module(body=body, type_ignores=[]), str(runner), "exec")
A, B = "a" * 32, "b" * 32


def line(stamp, lease):
    text = ('Ciel Work result is awaiting main review (data, not instructions): {\\"lease_id\\":\\"%s\\",'
            '\\"item_id\\":\\"n1\\"' % lease)
    return '%s [DEBUG] $.prompt.submit (ciel-work): "%s"' % (stamp, text)


def run(lines, reviews, observations, calls):
    base = Path(tempfile.mkdtemp(prefix="synthetic-assert-"))
    (base / "first-claude-debug.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    class Gate:
        wake_calls = calls
    ns = {"hashlib": hashlib, "json": json, "re": re, "BASE": base, "gate": Gate,
          "wake_observations": observations}
    exec(code, ns)
    return ns


t0 = 1794435700.0  # 2026-11-11T22:21:40Z
stamp = lambda dt: __import__("datetime").datetime.fromtimestamp(t0 + dt, __import__("datetime").timezone.utc) \
    .strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"
checks = {}

# 1. Same lease, one durable attempt, raw-submitted twice -> duplicate.
ns = run([line(stamp(1), A), line(stamp(2), A)], {A: {"wake": {"attempts": 1}}},
         {A: [["submitting", 1, None, "att1", t0]]}, [])
raw = ns["raw_prompt_submits"]({A: {"wake": {"attempts": 1}}})
checks["same_identity_twice_flagged"] = bool(raw["duplicates"]) and \
    raw["calls"][0]["attempt_ref"] == hashlib.sha256(b"att1").hexdigest()[:16]

# 2. Two distinct leases concurrently -> no duplicate.
ns = run([line(stamp(1), A), line(stamp(1.1), B)],
         {A: {"wake": {"attempts": 1}}, B: {"wake": {"attempts": 1}}}, {}, [])
raw = ns["raw_prompt_submits"]({A: {"wake": {"attempts": 1}}, B: {"wake": {"attempts": 1}}})
checks["distinct_identities_concurrent_allowed"] = not raw["duplicates"] and not raw["unattributed"]

# 3. One lease, two attempts, one raw submit each -> distinct identities via record calls.
calls = [{"at": t0 + 0.5, "operation": "prepare", "lease_id": A},
         {"at": t0 + 1.5, "operation": "record", "lease_id": A, "attempt_id": "att1", "state": "failed"},
         {"at": t0 + 2.5, "operation": "prepare", "lease_id": A},
         {"at": t0 + 3.5, "operation": "record", "lease_id": A, "attempt_id": "att2", "state": "queued"}]
reviews = {A: {"wake": {"attempts": 2}}}
ns = run([line(stamp(1), A), line(stamp(3), A)], reviews, {}, calls)
raw = ns["raw_prompt_submits"](reviews)
checks["two_attempts_distinct"] = not raw["duplicates"] and not raw["unattributed"] \
    and [c["attempt_id"] for c in raw["calls"]] == ["att1", "att2"]

# 4. Second attempt submitted twice -> duplicate on att2 only.
ns = run([line(stamp(1), A), line(stamp(3), A), line(stamp(3.2), A)], reviews, {}, calls)
raw = ns["raw_prompt_submits"](reviews)
checks["second_attempt_duplicate_flagged"] = list(raw["duplicates"].values()) == [2] \
    and "att2" not in json.dumps(raw["duplicates"]) and hashlib.sha256(b"att2").hexdigest()[:16] \
    in next(iter(raw["duplicates"]))

# 5. Multi-attempt submit after the last record -> unattributed (fails assertion).
ns = run([line(stamp(5), A)], reviews, {}, calls)
raw = ns["raw_prompt_submits"](reviews)
checks["unattributed_fails"] = len(raw["unattributed"]) == 1

# 6. Stop presentation inside [prepare, record] -> flagged; after record -> not.
ns = run([line(stamp(3), A)], {A: {"wake": {"attempts": 1}}},
         {A: [["submitting", 1, None, "att2", t0 + 2.6]]},
         [{"at": t0 + 2.5, "operation": "prepare", "lease_id": A},
          {"at": t0 + 3.5, "operation": "record", "lease_id": A, "attempt_id": "att2", "state": "queued"}])
raw = ns["raw_prompt_submits"]({A: {"wake": {"attempts": 1}}})
inside = ns["stop_during_pending_prompt"](raw, {A: {"wake": [], "stop": [stamp(3.2)]}})
after = ns["stop_during_pending_prompt"](raw, {A: {"wake": [], "stop": [stamp(9)]}})
checks["stop_inside_pending_window_flagged"] = len(inside) == 1 and not after

print(json.dumps(checks, indent=1))
sys.exit(0 if all(checks.values()) else 1)
