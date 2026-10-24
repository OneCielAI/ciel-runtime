"""Structured image payloads inside model request bodies.

Encoded image bytes are wire content, not text. Counting a base64 body as
ASCII made a Claude checkpoint holding 55 screenshot tool results look about
3.2 times larger than the provider-reported usage (Regina, 2026-10-24: 2.36M
estimated against 0.73M reported), and pasting those bytes into a textual
map prompt gives a text summarizer data it cannot read.

Only recognized image blocks are touched: Anthropic ``image`` blocks with a
base64 source (including those nested in ``tool_result`` content), Responses
``input_image`` data URLs, and Ollama message ``images`` lists. Any other
string, including one under a ``data`` key, is left as it is. The visual
token cost of an image is provider specific and stays unknown here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MediaObservation:
    images: int = 0
    encoded_bytes: int = 0

    def add(self, other: "MediaObservation") -> "MediaObservation":
        return MediaObservation(self.images + other.images, self.encoded_bytes + other.encoded_bytes)


def _marker(media_type: str, data: str) -> str:
    digest = hashlib.sha256(data.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return (f"[ciel-runtime omitted image bytes: media_type={media_type or 'unknown'} "
            f"encoded_bytes={len(data)} sha256={digest}; the image is not visible in this text]")


def _image_block(value: dict[str, Any]) -> tuple[dict[str, Any], MediaObservation] | None:
    kind = value.get("type")
    if kind == "image":
        source = value.get("source")
        if (isinstance(source, dict) and source.get("type") == "base64"
                and isinstance(source.get("data"), str)):
            data = source["data"]
            media = str(source.get("media_type") or "")
            return ({**value, "source": {**source, "data": _marker(media, data)}},
                    MediaObservation(1, len(data)))
        return None
    if kind == "input_image":
        url = value.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        if isinstance(url, str) and url.startswith("data:") and "," in url:
            header, data = url.split(",", 1)
            media = header[5:].split(";", 1)[0]
            if not media.casefold().startswith("image/") or ";base64" not in header.casefold():
                return None
            replaced = {**value}
            marker = f"data:{media};marker,{_marker(media, data)}"
            replaced["image_url"] = ({**value["image_url"], "url": marker}
                                     if isinstance(value.get("image_url"), dict) else marker)
            return replaced, MediaObservation(1, len(data))
    return None


def _content(value: Any) -> tuple[Any, MediaObservation]:
    if isinstance(value, list):
        observed = MediaObservation()
        items = []
        changed = False
        for item in value:
            projected, seen = _content(item)
            changed = changed or projected is not item
            observed = observed.add(seen)
            items.append(projected)
        return (items if changed else value), observed
    if not isinstance(value, dict):
        return value, MediaObservation()
    block = _image_block(value)
    if block is not None:
        return block
    # Descend only through protocol content containers, never tool arguments,
    # schemas or arbitrary result objects which may describe an image as data.
    if value.get("type") == "tool_result" and isinstance(value.get("content"), list):
        projected, seen = _content(value["content"])
        return ({**value, "content": projected} if projected is not value["content"] else value), seen
    return value, MediaObservation()


def opaque_media(value: Any) -> tuple[Any, MediaObservation]:
    """Copy recognized request/message content, not arbitrary nested objects.

    Original wire data is never mutated. Unknown structures remain unchanged.
    """
    if isinstance(value, list):
        observed = MediaObservation()
        items = []
        changed = False
        for item in value:
            projected, seen = opaque_media(item)
            changed = changed or projected is not item
            observed = observed.add(seen)
            items.append(projected)
        return (items if changed else value), observed
    if not isinstance(value, dict):
        return value, MediaObservation()
    result = value
    observed = MediaObservation()
    if "role" in value or value.get("type") == "message":
        if "content" in value:
            content, seen = _content(value["content"])
            observed = observed.add(seen)
            if content is not value["content"]:
                result = {**result, "content": content}
        images = value.get("images")
        if isinstance(images, list) and images and all(isinstance(image, str) for image in images):
            result = {**result, "images": [_marker("", image) for image in images]}
            observed = observed.add(MediaObservation(len(images), sum(len(image) for image in images)))
    for key in ("messages", "input"):
        if isinstance(value.get(key), list):
            content, seen = opaque_media(value[key])
            observed = observed.add(seen)
            if content is not value[key]:
                result = {**result, key: content}
    return result, observed


__all__ = ["MediaObservation", "opaque_media"]
