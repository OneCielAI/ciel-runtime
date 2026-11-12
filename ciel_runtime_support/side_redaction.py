"""Credential-shape redaction for bounded side evidence and code reads."""
import re

_SECRET = re.compile(r'(?i)(bearer\s+|sk-|gh[pousr]_|AKIA|(?:api[_-]?key|token|secret|password|credential|cookie)["\s:=]+)[A-Za-z0-9._~+/=-]{8,}')


def redact_text(value):
    """Hide common credential shapes in bounded side evidence."""
    return _SECRET.sub(r'\1‹hidden›', str(value))
