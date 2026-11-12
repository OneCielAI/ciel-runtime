"""Third pass: after the replacement is finalized, the final terminal status and
the web Main review (1440 and 390) show 0 rows for the reworked item."""
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:100])
    s = s.replace(a, b)


rep('''def wake_messages() -> dict[str, list[str]]:''',
'''# register.js parallelStatusText: MAIN REVIEW (n) follows the Policy line only
# when a review is listed; the fork groups (or the dispatch/settings footer) end it.
TERM_GROUP = re.compile(r"^\\s*(?:(?:CONFIRMED|WAITING|RESERVATION|UNCERTAIN|VALIDATING|ENDED|LEGACY LEASES) \\(\\d+\\)"
                        r"|Pending native Agent dispatch|Settings:)")


def terminal_main_review(text: str, item: str) -> dict:
    """Main review rows for one item in the last visible terminal status block.
    Not visible (fails) unless that block's header and Policy line are on screen,
    so a scrolled-off MAIN REVIEW line can never read as empty."""
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines) if "Ciel Work parallel" in line]
    if not heads:
        return {"visible": False}
    policy = next((i for i in range(heads[-1] + 1, len(lines)) if lines[i].strip().startswith("Policy:")), None)
    if policy is None:
        return {"visible": False}
    end = next((i for i in range(policy + 1, len(lines)) if TERM_GROUP.search(lines[i])), len(lines))
    start = next((i for i in range(policy + 1, end) if "MAIN REVIEW (" in lines[i]), None)
    if start is None:
        return {"visible": True, "header_count": 0, "item_rows": []}
    found = re.search(r"MAIN REVIEW \\((\\d+)\\)", lines[start])
    rows = [line.strip() for line in lines[start + 1:end] if re.search(r"·\\s+" + re.escape(item) + r"\\b", line)]
    return {"visible": True, "header_count": int(found.group(1)) if found else None, "item_rows": rows}


def web_main_review(text: str, item: str) -> dict:
    """Main review rows for one item in the web Parallel tab text (section up to
    'Processing now'). A row header reads '<item> · <title>'."""
    at = text.find("Main review (")
    if at < 0:
        return {"visible": False}
    end = text.find("Processing now", at)
    section = text[at:end if end > 0 else None]
    found = re.match(r"Main review \\((\\d+)\\)", section)
    rows = [line.strip() for line in section.splitlines() if re.match(r"\\s*" + re.escape(item) + r"\\s+·", line)]
    return {"visible": True, "header_count": int(found.group(1)) if found else None, "item_rows": rows}


def wake_messages() -> dict[str, list[str]]:''')

rep('''    absent_rows = [row for row in audit if row.get("kind") == "absent"]
    result["acceptance"] = {''',
'''    absent_rows = [row for row in audit if row.get("kind") == "absent"]
    final_web = result["screenshots"].get("final_web") or {}
    final_terminal = (OUT / "07-finalized.txt").read_text(encoding="utf-8", errors="replace") \\
        if (OUT / "07-finalized.txt").exists() else ""
    result["final"]["main_review_after_finalize"] = {
        "item": items[FAIL_TITLE],
        "terminal": terminal_main_review(final_terminal, items[FAIL_TITLE]),
        "web": {mode: web_main_review(str((final_web.get(mode) or {}).get("text") or ""), items[FAIL_TITLE])
                for mode in ("desktop", "mobile")}}
    after = result["final"]["main_review_after_finalize"]
    result["acceptance"] = {''')

rep('''        "replacement_second_review_approved_finalized": second_review.get("state") == "finalized",''',
'''        "replacement_second_review_approved_finalized": second_review.get("state") == "finalized",
        "reworked_item_absent_from_final_main_review": after["terminal"].get("visible") is True
            and not after["terminal"].get("item_rows")
            and all(row.get("visible") is True and not row.get("item_rows") for row in after["web"].values()),''')

p.write_text(s, encoding="utf-8", newline="\n")
print("applied")
