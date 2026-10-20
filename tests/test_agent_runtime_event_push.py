"""Turn ends pushed straight to Walkie's /api/agent-runtime-events (Walkie 48926 F3).

Walkie's contract (server/src/agent-runtime-events.ts): CloudEvents 1.0, one
event as application/cloudevents+json, the agent's own service credential as
``Authorization: Bearer``, each (source, id) handled once a day, an
Idempotency-Key answered with the first answer.  ``agent.turn_ended`` data is
turn_id, reason, by_user_input, runtime (and the Runtime's ended_at,
stop_check_blocked).
"""

from datetime import datetime
import io
import json
import unittest
import urllib.error

from ciel_runtime_support import tui_observation
from ciel_runtime_support.agent_runtime_event_push import (
    TURN_ENDED_TYPE,
    AgentRuntimeEventPusher,
    RuntimeEventEndpoint,
    runtime_event_endpoints,
    turn_ended_cloud_event,
)
from ciel_runtime_support.agent_turn_events import turn_event

CREDENTIAL = "wka_test-credential-value"
STREAM = "http://walkie.test:8787/api/notification-delivery/stream?consumer_id=ws.walkie-inbox-v1"
ENDPOINT = "http://walkie.test:8787/api/agent-runtime-events"


def _receivers(**override):
    receivers = {
        "default": {"enabled": True, "transport": "sse", "url": STREAM},
        "other": {"enabled": False, "transport": "sse", "url": "http://elsewhere.test/api/notification-delivery/stream"},
        "hook": {"enabled": True, "transport": "webhook", "url": ""},
    }
    receivers.update(override)
    return receivers


def _vault(values):
    return lambda receiver_id: dict(values.get(receiver_id, {}))


class _Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return b'{"accepted":1,"processed":1}'


class _Server:
    """Answers each POST with the next status (200 when the list is used up)."""

    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(
            {
                "url": request.full_url,
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": json.loads(request.data),
                "timeout": timeout,
            }
        )
        status = self.statuses.pop(0) if self.statuses else 200
        if isinstance(status, Exception):
            raise status
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(b"{}"))
        return _Response(status)


class _Clock:
    def __init__(self):
        self.now = 1_791_000_000.0

    def __call__(self):
        return self.now


def _pusher(server, clock=None, logs=None, endpoints=None):
    logs = [] if logs is None else logs
    endpoint = RuntimeEventEndpoint(ENDPOINT, "Bearer " + CREDENTIAL)
    return AgentRuntimeEventPusher(
        "workspace-1",
        lambda: [endpoint] if endpoints is None else endpoints,
        lambda level, message: logs.append((level, message)),
        urlopen=server,
        clock=clock or _Clock(),
    )


FIELDS = turn_event("turn-1", "claude", "end_turn", by_user_input=True, ended_at="2026-10-12T19:39:00Z")


class EndpointTests(unittest.TestCase):
    def test_walkie_stream_receiver_gives_the_events_endpoint_and_its_credential(self):
        endpoints = runtime_event_endpoints({}, _receivers(), _vault({"default": {"authorization": CREDENTIAL}}))
        self.assertEqual([ENDPOINT], [e.url for e in endpoints])
        self.assertEqual("Bearer " + CREDENTIAL, endpoints[0].authorization)

    def test_a_bearer_prefix_is_kept_and_not_doubled(self):
        endpoints = runtime_event_endpoints({}, _receivers(), _vault({"default": {"authorization": "Bearer " + CREDENTIAL}}))
        self.assertEqual("Bearer " + CREDENTIAL, endpoints[0].authorization)

    def test_no_credential_disabled_receiver_or_switch_off_gives_no_endpoint(self):
        self.assertEqual([], runtime_event_endpoints({}, _receivers(), _vault({})))
        self.assertEqual([], runtime_event_endpoints({}, {"other": _receivers()["other"]}, _vault({"other": {"authorization": CREDENTIAL}})))
        self.assertEqual([], runtime_event_endpoints({"agent_runtime_events": {"enabled": False}}, _receivers(), _vault({"default": {"authorization": CREDENTIAL}})))

    def test_an_explicit_url_uses_the_named_receivers_credential(self):
        config = {"agent_runtime_events": {"url": "https://walkie.example/api/agent-runtime-events", "credential_receiver": "default", "timeout_seconds": 3}}
        endpoints = runtime_event_endpoints(config, _receivers(), _vault({"default": {"authorization": CREDENTIAL}}))
        self.assertEqual(["https://walkie.example/api/agent-runtime-events"], [e.url for e in endpoints])
        self.assertEqual("Bearer " + CREDENTIAL, endpoints[0].authorization)
        self.assertEqual(3.0, endpoints[0].timeout_seconds)

    def test_a_missing_environment_reference_gives_no_endpoint(self):
        vault = _vault({"default": {"authorization": "${CIEL_TEST_UNSET_WALKIE_TOKEN_48926}"}})
        self.assertEqual([], runtime_event_endpoints({}, _receivers(), vault))


class CloudEventShapeTests(unittest.TestCase):
    def test_turn_end_is_a_walkie_runtime_cloud_event(self):
        event = turn_ended_cloud_event(FIELDS, "workspace-1", 1_791_000_000.0)
        self.assertEqual("1.0", event["specversion"])
        self.assertEqual(TURN_ENDED_TYPE, event["type"])
        self.assertEqual("ai.oneciel.ciel-runtime.agent.turn_ended", event["type"])
        self.assertEqual("urn:ciel-runtime:workspace:workspace-1", event["source"])
        self.assertEqual("application/json", event["datacontenttype"])
        datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        self.assertTrue(0 < len(event["id"]) <= 256)
        self.assertEqual(
            {"turn_id": "turn-1", "runtime": "claude", "ended_at": "2026-10-12T19:39:00Z", "reason": "end_turn", "by_user_input": True, "stop_check_blocked": False},
            event["data"],
        )

    def test_the_id_is_the_turns_own_however_often_or_late_it_is_built(self):
        first = turn_ended_cloud_event(FIELDS, "workspace-1", 1.0)
        again = turn_ended_cloud_event(FIELDS, "workspace-1", 9_999.0)
        other = turn_ended_cloud_event({**FIELDS, "turn_id": "turn-2"}, "workspace-1", 1.0)
        self.assertEqual(first["id"], again["id"])
        self.assertNotEqual(first["id"], other["id"])


class PushTests(unittest.TestCase):
    def test_one_turn_is_sent_once_with_the_contract_headers(self):
        server = _Server()
        pusher = _pusher(server)
        pusher.submit(FIELDS)
        pusher.submit(dict(FIELDS))
        self.assertEqual(1, pusher.flush())
        self.assertEqual(0, pusher.flush())
        self.assertEqual(1, len(server.requests))
        request = server.requests[0]
        self.assertEqual(ENDPOINT, request["url"])
        self.assertEqual("application/cloudevents+json", request["headers"]["content-type"])
        self.assertEqual("Bearer " + CREDENTIAL, request["headers"]["authorization"])
        self.assertEqual(request["body"]["id"], request["headers"]["idempotency-key"])
        self.assertRegex(request["headers"]["idempotency-key"], r"^[\x21-\x7e]{1,200}$")
        self.assertEqual(TURN_ENDED_TYPE, request["body"]["type"])
        self.assertEqual("turn-1", request["body"]["data"]["turn_id"])

    def test_a_5xx_or_network_error_is_retried_with_the_same_id(self):
        clock, logs = _Clock(), []
        server = _Server(503, urllib.error.URLError("refused"), 200)
        pusher = _pusher(server, clock, logs)
        pusher.submit(FIELDS)
        self.assertEqual(0, pusher.flush())
        self.assertEqual(0, pusher.flush())  # not due yet: backoff
        clock.now += 60
        self.assertEqual(0, pusher.flush())
        clock.now += 60
        self.assertEqual(1, pusher.flush())
        self.assertEqual(3, len(server.requests))
        self.assertEqual(1, len({r["body"]["id"] for r in server.requests}))
        self.assertEqual(1, len({r["headers"]["idempotency-key"] for r in server.requests}))

    def test_a_4xx_is_not_retried(self):
        for status in (400, 401, 403, 415):
            with self.subTest(status=status):
                clock, logs = _Clock(), []
                server = _Server(status)
                pusher = _pusher(server, clock, logs)
                pusher.submit(FIELDS)
                pusher.flush()
                clock.now += 3600
                pusher.flush()
                self.assertEqual(1, len(server.requests))
                self.assertTrue(any(level == "WARN" and f"status={status}" in message for level, message in logs))

    def test_429_is_retried(self):
        clock = _Clock()
        server = _Server(429, 200)
        pusher = _pusher(server, clock)
        pusher.submit(FIELDS)
        pusher.flush()
        clock.now += 60
        self.assertEqual(1, pusher.flush())
        self.assertEqual(2, len(server.requests))

    def test_retries_are_bounded(self):
        clock, logs = _Clock(), []
        server = _Server(*([500] * 50))
        pusher = _pusher(server, clock, logs)
        pusher.submit(FIELDS)
        for _ in range(50):
            pusher.flush()
            clock.now += 120
        self.assertEqual(pusher.max_attempts, len(server.requests))
        self.assertTrue(any(level == "WARN" and "agent_runtime_event_dropped" in message for level, message in logs))

    def test_the_credential_never_reaches_the_log(self):
        logs = []
        for server in (_Server(200), _Server(403), _Server(*([500] * 10))):
            clock = _Clock()
            pusher = _pusher(server, clock, logs)
            pusher.submit(FIELDS)
            for _ in range(10):
                pusher.flush()
                clock.now += 120
        self.assertTrue(logs)
        self.assertFalse(any(CREDENTIAL in message for _level, message in logs))

    def test_no_endpoint_means_nothing_is_queued(self):
        server = _Server()
        pusher = _pusher(server, endpoints=[])
        pusher.submit(FIELDS)
        self.assertEqual(0, pusher.flush())
        self.assertEqual([], server.requests)

    def test_the_routers_turn_end_reaches_the_pusher_once(self):
        server = _Server()
        pusher = _pusher(server)
        bus = tui_observation.TuiObservationBus(enabled=True)
        pusher.attach()
        try:
            bus.publish_turn_ended(FIELDS)
            bus.publish_turn_ended(FIELDS)
        finally:
            pusher.detach()
        bus.publish_turn_ended({**FIELDS, "turn_id": "after-detach"})
        pusher.flush()
        self.assertEqual(["turn-1"], [r["body"]["data"]["turn_id"] for r in server.requests])


if __name__ == "__main__":
    unittest.main()
