"""CSV ledger reconciliation (problem C).

``reconcile(chunks)`` accepts an iterable of arbitrary string fragments of a
single RFC 4180 CSV document with header ``id,account,amount`` and returns a
mapping ``account -> Decimal`` total.

Rules:
* Fragment boundaries are irrelevant (they may split CRLF pairs, quotes or
  quoted fields); the fragments are concatenated before parsing.
* Blank rows are ignored. Empty input returns ``{}``.
* Every row is validated: id and account must be non-empty, amount must be a
  finite decimal literal (NaN/Infinity rejected). Any violation -> ValueError.
* Duplicate ids: the LAST row wins globally. Earlier rows for that id do not
  contribute, even if they named a different account. Accounts that appear in
  any row are kept, even when their final total is zero.
* Arithmetic uses exact Decimal sums; floats are never used.
"""

import csv
import decimal
import io
import re

HEADER = ["id", "account", "amount"]

_AMOUNT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

_EXACT = decimal.Context(
    prec=decimal.MAX_PREC,
    Emax=decimal.MAX_EMAX,
    Emin=decimal.MIN_EMIN,
    traps=[decimal.Inexact, decimal.Rounded, decimal.InvalidOperation, decimal.Overflow],
)


def _parse_amount(text, row_no):
    if not _AMOUNT_RE.match(text):
        raise ValueError(f"row {row_no}: invalid amount {text!r}")
    return decimal.Decimal(text)


def _rows(text):
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        for row in reader:
            yield reader.line_num, row
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from None


def reconcile(chunks):
    parts = []
    for chunk in chunks:
        if not isinstance(chunk, str):
            raise TypeError(f"chunks must be str, got {type(chunk).__name__}")
        parts.append(chunk)
    text = "".join(parts)

    header_seen = False
    latest = {}  # id -> (account, amount); dict order irrelevant to result
    accounts = []
    for row_no, row in _rows(text):
        if not row:
            continue  # blank line
        if not header_seen:
            if row != HEADER:
                raise ValueError(f"row {row_no}: expected header {HEADER}, got {row}")
            header_seen = True
            continue
        if len(row) != 3:
            raise ValueError(f"row {row_no}: expected 3 fields, got {len(row)}")
        row_id, account, amount_text = row
        if not row_id.strip():
            raise ValueError(f"row {row_no}: missing id")
        if not account.strip():
            raise ValueError(f"row {row_no}: missing account")
        amount = _parse_amount(amount_text, row_no)
        latest[row_id] = (account, amount)
        accounts.append(account)

    totals = {account: decimal.Decimal(0) for account in accounts}
    with decimal.localcontext(_EXACT):
        for account, amount in latest.values():
            totals[account] = totals[account] + amount
    return totals
