"""Atomic session-local side intake. Receipts share the Work transaction."""
import hashlib
import time
from . import work_plan_tree as tree


def enqueue(store, text, event_key):
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise tree.WorkPlanError("side intake text must contain 1-16000 characters")
    if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 300:
        raise tree.WorkPlanError("stable side intake event key required (1-300 characters)")
    digest = hashlib.sha256(text.encode()).hexdigest()
    key = hashlib.sha256(event_key.encode()).hexdigest()

    def apply(doc):
        receipts = doc.setdefault("side_intake_receipts", {})
        if key in receipts:
            receipt = receipts[key]
            if receipt["digest"] != digest:
                raise tree.WorkPlanError("side intake event key reused with different content")
            return {**receipt, "duplicate": True}
        before = doc.get("active_root")
        root = tree.add_node(doc, None, "External request: " + " ".join(text.split())[:220],
                             note="Untrusted external request; review and plan before execution. " + text,
                             ref="side:" + key[:40])
        # Intake never interrupts or approves the current goal.
        if before is not None:
            tree._activate(doc, before)
        receipt = {"item_id": root, "digest": digest, "text": text, "recap": " ".join(text.split())[:500]}
        receipts[key] = receipt
        return {**receipt, "duplicate": False}
    for attempt in range(5):
        try:
            doc, receipt = store.transaction(apply)
            break
        except PermissionError:
            # A concurrent first writer can lock the byte while another opens
            # the empty Windows sidecar. Retry the whole atomic/dedup operation.
            if attempt == 4:
                raise
            time.sleep(0.05)
    return {key: value for key, value in {**receipt, "version": doc["version"], "accepted": True,
                                          "execution_approved": False}.items() if key != "text"}
