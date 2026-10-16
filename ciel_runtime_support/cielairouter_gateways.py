"""Standalone CielAiRouter gateway selection; no runtime integration.

Discovery(base, key, timeout), probe(candidate, timeout), and optional
model_support(candidate, model, key, timeout) are injectable and must honor
their deadlines. A missing model callback leaves model eligibility unverified.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping


MAX_CLOCK_OFFSET_SECONDS = 24 * 3600


class DiscoveryError(ValueError):
    """Invalid or unavailable discovery; contains no remote response content."""


class GatewayAuthenticationError(DiscoveryError):
    """Central discovery rejected the API key."""


@dataclass(frozen=True)
class GatewayCandidate:
    node_id: str
    endpoint: str
    health_url: str
    health: str
    available_until: float
    last_seen_at: float
    utilization: float | None
    backlog: float | None
    observed_at: float | None
    timeout_seconds: float = 2.0


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    rtt_seconds: float = math.inf
    status: int | None = None
    node_id: str | None = None
    role: str | None = None
    ready: bool | None = None


@dataclass(frozen=True)
class DiscoverySnapshot:
    candidates: tuple[GatewayCandidate, ...]
    expires_at: float
    excluded_count: int = 0


@dataclass(frozen=True)
class GatewaySelection:
    endpoint: str
    candidate: GatewayCandidate | None
    probe: ProbeResult | None
    reason: str
    degraded: bool
    generation: int
    scope: tuple[str, str, str] = field(repr=False)
    excluded_count: int = 0


def _timestamp(value: Any) -> float:
    if not isinstance(value, str):
        raise DiscoveryError('invalid timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise DiscoveryError('invalid timestamp') from exc


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def normalize_endpoint(value: str, *, allow_insecure_http: bool = False) -> str:
    """Validate URL and strip terminal /v1 so protocol paths append once."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise DiscoveryError('invalid gateway URL')
    try:
        parts = urllib.parse.urlsplit(value)
        host, port = parts.hostname, parts.port
        if (parts.scheme not in {'http', 'https'} or not host
                or parts.username is not None or parts.password is not None
                or '?' in value or '#' in value or chr(92) in value or '%' in parts.netloc):
            raise ValueError
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == 'localhost'
        if parts.scheme == 'http' and not (loopback or allow_insecure_http):
            raise ValueError
        path = parts.path.rstrip('/')
        if '%' in path or '//' in path or any(p in {'.', '..'} for p in path.split('/')):
            raise ValueError
        if path.endswith('/v1'):
            path = path[:-3]
        authority = f'[{host.lower()}]' if ':' in host else host.lower()
        if port is not None and port != (443 if parts.scheme == 'https' else 80):
            authority += f':{port}'
        return urllib.parse.urlunsplit((parts.scheme, authority, path, '', ''))
    except ValueError as exc:
        raise DiscoveryError('gateway URL rejected by trust policy') from exc


def _health_url(endpoint: str) -> str:
    parts = urllib.parse.urlsplit(endpoint)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, '/healthz', '', ''))


def validate_discovery(payload: Any, *, now: float, allow_insecure_http: bool = False) -> DiscoverySnapshot:
    """Validate protocol v1 and retain only fresh, bounded endpoint candidates."""
    if (not isinstance(payload, Mapping) or payload.get('object') != 'list'
            or type(payload.get('protocol_version')) is not int or payload.get('protocol_version') != 1 or not isinstance(payload.get('data'), list)):
        raise DiscoveryError('unsupported discovery schema')
    generated = _timestamp(payload.get('generated_at'))
    # Server timestamps are compared on the server's clock: shift them by the
    # observed offset so a central clock a few seconds ahead or behind this
    # machine neither rejects every document nor ages every node.
    offset = now - generated
    if abs(offset) > MAX_CLOCK_OFFSET_SECONDS:
        raise DiscoveryError('discovery clock out of range')

    def local(value: Any) -> float:
        return _timestamp(value) + offset
    candidates: list[GatewayCandidate] = []
    excluded = 0
    seen: set[tuple[str, str]] = set()
    for row in payload['data']:
        try:
            if not isinstance(row, Mapping) or not isinstance(row.get('id'), str):
                raise DiscoveryError('invalid node')
            last_seen = local(row['lastSeenAt'])
            until = local(row['availableUntil'])
            if row.get('status') != 'online' or not last_seen <= now < until:
                raise DiscoveryError('node unavailable')
            if row.get('health') not in {'healthy', 'degraded', 'unknown'}:
                raise DiscoveryError('invalid health')
            probe = row.get('clientProbe', {})
            if not isinstance(probe, Mapping) or probe.get('path', '/healthz') != '/healthz' or probe.get('method', 'GET') != 'GET':
                raise DiscoveryError('invalid health probe')
            timeout = _number(probe.get('timeoutMs', 2000))
            if timeout is None or timeout < 1:
                raise DiscoveryError('invalid probe timeout')
            congestion = row.get('congestion', {})
            if not isinstance(congestion, Mapping):
                raise DiscoveryError('invalid congestion')
            try:
                observed = local(congestion.get('observedAt'))
            except DiscoveryError:
                observed = None
            fresh = observed is not None and 0 <= now - observed <= 90 and now - last_seen <= 90
            utilization = _number(congestion.get('utilization')) if fresh else None
            backlog = _number(congestion.get('backlog')) if fresh else None
            if utilization is not None and utilization > 1:
                utilization = None
            endpoint_values = row.get('endpoints', [row['endpoint']])
            if not isinstance(endpoint_values, list):
                raise DiscoveryError('invalid endpoint list')
        except (KeyError, TypeError, DiscoveryError):
            excluded += 1
            continue
        # The advertised healthUrl belongs only to the representative endpoint.
        # Its trust failure must not discard independently usable alternatives.
        try:
            representative = normalize_endpoint(row['endpoint'], allow_insecure_http=allow_insecure_http)
        except (KeyError, DiscoveryError):
            representative = None
        expanded: set[str] = set()
        for value in endpoint_values:
            try:
                item = normalize_endpoint(value, allow_insecure_http=allow_insecure_http)
                health_url = _health_url(item)
                if item == representative:
                    declared = normalize_endpoint(row.get('healthUrl', health_url), allow_insecure_http=allow_insecure_http)
                    if declared != health_url:
                        raise DiscoveryError('health origin mismatch')
            except DiscoveryError:
                excluded += 1
                continue
            if item in expanded:
                continue
            expanded.add(item)
            identity = (row['id'], item)
            if identity in seen or len(candidates) >= 16:
                excluded += 1
                continue
            seen.add(identity)
            candidates.append(GatewayCandidate(row['id'], item, _health_url(item), row['health'], until,
                                               last_seen, utilization, backlog, observed, min(timeout / 1000, 2.0)))
    expiry = min([now + 60] + [item.available_until for item in candidates]) if candidates else now + 60
    return DiscoverySnapshot(tuple(candidates), expiry, excluded)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)


class GatewayHttpClient:
    """Injectable urllib transport; health probes never carry the API key."""
    def __init__(self, urlopen: Callable[..., Any] | None = None) -> None:
        self.urlopen = urlopen or urllib.request.build_opener(_NoRedirect()).open

    def get_json(self, url: str, timeout: float, api_key: str | None = None) -> Any:
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {api_key}'} if api_key else {})
        try:
            with self.urlopen(request, timeout=timeout) as response:
                if not 200 <= response.status < 300 or response.geturl() != url:
                    raise DiscoveryError('HTTP status or redirect rejected')
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise DiscoveryError('response too large')
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise GatewayAuthenticationError('discovery authentication rejected') from None
            raise DiscoveryError('HTTP request failed') from None
        except (ValueError, UnicodeError):
            raise DiscoveryError('invalid JSON') from None

    def discovery(self, base_url: str, api_key: str, timeout: float = 2.0) -> Any:
        return self.get_json(base_url.rstrip('/') + '/api/gateway/discovery', timeout, api_key)

    def probe(self, candidate: GatewayCandidate, timeout: float = 2.0) -> ProbeResult:
        started = time.monotonic()
        try:
            request = urllib.request.Request(candidate.health_url)
            with self.urlopen(request, timeout=timeout) as response:
                if not 200 <= response.status < 300 or response.geturl() != candidate.health_url:
                    return ProbeResult(False)
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    return ProbeResult(False)
                payload = json.loads(body) if body.lstrip().startswith(b'{') else {}
            if not isinstance(payload, Mapping):
                return ProbeResult(False)
            return ProbeResult(True, time.monotonic() - started, 200, payload.get('nodeId'), payload.get('role'), payload.get('ready'))
        except Exception:
            return ProbeResult(False)


@dataclass
class _State:
    snapshot: DiscoverySnapshot | None = None
    selection: GatewaySelection | None = None
    generation: int = 0
    next_refresh: float = 0
    failures: dict[str, float] = field(default_factory=dict)
    last_good: GatewayCandidate | None = None


class CielAiRouterGatewayService:
    """Sticky selector, scoped by central URL, key fingerprint, and actual model.

    Callback signatures are discovery(base, key, timeout), probe(candidate,
    timeout), model_support(candidate, model, key, timeout). Model callbacks must
    return True to admit a candidate. Without one, no model claim is made.
    No callback exception text or API key is retained in status.
    """
    def __init__(self, *, discovery: Callable[..., Any] | None = None,
                 probe: Callable[..., ProbeResult] | None = None,
                 model_support: Callable[..., bool] | None = None,
                 urlopen: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 allow_insecure_http: bool = False,
                 cooldown_seconds: float = 60.0, refresh_interval: float = 5.0,
                 exploration_timeout: float = 10.0) -> None:
        client = GatewayHttpClient(urlopen)
        self.discovery = discovery or client.discovery
        self.probe = probe or client.probe
        self.model_support = model_support
        self.clock, self.monotonic = clock, monotonic
        self.allow_insecure_http = allow_insecure_http
        if not all(math.isfinite(v) and v > 0 for v in (cooldown_seconds, refresh_interval, exploration_timeout)):
            raise ValueError('timeouts must be positive finite numbers')
        self.cooldown = cooldown_seconds
        self.refresh_interval = refresh_interval
        self.exploration_timeout = min(exploration_timeout, 10.0)
        self._condition = threading.Condition(threading.RLock())
        self._states: dict[tuple[str, str, str], _State] = {}
        self._flights: set[tuple[str, str, str]] = set()

    def select(self, base_url: str, api_key: str, model: str = '', *, refresh: bool = False,
               fresh: bool = False) -> GatewaySelection:
        """The best gateway for this scope.

        ``fresh`` (a new session starting) ranks all candidates again instead of
        keeping the scope's current gateway, unless an exploration finished
        within ``refresh_interval`` (sessions starting together share it).
        """
        base = normalize_endpoint(base_url, allow_insecure_http=self.allow_insecure_http)
        scope = (base, hashlib.sha256(api_key.encode()).hexdigest(), model)
        with self._condition:
            state = self._states.setdefault(scope, _State())
            waited = False
            while scope in self._flights:
                self._condition.wait()
                waited = True
            now = self.clock()
            if state.selection and (waited or now < state.next_refresh or
                                    (not refresh and not fresh and state.snapshot
                                     and now < state.snapshot.expires_at)):
                return state.selection
            self._flights.add(scope)
            generation = state.generation
            prior = state.last_good
            sticky = None if fresh else (state.selection.candidate if state.selection else None)
            failures = dict(state.failures)
        try:
            selected, snapshot = self._explore(base, api_key, model, scope, generation,
                                               prior, sticky, failures)
            with self._condition:
                # A failure reported during probing fences the entire result.
                if state.generation != generation:
                    return state.selection or GatewaySelection(base, None, None, 'selection invalidated', True, state.generation, scope)
                state.generation += 1
                selected = GatewaySelection(selected.endpoint, selected.candidate, selected.probe,
                                            selected.reason, selected.degraded, state.generation,
                                            scope, selected.excluded_count)
                state.selection = selected
                state.snapshot = snapshot
                state.next_refresh = self.clock() + self.refresh_interval
                if selected.candidate:
                    state.last_good = selected.candidate
                return selected
        finally:
            with self._condition:
                self._flights.discard(scope)
                self._condition.notify_all()

    def _explore(self, base: str, key: str, model: str, scope: tuple[str, str, str],
                 generation: int, prior: GatewayCandidate | None, sticky: GatewayCandidate | None,
                 failures: dict[str, float]) -> tuple[GatewaySelection, DiscoverySnapshot | None]:
        deadline = self.monotonic() + self.exploration_timeout
        reason = 'no healthy gateway'
        snapshot = None
        try:
            payload = self.discovery(base, key, min(5.0, self.exploration_timeout))
            snapshot = validate_discovery(payload, now=self.clock(), allow_insecure_http=self.allow_insecure_http)
            candidates = [c for c in snapshot.candidates if failures.get(c.endpoint, 0) <= self.clock()]
        except GatewayAuthenticationError:
            return GatewaySelection(base, None, None, 'discovery authentication rejected', True, generation, scope), None
        except Exception:
            # A last-good route is not silently considered healthy: probe it again.
            reason = 'discovery unavailable'
            candidates = [prior] if prior and prior.available_until > self.clock() and failures.get(prior.endpoint, 0) <= self.clock() else []
        probes = self._probe_all(candidates, model, key, deadline)
        healthy = [c for c in candidates if c.health == 'healthy' and (c.node_id, c.endpoint) in probes]
        degraded = False
        if not healthy and snapshot:
            # Runbook fallback is a new probe, not a cached success. Never bypass cooldown.
            probes = self._probe_all(candidates, model, key, deadline)
            healthy = [c for c in candidates if (c.node_id, c.endpoint) in probes]
            degraded = True
        if not snapshot:
            healthy = [c for c in candidates if (c.node_id, c.endpoint) in probes]
            degraded = True
        now = self.clock()
        healthy = [c for c in healthy if c.available_until > now]
        if not healthy:
            return GatewaySelection(base, None, None, reason, True, generation, scope,
                                    snapshot.excluded_count if snapshot else 0), snapshot
        # Missing/stale metrics are unknown, never zero load. Refresh staleness at selection time.
        def rank(c: GatewayCandidate) -> tuple[Any, ...]:
            fresh = c.observed_at is not None and 0 <= now - c.observed_at <= 90 and now - c.last_seen_at <= 90
            utilization = c.utilization if fresh else None
            backlog = c.backlog if fresh else None
            host = urllib.parse.urlsplit(c.endpoint).hostname or ''
            try:
                is_local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_local = host.lower() == 'localhost'
            return (0 if is_local else 1, utilization if utilization is not None else math.inf,
                    backlog if backlog is not None else math.inf,
                    probes[(c.node_id, c.endpoint)].rtt_seconds, c.endpoint)
        chosen = next((c for c in healthy if sticky and c.node_id == sticky.node_id and c.endpoint == sticky.endpoint), None)
        if chosen is None:
            chosen = min(healthy, key=rank) if not degraded else max(healthy, key=lambda c: c.last_seen_at)
        return GatewaySelection(chosen.endpoint, chosen, probes[(chosen.node_id, chosen.endpoint)],
                                'sticky' if sticky and chosen.endpoint == sticky.endpoint else 'reprobed fallback' if degraded else 'ranked',
                                degraded, generation, scope, snapshot.excluded_count if snapshot else 0), snapshot

    def _probe_all(self, candidates: list[GatewayCandidate], model: str, key: str,
                   deadline: float) -> dict[tuple[str, str], ProbeResult]:
        def run(candidate: GatewayCandidate) -> ProbeResult | None:
            timeout = min(candidate.timeout_seconds, deadline - self.monotonic())
            if timeout <= 0:
                return None
            try:
                result = self.probe(candidate, timeout)
                if (not isinstance(result, ProbeResult) or not result.ok
                        or not math.isfinite(result.rtt_seconds) or result.rtt_seconds < 0
                        or result.rtt_seconds > timeout
                        or result.status is not None and not 200 <= result.status < 300
                        or result.node_id is not None and result.node_id != candidate.node_id
                        or result.role is not None and result.role != 'gateway'
                        or result.ready is not None and result.ready is not True):
                    return None
                timeout = min(2.0, deadline - self.monotonic())
                if timeout <= 0:
                    return None
                if self.model_support and self.model_support(candidate, model, key, timeout) is not True:
                    return None
                return result
            except Exception:
                return None
        pool = ThreadPoolExecutor(max_workers=4)
        futures = {pool.submit(run, c): c for c in candidates}
        done, pending = wait(futures, timeout=max(0, deadline - self.monotonic()))
        for future in pending:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        return {(futures[f].node_id, futures[f].endpoint): result for f in done if (result := f.result()) is not None}

    def record_failure(self, selection: GatewaySelection, classification: str) -> bool:
        """Cool down a failed gateway; only connectivity, truncation and 5xx count.

        Any session's route may report (a session keeps its own gateway, which
        need not be the scope's latest pick).  Returns True only when this report
        starts the cooldown, so concurrent reports of one outage count once.
        """
        if classification not in {'connection', 'timeout', 'stream_truncated', '5xx'} or not selection.candidate:
            return False
        with self._condition:
            state = self._states.get(selection.scope)
            if not state:
                return False
            now = self.clock()
            already = state.failures.get(selection.endpoint, 0) > now
            state.failures[selection.endpoint] = now + self.cooldown
            if state.selection is not None and state.selection.endpoint == selection.endpoint:
                state.generation += 1
                state.selection = None
                state.snapshot = None
                state.next_refresh = 0
            return not already

    def is_cooling(self, selection: GatewaySelection) -> bool:
        """Whether this route's gateway is cooling down after a failure."""
        with self._condition:
            state = self._states.get(selection.scope)
            return bool(state and state.failures.get(selection.endpoint, 0) > self.clock())

    def status(self) -> list[dict[str, Any]]:
        """Sanitized in-memory status; no keys, remote bodies, or exception text."""
        with self._condition:
            return [{'origin': urllib.parse.urlsplit(scope[0]).netloc,
                     'endpoint': state.selection.endpoint if state.selection else None,
                     'reason': state.selection.reason if state.selection else 'unselected',
                     'degraded': state.selection.degraded if state.selection else True,
                     'generation': state.generation,
                     'cooldowns': dict(state.failures)} for scope, state in self._states.items()]

