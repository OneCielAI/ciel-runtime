"""Pure buffering policy for word-boundary streaming."""

from __future__ import annotations


def split_word_buffer(
    buffer: str, *, force: bool = False, max_buffer: int = 64
) -> tuple[str, str]:
    if not buffer:
        return "", ""
    if force:
        return buffer, ""
    for index in range(len(buffer) - 1, -1, -1):
        if buffer[index].isspace():
            return buffer[: index + 1], buffer[index + 1 :]
    if len(buffer) >= max_buffer:
        return buffer, ""
    return "", buffer
