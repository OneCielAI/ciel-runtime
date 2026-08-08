"""Stop runaway repetition loops in model output.

A model can fall into a degenerate sampling loop and emit the same block of
text back to back until the request budget runs out.  ciel-runtime cannot fix
that upstream: for the providers where this was reported no anti-repetition
sampling parameter is even in play.  DeepSeek documents sampling overrides as
ineffective while thinking is enabled, so the adapter deliberately drops them,
and Ollama's Go sampler has a documented history of accepting
``repeat_penalty``/``frequency_penalty``/``presence_penalty`` and then ignoring
them (ollama/ollama#15783).

So this guard does not reason about *why* the model looped.  It watches the one
thing that is directly observable -- the emitted characters -- and reports that
the tail of the output is literally the same block repeated beyond a budget.
Detection is exact: no similarity scoring, no semantic judgement, no sampling.

Defaults are deliberately conservative.  A verdict needs both a minimum number
of consecutive identical repeats *and* a minimum repeated length, so ordinary
output that happens to contain duplicated lines never trips it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_PROBE_CHARS = 32
DEFAULT_MIN_REPEATS = 10
DEFAULT_MIN_REPEATED_CHARS = 2000
DEFAULT_MAX_PERIOD_CHARS = 4096
DEFAULT_CHECK_INTERVAL_CHARS = 256
DEFAULT_DENSE_PROBE_CHARS = 64
DEFAULT_DENSE_PROBE_SAMPLES = 24
DEFAULT_DENSE_WINDOW_CHARS = 8000
DEFAULT_MIN_DENSITY_PERCENT = 70

_DISABLED_VALUES = {"0", "off", "false", "no", "disable", "disabled"}

CONSECUTIVE = "consecutive"
INTERLEAVED = "interleaved"


@dataclass(frozen=True, slots=True)
class RunawayOutputPolicy:
    """Thresholds a repeated tail must clear before the turn is cut short."""

    enabled: bool = True
    probe_chars: int = DEFAULT_PROBE_CHARS
    min_repeats: int = DEFAULT_MIN_REPEATS
    min_repeated_chars: int = DEFAULT_MIN_REPEATED_CHARS
    max_period_chars: int = DEFAULT_MAX_PERIOD_CHARS
    check_interval_chars: int = DEFAULT_CHECK_INTERVAL_CHARS
    dense_probe_chars: int = DEFAULT_DENSE_PROBE_CHARS
    dense_probe_samples: int = DEFAULT_DENSE_PROBE_SAMPLES
    dense_window_chars: int = DEFAULT_DENSE_WINDOW_CHARS
    min_density_percent: int = DEFAULT_MIN_DENSITY_PERCENT

    def tail_budget(self) -> int:
        """Characters worth retaining to still measure ``min_repeats`` repeats."""

        return self.max_period_chars * (self.min_repeats + 1) + self.probe_chars


@dataclass(frozen=True, slots=True)
class RunawayVerdict:
    """An exact, reproducible statement about the repeated tail."""

    period_chars: int
    repeats: int
    repeated_chars: int
    unit_preview: str
    kind: str = CONSECUTIVE
    span_chars: int = 0

    def spanned_chars(self) -> int:
        return self.span_chars or self.repeated_chars

    def notice(self) -> str:
        if self.kind == INTERLEAVED:
            detail = (
                f"the same {self.period_chars}-character block {self.repeats} times "
                f"within the last {self.spanned_chars()} characters"
            )
        else:
            detail = (
                f"the same {self.period_chars}-character block {self.repeats} times "
                f"in a row ({self.repeated_chars} characters)"
            )
        return (
            f"[ciel-runtime] Stopped a runaway repetition loop: the model emitted "
            f"{detail}. The turn was ended early and no further upstream output "
            "was read."
        )

    def log_fields(self) -> str:
        return (
            f"kind={self.kind} period={self.period_chars} repeats={self.repeats} "
            f"repeated_chars={self.repeated_chars} span={self.spanned_chars()}"
        )


def _positive_int(value: str | None, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def policy_from_env(
    env_get: Callable[[str], str | None],
    base: RunawayOutputPolicy | None = None,
) -> RunawayOutputPolicy:
    """Operator overrides for the guard, including a full kill switch."""

    policy = base or RunawayOutputPolicy()
    raw_enabled = env_get("CIEL_RUNTIME_RUNAWAY_GUARD")
    enabled = policy.enabled
    if raw_enabled is not None and str(raw_enabled).strip():
        enabled = str(raw_enabled).strip().lower() not in _DISABLED_VALUES
    return RunawayOutputPolicy(
        enabled=enabled,
        probe_chars=policy.probe_chars,
        min_repeats=_positive_int(
            env_get("CIEL_RUNTIME_RUNAWAY_MIN_REPEATS"), policy.min_repeats
        ),
        min_repeated_chars=_positive_int(
            env_get("CIEL_RUNTIME_RUNAWAY_MIN_CHARS"), policy.min_repeated_chars
        ),
        max_period_chars=_positive_int(
            env_get("CIEL_RUNTIME_RUNAWAY_MAX_PERIOD"), policy.max_period_chars
        ),
        check_interval_chars=policy.check_interval_chars,
        dense_probe_chars=policy.dense_probe_chars,
        dense_probe_samples=policy.dense_probe_samples,
        dense_window_chars=policy.dense_window_chars,
        min_density_percent=_positive_int(
            env_get("CIEL_RUNTIME_RUNAWAY_MIN_DENSITY"), policy.min_density_percent
        ),
    )


def _find_consecutive_loop(
    text: str, policy: RunawayOutputPolicy
) -> RunawayVerdict | None:
    """The tail is one block repeated back to back with nothing in between.

    The probe is the final ``probe_chars`` characters.  Its most recent earlier
    occurrence gives the candidate period, and the repeat count is then verified
    by exact block comparison, so a match is a fact about the string rather than
    an estimate.
    """

    probe_chars = policy.probe_chars
    if len(text) <= probe_chars:
        return None
    probe = text[-probe_chars:]
    previous = text.rfind(probe, 0, len(text) - probe_chars)
    if previous < 0:
        return None
    period = len(text) - probe_chars - previous
    if period <= 0 or period > policy.max_period_chars:
        return None
    unit = text[-period:]
    repeats = 1
    end = len(text) - period
    while end >= period and text[end - period : end] == unit:
        repeats += 1
        end -= period
    repeated_chars = repeats * period
    if repeats < policy.min_repeats or repeated_chars < policy.min_repeated_chars:
        return None
    return RunawayVerdict(
        period_chars=period,
        repeats=repeats,
        repeated_chars=repeated_chars,
        unit_preview=unit[:120],
        kind=CONSECUTIVE,
        span_chars=repeated_chars,
    )


def _occurrences(text: str, probe: str) -> list[int]:
    """Non-overlapping start positions of ``probe`` in ``text``."""

    positions: list[int] = []
    start = text.find(probe)
    while start >= 0:
        positions.append(start)
        start = text.find(probe, start + len(probe))
    return positions


def _grow_common_block(
    text: str, positions: list[int], probe_chars: int
) -> tuple[int, int]:
    """Widen the probe to the longest block every occurrence still shares.

    A fixed-width probe under-measures the repeated block, which would make a
    real loop look less dense than it is. Growing to the actual shared extent
    removes that bias, and the growth is capped so neighbouring occurrences
    cannot be counted twice.
    """

    limit = min(
        later - earlier for earlier, later in zip(positions, positions[1:])
    )
    head = positions[0]
    left = 0
    while probe_chars + left < limit and head - left - 1 >= 0:
        char = text[head - left - 1]
        if any(text[pos - left - 1] != char for pos in positions):
            break
        left += 1
    right = 0
    while probe_chars + left + right < limit:
        index = head + probe_chars + right
        if index >= len(text):
            break
        char = text[index]
        if any(
            pos + probe_chars + right >= len(text)
            or text[pos + probe_chars + right] != char
            for pos in positions
        ):
            break
        right += 1
    return head - left, probe_chars + left + right


def _find_interleaved_loop(
    text: str, policy: RunawayOutputPolicy
) -> RunawayVerdict | None:
    """The same block keeps coming back with other text between the repeats.

    A loop rarely repeats cleanly.  It usually alternates with a little
    variation, which breaks strict periodicity while still being a loop.  This
    rule counts exact occurrences of a recurring block and requires them to make
    up most of the text they span.

    It is deliberately stricter than the back-to-back rule: twice the repeat
    count, and at least ``min_density_percent`` of the spanned characters must
    be the repeated block itself.  That is a real threshold, not a proof -- text
    that is genuinely mostly boilerplate (a table whose rows carry little new
    data, a long block of near-identical log lines) can sit close to the same
    density.  Operators can move the bar or switch the guard off entirely; the
    back-to-back rule above needs no such judgement call.
    """

    probe_chars = policy.dense_probe_chars
    # Scan a bounded window rather than the whole tail buffer. The rule needs
    # only enough room for its occurrence and span budgets, and every measure
    # below is relative to the end, so dropping older text is safe.
    text = text[-policy.dense_window_chars :]
    if len(text) <= probe_chars:
        return None
    # The very last characters are often the part that varies between repeats,
    # so probing only the tail would miss the loop. Sample a few probes stepping
    # back from the end; a repeating core lands inside at least one of them.
    # Half-probe strides so successive samples land on different phases of the
    # loop; one of them falls entirely inside the part that does not vary.
    stride = max(8, probe_chars // 2)
    min_occurrences = policy.min_repeats * 2
    best: RunawayVerdict | None = None
    for sample in range(policy.dense_probe_samples):
        end = len(text) - sample * stride
        if end - probe_chars <= 0:
            break
        probe = text[end - probe_chars : end]
        positions = _occurrences(text, probe)
        occurrences = len(positions)
        if occurrences < min_occurrences:
            continue
        span = len(text) - positions[0]
        if span < policy.min_repeated_chars:
            continue
        start, core = _grow_common_block(text, positions, probe_chars)
        repeated_chars = occurrences * core
        if repeated_chars * 100 < span * policy.min_density_percent:
            continue
        if best is not None and repeated_chars <= best.repeated_chars:
            continue
        best = RunawayVerdict(
            period_chars=core,
            repeats=occurrences,
            repeated_chars=repeated_chars,
            unit_preview=text[start : start + 120],
            kind=INTERLEAVED,
            span_chars=len(text) - start,
        )
    return best


def find_runaway_tail(
    text: str, policy: RunawayOutputPolicy | None = None
) -> RunawayVerdict | None:
    """Report the tail of ``text`` when it has collapsed into a repetition loop.

    Two exact rules, checked in order of how confident they are: a block
    repeated back to back, then the same block recurring densely with other
    text mixed in.
    """

    policy = policy or RunawayOutputPolicy()
    if not policy.enabled or not text or len(text) < policy.min_repeated_chars:
        return None
    return _find_consecutive_loop(text, policy) or _find_interleaved_loop(text, policy)


def trim_runaway_tail(
    text: str, policy: RunawayOutputPolicy | None = None
) -> tuple[str, RunawayVerdict | None]:
    """Cut a repeated tail down to its first pass, keeping everything before it."""

    verdict = find_runaway_tail(text, policy)
    if verdict is None:
        return text, None
    keep = len(text) - verdict.spanned_chars() + verdict.period_chars
    return text[:keep], verdict


_TRIMMABLE_BLOCK_FIELDS = {"text": "text", "thinking": "thinking"}


def trim_runaway_message_content(
    content: object, policy: RunawayOutputPolicy | None = None
) -> tuple[object, RunawayVerdict | None]:
    """Trim runaway tails out of collected Anthropic content blocks.

    The non-streaming collection path hands the whole message over at once, so
    there is nothing to cut short -- the loop has already been generated. The
    guard still removes it, because relaying thousands of repeated characters
    back into the next request's history is what turns one looping turn into a
    looping session.
    """

    if not isinstance(content, list):
        return content, None
    verdict: RunawayVerdict | None = None
    blocks: list[Any] = []
    for block in content:
        field = (
            _TRIMMABLE_BLOCK_FIELDS.get(str(block.get("type") or ""))
            if isinstance(block, dict)
            else None
        )
        raw = block.get(field) if field else None
        if not isinstance(raw, str) or not raw:
            blocks.append(block)
            continue
        trimmed, block_verdict = trim_runaway_tail(raw, policy)
        if block_verdict is None:
            blocks.append(block)
            continue
        verdict = verdict or block_verdict
        blocks.append({**block, field: trimmed})
    if verdict is None:
        return content, None
    blocks.append({"type": "text", "text": verdict.notice()})
    return blocks, verdict


class RunawayOutputDetector:
    """Streaming view of :func:`find_runaway_tail` over a bounded tail buffer."""

    __slots__ = ("_policy", "_tail", "_since_check", "_total_chars", "_verdict")

    def __init__(self, policy: RunawayOutputPolicy | None = None) -> None:
        self._policy = policy or RunawayOutputPolicy()
        self._tail = ""
        self._since_check = 0
        self._total_chars = 0
        self._verdict: RunawayVerdict | None = None

    @property
    def policy(self) -> RunawayOutputPolicy:
        return self._policy

    @property
    def verdict(self) -> RunawayVerdict | None:
        return self._verdict

    @property
    def total_chars(self) -> int:
        return self._total_chars

    def feed(self, text: str) -> RunawayVerdict | None:
        """Append streamed text and return a verdict the first time one holds."""

        if self._verdict is not None:
            return self._verdict
        if not text or not self._policy.enabled:
            return None
        self._total_chars += len(text)
        self._tail = (self._tail + text)[-self._policy.tail_budget() :]
        self._since_check += len(text)
        if self._since_check < self._policy.check_interval_chars:
            return None
        self._since_check = 0
        self._verdict = find_runaway_tail(self._tail, self._policy)
        return self._verdict


__all__ = [
    "CONSECUTIVE",
    "INTERLEAVED",
    "DEFAULT_CHECK_INTERVAL_CHARS",
    "DEFAULT_MAX_PERIOD_CHARS",
    "DEFAULT_MIN_REPEATED_CHARS",
    "DEFAULT_MIN_REPEATS",
    "DEFAULT_PROBE_CHARS",
    "RunawayOutputDetector",
    "RunawayOutputPolicy",
    "RunawayVerdict",
    "find_runaway_tail",
    "policy_from_env",
    "trim_runaway_message_content",
    "trim_runaway_tail",
]
