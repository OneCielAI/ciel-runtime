"""Synthetic Walkie-shaped rows mixing Hangul, BMP symbols and astral emoji.

Shared by the stdio and HTTP fixtures of the owned MCP size probe so both
transports serve byte-identical text. No real data.
"""
import hashlib
import json

CONVERSATION = '34886995-4a66-4527-a618-814e9cb0a5bf'
# Hangul (BMP), BMP symbols, and astral emoji (UTF-16 surrogate pairs), plus a
# ZWJ sequence.
LINE = '워키 스레드 {n} 행 ★→€✓ 😀🚀👩‍💻 '


def payload(tool, size, *, ascii_only=False):
    rows, total, number = [], 2, 1
    while total < size:
        row = {'id': str(1000 + number), 'conversation_id': CONVERSATION,
               'thread_root_id': '1000' if number > 1 else None,
               'text': (('synthetic %s row %d ' % (tool, number)) * 100 if ascii_only
                        else LINE.format(n=number) * 60)}
        total += len(json.dumps(row, ensure_ascii=ascii_only)) + 1
        rows.append(row)
        number += 1
    return json.dumps(rows if tool == 'walkie_call_tool' else {'messages': rows}, ensure_ascii=ascii_only)


def stats(text):
    return {'chars': len(text), 'code_points': len(text),
            'utf16_units': len(text.encode('utf-16-le')) // 2,
            'utf8_bytes': len(text.encode('utf-8')),
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
