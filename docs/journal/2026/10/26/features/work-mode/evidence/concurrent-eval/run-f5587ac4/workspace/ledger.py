"""Reconcile fragmented CSV with exact totals and global last-id-wins rows."""

import csv
from decimal import Decimal, InvalidOperation
from io import StringIO


def _exact_add(left, right):
    """Add finite decimals without rounding by the ambient Decimal context."""
    def parts(value):
        sign, digits, exponent = value.as_tuple()
        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit
        return -coefficient if sign else coefficient, exponent

    a, ae = parts(left)
    b, be = parts(right)
    exponent = min(ae, be)
    coefficient = a * 10 ** (ae - exponent) + b * 10 ** (be - exponent)
    digits = Decimal(abs(coefficient)).as_tuple().digits
    return Decimal((int(coefficient < 0), digits, exponent))


def reconcile(chunks):
    # Joining before parsing makes fragment boundaries irrelevant, including
    # boundaries inside doubled quotes or CRLF. newline='' preserves quoted CRLF.
    reader = csv.reader(StringIO(''.join(chunks), newline=''), strict=True)
    latest = {}
    accounts = {}
    try:
        header = next((row for row in reader if row), None)
        if header is None:
            return {}
        if header != ['id', 'account', 'amount']:
            raise ValueError("expected header id,account,amount")
        for row in reader:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("each row must contain id,account,amount")
            row_id, account, raw_amount = row
            if not row_id or not account:
                raise ValueError("id and account must not be empty")
            try:
                amount = Decimal(raw_amount)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid amount: {raw_amount!r}") from exc
            if not amount.is_finite():
                raise ValueError("amount must be finite")
            # Validate before replacing, even if another row later overrides it.
            latest[row_id] = (account, amount)
            accounts.setdefault(account, Decimal(0))
    except csv.Error as exc:
        raise ValueError(f"invalid CSV: {exc}") from exc

    for account, amount in latest.values():
        accounts[account] = _exact_add(accounts[account], amount)
    return accounts
