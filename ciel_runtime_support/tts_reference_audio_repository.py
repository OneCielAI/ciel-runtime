"""Private binary sidecars for configured TTS reference audio."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext


REFERENCE_MARKER_PREFIX = "ciel-tts-reference:v1:"
_MARKER_PATTERN = re.compile(
    rf"{re.escape(REFERENCE_MARKER_PREFIX)}([a-f0-9]{{48}})\Z"
)
_MAGIC = b"CIELTTSREF1\0"
_MAX_MEDIA_DESCRIPTOR_BYTES = 255


def _maximum_base64_characters(decoded_bytes: int) -> int:
    return 4 * ((max(0, int(decoded_bytes)) + 2) // 3)


class TtsReferenceAudioRepository:
    """Store one configured reference as an opaque marker plus private bytes."""

    def __init__(
        self,
        root: Path,
        *,
        token: Callable[[], str] | None = None,
        process_id: Callable[[], int] = os.getpid,
        clock_ns: Callable[[], int] = time.time_ns,
        transformed_admission: Callable[
            [str, int, int, str], AbstractContextManager[None]
        ] | None = None,
    ) -> None:
        self.root = root
        self._token = token or (lambda: secrets.token_hex(24))
        self._process_id = process_id
        self._clock_ns = clock_ns
        self._transformed_admission = transformed_admission

    @staticmethod
    def is_marker(value: object) -> bool:
        return _MARKER_PATTERN.fullmatch(str(value or "").strip()) is not None

    def store_data_url(self, value: object, maximum_bytes: int) -> str:
        """Decode, validate, and atomically persist an audio data URL."""
        media_descriptor, encoded = self._split_data_url(value)
        maximum = max(0, int(maximum_bytes))
        if len(encoded) > _maximum_base64_characters(maximum):
            raise OverflowError(
                f"TTS reference audio exceeds {maximum} decoded bytes"
            )
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid base64 TTS reference audio") from exc
        if not audio:
            raise ValueError("TTS reference audio must not be empty")
        if len(audio) > maximum:
            raise OverflowError(
                f"TTS reference audio exceeds {maximum} decoded bytes"
            )

        descriptor = media_descriptor.encode("ascii")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._chmod(self.root, 0o700)
        token = self._unused_token()
        marker = REFERENCE_MARKER_PREFIX + token
        target = self._path(token)
        temporary = self.root / (
            f".{token}.{self._process_id()}.{self._clock_ns()}.tmp"
        )
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            descriptor_id = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor_id, "wb") as stream:
                stream.write(_MAGIC)
                stream.write(len(descriptor).to_bytes(2, "big"))
                stream.write(descriptor)
                stream.write(audio)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            self._chmod(temporary, 0o600)
            os.replace(temporary, target)
            self._chmod(target, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return marker

    def data_url_wire_length(self, value: object, maximum_bytes: int) -> int:
        """Validate a data-URL envelope without allocating decoded media."""
        _descriptor, encoded = self._split_data_url(value)
        maximum = max(0, int(maximum_bytes))
        if len(encoded) > _maximum_base64_characters(maximum):
            raise OverflowError(
                f"TTS reference audio exceeds {maximum} decoded bytes"
            )
        if not encoded or not encoded.isascii():
            raise ValueError("invalid base64 TTS reference audio")
        return len(str(value).strip())

    def expand_data_url(self, value: object, maximum_bytes: int) -> str:
        """Expand a local marker only at the TTS forwarding boundary."""
        text = str(value or "").strip()
        match = _MARKER_PATTERN.fullmatch(text)
        if match is None:
            return text
        target = self._path(match.group(1))
        try:
            descriptor, audio = self._read(target, max(0, int(maximum_bytes)))
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError(
                "saved TTS reference audio is unavailable; upload it again"
            ) from exc
        return (
            f"data:{descriptor};base64,"
            + base64.b64encode(audio).decode("ascii")
        )

    @contextmanager
    def materialize_data_url(
        self,
        value: object,
        maximum_bytes: int,
        *,
        path: str,
        original_length: int,
        transformed_length: Callable[[int], int],
        content_type: str = "application/json",
    ) -> Iterator[str]:
        """Reserve transformed memory before reading and encoding a sidecar."""
        text = str(value or "").strip()
        match = _MARKER_PATTERN.fullmatch(text)
        if match is None:
            yield text
            return
        maximum = max(0, int(maximum_bytes))
        opened_context = self._opened(self._path(match.group(1)), maximum)
        try:
            stream, descriptor, audio_size = opened_context.__enter__()
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError(
                "saved TTS reference audio is unavailable; upload it again"
            ) from exc
        try:
            data_url_length = (
                len("data:")
                + len(descriptor)
                + len(";base64,")
                + _maximum_base64_characters(audio_size)
            )
            final_length = int(transformed_length(data_url_length))
            with self.admit_transformed(
                path,
                original_length,
                final_length,
                content_type,
            ):
                try:
                    audio = stream.read(audio_size)
                    if len(audio) != audio_size or stream.read(1):
                        raise ValueError("invalid TTS reference audio sidecar")
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        "saved TTS reference audio is unavailable; upload it again"
                    ) from exc
                materialized = (
                    f"data:{descriptor};base64,"
                    + base64.b64encode(audio).decode("ascii")
                )
                yield materialized
        finally:
            opened_context.__exit__(None, None, None)

    def discard(self, value: object) -> bool:
        """Remove the sidecar named by a valid marker without following paths."""
        match = _MARKER_PATTERN.fullmatch(str(value or "").strip())
        if match is None:
            return False
        try:
            self._path(match.group(1)).unlink()
            return True
        except FileNotFoundError:
            return False

    def admit_transformed(
        self,
        path: str,
        original_length: int,
        transformed_length: int,
        content_type: str,
    ) -> AbstractContextManager[None]:
        if self._transformed_admission is None:
            return nullcontext()
        return self._transformed_admission(
            path,
            original_length,
            transformed_length,
            content_type,
        )

    def _unused_token(self) -> str:
        for _attempt in range(16):
            token = str(self._token()).strip().casefold()
            if re.fullmatch(r"[a-f0-9]{48}", token) and not self._path(token).exists():
                return token
        raise RuntimeError("could not allocate a TTS reference audio sidecar")

    def _path(self, token: str) -> Path:
        return self.root / f"{token}.bin"

    @staticmethod
    def _split_data_url(value: object) -> tuple[str, str]:
        text = str(value or "").strip()
        head, separator, encoded = text.partition(",")
        if (
            not separator
            or not head.casefold().startswith("data:audio/")
            or not head.casefold().endswith(";base64")
        ):
            raise ValueError("TTS reference audio must be an audio data URL")
        descriptor = head[5:-7]
        try:
            raw_descriptor = descriptor.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("TTS reference audio has an invalid media type") from exc
        if (
            not raw_descriptor
            or len(raw_descriptor) > _MAX_MEDIA_DESCRIPTOR_BYTES
            or any(
                byte < 33 or byte > 126 or byte in b',"\\'
                for byte in raw_descriptor
            )
        ):
            raise ValueError("TTS reference audio has an invalid media type")
        return descriptor, encoded

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError:
            # Windows and some mounted filesystems do not expose POSIX modes.
            pass

    @staticmethod
    def _read(path: Path, maximum: int) -> tuple[str, bytes]:
        with TtsReferenceAudioRepository._opened(path, maximum) as opened:
            stream, descriptor, audio_size = opened
            audio = stream.read(audio_size)
            if len(audio) != audio_size or stream.read(1):
                raise ValueError("invalid TTS reference audio sidecar")
        return descriptor, audio

    @staticmethod
    @contextmanager
    def _opened(path: Path, maximum: int) -> Iterator[tuple[object, str, int]]:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow:
            flags |= nofollow
        descriptor_id: int | None = None
        try:
            descriptor_id = os.open(path, flags)
            size = os.fstat(descriptor_id).st_size
            minimum_overhead = len(_MAGIC) + 2 + 1
            if size < minimum_overhead or size > maximum + len(_MAGIC) + 2 + _MAX_MEDIA_DESCRIPTOR_BYTES:
                raise OverflowError(
                    f"TTS reference audio exceeds {maximum} decoded bytes"
                )
            with os.fdopen(descriptor_id, "rb") as stream:
                descriptor_id = None
                if stream.read(len(_MAGIC)) != _MAGIC:
                    raise ValueError("invalid TTS reference audio sidecar")
                length_bytes = stream.read(2)
                if len(length_bytes) != 2:
                    raise ValueError("invalid TTS reference audio sidecar")
                descriptor_length = int.from_bytes(length_bytes, "big")
                if not 0 < descriptor_length <= _MAX_MEDIA_DESCRIPTOR_BYTES:
                    raise ValueError("invalid TTS reference audio sidecar")
                descriptor = stream.read(descriptor_length).decode("ascii")
                if not descriptor.casefold().startswith("audio/"):
                    raise ValueError("invalid TTS reference audio sidecar")
                audio_size = size - len(_MAGIC) - 2 - descriptor_length
                if not 0 < audio_size <= maximum:
                    raise OverflowError(
                        f"TTS reference audio exceeds {maximum} decoded bytes"
                    )
                yield stream, descriptor, audio_size
        finally:
            if descriptor_id is not None:
                os.close(descriptor_id)


__all__ = [
    "REFERENCE_MARKER_PREFIX",
    "TtsReferenceAudioRepository",
]
