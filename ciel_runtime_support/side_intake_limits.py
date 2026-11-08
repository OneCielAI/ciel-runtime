"""Shared capacity contract for the original text of one side-intake event."""

import json

SIDE_INTAKE_TEXT_MAX_CHARS = 32_000
SIDE_INTAKE_PENDING_RECORD_MAX_BYTES = 256 * 1024
# Room a pending record keeps beside its text: event key (300 characters, at
# most 6 escaped bytes each), digest, an 8 KiB reply route, session identity
# and field names. Held separate so the gateway can decide before queueing.
SIDE_INTAKE_RECORD_OVERHEAD_BYTES = 16 * 1024


def side_text_fits_pending_record(text: str) -> bool:
    """Whether ``text`` can be stored unchanged in one pending side record."""
    encoded = len(json.dumps(text, ensure_ascii=False).encode('utf-8'))
    return encoded <= SIDE_INTAKE_PENDING_RECORD_MAX_BYTES - SIDE_INTAKE_RECORD_OVERHEAD_BYTES
