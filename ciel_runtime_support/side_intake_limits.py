"""Shared capacity contract for the original text of one side-intake event."""

import json

SIDE_INTAKE_TEXT_MAX_CHARS = 32_000
SIDE_INTAKE_PENDING_RECORD_MAX_BYTES = 256 * 1024
# Room a pending record keeps beside its text for fields written before and
# during processing: event key (300 characters, up to 6 escaped bytes each),
# digest, an 8 KiB reply route, session identity, the claim lease, the context
# preview (6,000 characters, up to 24 KB in UTF-8), the bounded side failure
# and main-fallback records, and field names. Held separate so the gateway can
# decide before queueing; 32,000 escaped characters (192,000 bytes) still fit.
SIDE_INTAKE_RECORD_OVERHEAD_BYTES = 64 * 1024


def side_text_fits_pending_record(text: str) -> bool:
    """Whether ``text`` can be stored unchanged in one pending side record."""
    encoded = len(json.dumps(text, ensure_ascii=False).encode('utf-8'))
    return encoded <= SIDE_INTAKE_PENDING_RECORD_MAX_BYTES - SIDE_INTAKE_RECORD_OVERHEAD_BYTES
