import http.server
import json
import re
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

from ciel_runtime_support import event_delivery_ack
from ciel_runtime_support.event_delivery_ack import (
    ACK_TIMEOUT_SECONDS,
    LEDGER_RETENTION_SECONDS,
    RETRY_DELAYS,
    DeliveryAckService,
    delivery_ack_request,
    ledger_summary,
    receiver_client_header,
)
from ciel_runtime_support.external_event_menu import panel_rows
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService


SSE_URL = "http://walkie.test:8787/api/notification-delivery/stream?consumer_id=c27b0d656c05.walkie-inbox-v1"
ACK_URL = "http://walkie.test:8787/api/notification-delivery/event-ack"
CONSUMER = "c27b0d656c05.walkie-inbox-v1"


def frame(event_id, stream_id, ack=True, url=ACK_URL, redelivery_of=None):
    data = {"stream_id": stream_id, "event_type": "message.approved", "text": "BODY-TEXT-7 approved by a person"}
    if ack:
        data["delivery_ack"] = {
            "required": True, "version": 1, "url": url, "stream_id": stream_id,
            "decision": "approval", "redelivery_of": redelivery_of,
        }
    return json.dumps({
        "specversion": "1.0", "id": event_id, "source": "http://walkie.test:8787/conversations/c1",
        "type": "com.ciel.walkie.support.message", "data": data,
    }, separators=(",", ":"))


class _Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class _SseResponse:
    def __init__(self, texts, done=None):
        self.lines = [line for text in texts for line in f"id: x\ndata: {text}\n\n".encode().splitlines(keepends=True)]
        self.headers = {"content-type": "text/event-stream"}
        self.done = done

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        yield from self.lines
        if self.done is not None:
            self.done()  # the stream ends and the receiver stops instead of reconnecting


class _AckResponse:
    def __init__(self, body, status=200):
        self.body = json.dumps(body).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


class _Walkie:
    """Records ack posts and answers like Walkie's eventAck (decision-delivery.ts)."""

    def __init__(self, known=None, fail=None):
        self.posts = []
        self.known = known
        self.fail = list(fail or [])

    def __call__(self, request, timeout):
        self.posts.append({
            "url": request.full_url, "timeout": timeout, "body": json.loads(request.data),
            "authorization": request.get_header("Authorization"), "method": request.get_method(),
        })
        if self.fail:
            failure = self.fail.pop(0)
            if isinstance(failure, int):
                raise urllib.error.HTTPError(request.full_url, failure, "x", {}, None)
            raise failure
        acks = json.loads(request.data)["acks"]
        acked = [a["stream_id"] for a in acks if self.known is None or a["stream_id"] in self.known]
        unknown = [a["stream_id"] for a in acks if a["stream_id"] not in acked]
        return _AckResponse({"acked": acked, "unknown": unknown, "rejected": []})


class _Fixture:
    def __init__(self, root, admitted=None, walkie=None, clock=None):
        self.root = Path(root)
        self.config = {"external_event_receivers": {"ws": {}}}
        self.submitted = []
        self.admitted = admitted or (lambda raw, meta: {"id": len(self.submitted)})
        self.walkie = walkie or _Walkie()
        self.clock = clock or _Clock()
        self.logs = []
        self.sse_requests = []
        self.frames = []
        self.ack = DeliveryAckService(
            self.root, self.root / "runtime-input-status.jsonl", lambda level, text: self.logs.append((level, text)),
            urlopen=self.walkie, clock=self.clock,
        )
        self.service = ExternalEventReceiverService(
            load_config=lambda: self.config,
            save_config=self._save,
            write_json=lambda *_args: None,
            submit_event=self._submit,
            vault=EventReceiverSecretVault(self.root / "vault.json"),
            workspace_key="ws",
            log=lambda level, text: self.logs.append((level, text)),
            cursor_path=self.root / "cursors.json",
            urlopen=self._sse,
            delivery_ack=self.ack,
            client_header="ciel-runtime/0.2.52-test (TestOS 1) runtime=python/3.12.0",
        )
        self.service.save_receiver("default", {
            "enabled": True, "transport": "sse", "url": SSE_URL, "authorization": "wka_test",
            "cursor_json_pointer": "/data/stream_id", "cursor_query_parameter": "after",
        })
        self.ack.endpoint = self.service._ack_endpoint

    def _save(self, value):
        copied = json.loads(json.dumps(value))
        self.config.clear()
        self.config.update(copied)

    def _submit(self, raw, **meta):
        self.submitted.append((raw, meta))
        return self.admitted(raw, meta)

    def _sse(self, request, timeout):
        self.sse_requests.append(request)
        return _SseResponse(self.frames, self.service._stop.set)

    def stream(self, *texts):
        self.frames = list(texts)
        self.sse_requests.clear()
        self.service._stop.clear()
        self.service._run_sse("default")

    def ledger(self):
        path = self.root / "event-acks.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class DeliveryAckHeaderTests(unittest.TestCase):
    def test_sse_get_says_event_ack_and_names_the_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream()
            request = fixture.sse_requests[0]
            self.assertEqual("event-ack/1", request.get_header("Ciel-receiver-features"))
            self.assertEqual("ciel-runtime/0.2.52-test (TestOS 1) runtime=python/3.12.0", request.get_header("Walkie-client"))
            self.assertEqual("Bearer wka_test", request.get_header("Authorization"))

    def test_receiver_without_the_ack_service_never_claims_the_feature(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.service.delivery_ack = None
            fixture.service.client_header = ""
            fixture.stream()
            request = fixture.sse_requests[0]
            self.assertIsNone(request.get_header("Ciel-receiver-features"))
            self.assertIsNone(request.get_header("Walkie-client"))

    def test_client_header_matches_walkie_parse_walkie_client(self):
        # server/src/client-connections.ts parseWalkieClient and VERSION, as of Walkie rel430.
        pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,63})/(\S{1,40})(?:\s+\(([^)]{1,120})\))?(?:\s+runtime=(\S{1,80}))?\s*$")
        version = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
        header = receiver_client_header("0.2.51-nightly.20261014-230508.d2be7d8")
        match = pattern.match(header)
        self.assertIsNotNone(match, header)
        self.assertEqual("ciel-runtime", match.group(1))
        self.assertEqual("0.2.51-nightly.20261014-230508.d2be7d8", match.group(2))
        self.assertTrue(version.match(match.group(2)))
        self.assertTrue(match.group(3))
        self.assertTrue(match.group(4).startswith("python/"))


class DeliveryAckQueueTests(unittest.TestCase):
    def test_new_row_is_acknowledged_queued_with_the_sse_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            self.assertEqual(1, len(fixture.submitted))
            self.assertEqual(1, fixture.ack.flush())
            post = fixture.walkie.posts[0]
            self.assertEqual(ACK_URL, post["url"])
            self.assertEqual("POST", post["method"])
            self.assertEqual(ACK_TIMEOUT_SECONDS, post["timeout"])
            self.assertEqual("Bearer wka_test", post["authorization"])
            self.assertEqual(CONSUMER, post["body"]["consumer_id"])
            ack = post["body"]["acks"][0]
            self.assertEqual(
                {"stream_id": "781", "event_id": "conv:41:message.approved:e1", "stage": "queued", "outcome": "queued"},
                {key: ack[key] for key in ("stream_id", "event_id", "stage", "outcome")},
            )
            self.assertTrue(ack["received_at"].endswith("Z") and ack["queued_at"].endswith("Z"))
            entry = fixture.ledger()[-1]
            self.assertEqual("acked", entry["queued_ack"]["status"])
            self.assertEqual(1, entry["request_id"])
            self.assertNotIn("BODY-TEXT-7", json.dumps(fixture.ledger()))  # no event text in the ledger
            self.assertNotIn("wka_test", json.dumps(fixture.ledger()))

    def test_same_event_id_already_queued_is_acknowledged_already_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = {"id": 5, "created_at_epoch": 1_799_999_990.0, "_ciel_runtime_duplicate": True,
                        "meta": {"event_id": "conv:41:message.approved:e1"}}
            fixture = _Fixture(directory, admitted=lambda raw, meta: dict(existing))
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            fixture.ack.flush()
            ack = fixture.walkie.posts[0]["body"]["acks"][0]
            self.assertEqual("already_queued", ack["outcome"])
            self.assertEqual(event_delivery_ack.iso_utc(1_799_999_990.0), ack["queued_at"])

    def test_redelivery_of_a_queued_event_is_not_queued_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            fixture.ack.flush()
            redelivery = {"event_id": "conv:41:message.approved:e1", "stream_id": "781", "attempt": 1}
            fixture.stream(frame("conv:41:message.approved:e9", "790", redelivery_of=redelivery))
            self.assertEqual(1, len(fixture.submitted))  # the re-send never reached the queue
            fixture.ack.flush()
            ack = fixture.walkie.posts[1]["body"]["acks"][0]
            self.assertEqual(("790", "conv:41:message.approved:e9", "already_queued"), (ack["stream_id"], ack["event_id"], ack["outcome"]))
            first = fixture.walkie.posts[0]["body"]["acks"][0]
            self.assertEqual(first["queued_at"], ack["queued_at"])
            self.assertEqual("790", json.loads((Path(directory) / "cursors.json").read_text())["default"])

    def test_redelivery_whose_first_event_was_never_queued_is_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            redelivery = {"event_id": "conv:41:message.approved:lost", "stream_id": "781", "attempt": 1}
            fixture.stream(frame("conv:41:message.approved:e9", "790", redelivery_of=redelivery))
            self.assertEqual(1, len(fixture.submitted))
            fixture.ack.flush()
            self.assertEqual("queued", fixture.walkie.posts[0]["body"]["acks"][0]["outcome"])

    def test_absent_original_redeliveries_share_one_row_and_keep_both_ack_ids(self):
        for restart in (False, True):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                fixture = _Fixture(directory)
                original = {"event_id": "e0", "stream_id": "780", "attempt": 1}
                fixture.stream(frame("e1", "781", redelivery_of=original))
                if restart:
                    fixture.ack = DeliveryAckService(
                        Path(directory), Path(directory) / "runtime-input-status.jsonl",
                        lambda *_args: None, urlopen=fixture.walkie, clock=fixture.clock,
                    )
                    fixture.ack.endpoint = fixture.service._ack_endpoint
                    fixture.service.delivery_ack = fixture.ack
                fixture.stream(frame("e2", "782", redelivery_of={**original, "attempt": 2}))
                self.assertEqual(1, len(fixture.submitted))
                entries = list(fixture.ack.entries().values())
                self.assertEqual(["queued", "already_queued"], [e["outcome"] for e in entries])
                self.assertEqual({"e0"}, {e["canonical_event_id"] for e in entries})
                self.assertEqual({1}, {e["request_id"] for e in entries})
                self.assertEqual(2, fixture.ack.flush())
                status_path = Path(directory) / "runtime-input-status.jsonl"
                status_path.write_text(json.dumps({
                    "request_id": 1, "status": "submitted", "updated_at_epoch": fixture.clock.now + 1,
                }) + "\n", encoding="utf-8")
                self.assertEqual(2, fixture.ack.scan_injections())
                self.assertEqual(2, fixture.ack.flush())
                acks = [a for post in fixture.walkie.posts for a in post["body"]["acks"]]
                self.assertEqual(
                    [("e1", "781", "queued"), ("e2", "782", "queued"),
                     ("e1", "781", "injected"), ("e2", "782", "injected")],
                    [(a["event_id"], a["stream_id"], a["stage"]) for a in acks],
                )

    def test_rejected_redelivery_does_not_claim_the_canonical_identity_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            other = {"id": 5, "_ciel_runtime_duplicate": True, "meta": {"event_id": "other"}}
            fixture = _Fixture(directory, admitted=lambda raw, meta: dict(other))
            original = {"event_id": "e0", "stream_id": "780", "attempt": 1}
            fixture.stream(frame("e1", "781", redelivery_of=original))
            self.assertEqual("rejected", fixture.ledger()[-1]["outcome"])
            fixture.ack._entries = None
            fixture.admitted = lambda raw, meta: {"id": 6}
            fixture.stream(frame("e2", "782", redelivery_of=original))
            fixture.stream(frame("e3", "783", redelivery_of=original))
            self.assertEqual(2, len(fixture.submitted))
            self.assertEqual(["rejected", "queued", "already_queued"],
                             [e["outcome"] for e in fixture.ack.entries().values()])
            self.assertIsNone(fixture.ack.entries()["default e1"]["injected_ack"])

    def test_canonical_lookup_is_scoped_to_receiver_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            original = {"event_id": "e0", "stream_id": "780", "attempt": 1}
            fixture.stream(frame("e1", "781", redelivery_of=original))
            request = delivery_ack_request(frame("e2", "782", redelivery_of=original))
            self.assertIsNotNone(fixture.ack.queued_original("default", request, "e2"))
            self.assertIsNone(fixture.ack.queued_original("other", request, "e2"))
            different = json.loads(frame("e1", "782", redelivery_of=original))
            different["source"] = "http://walkie.test:8787/conversations/other"
            fixture.stream(json.dumps(different))
            fixture.ack._entries = None
            self.assertEqual(2, len(fixture.ack.entries()))
            self.assertEqual(2, len(fixture.submitted))
            other_request = delivery_ack_request(json.dumps(different))
            self.assertEqual(1, fixture.ack.queued_original("default", request, "e2")["request_id"])
            self.assertEqual(2, fixture.ack.queued_original("default", other_request, "e2")["request_id"])

    def test_queue_keeping_another_events_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            other = {"id": 5, "_ciel_runtime_duplicate": True, "meta": {"event_id": "conv:41:other"}}
            fixture = _Fixture(directory, admitted=lambda raw, meta: dict(other))
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            fixture.ack.flush()
            ack = fixture.walkie.posts[0]["body"]["acks"][0]
            self.assertEqual("rejected", ack["outcome"])
            self.assertNotIn("queued_at", ack)

    def test_events_without_delivery_ack_are_admitted_as_before(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            text = frame("conv:41", "780", ack=False)
            fixture.stream(text)
            self.assertEqual(text, fixture.submitted[0][0])
            self.assertEqual("conv:41", fixture.submitted[0][1]["event_id"])
            self.assertEqual(0, fixture.ack.flush())
            self.assertEqual([], fixture.walkie.posts)
            self.assertEqual([], fixture.ledger())

    def test_parse_ignores_events_that_do_not_ask(self):
        self.assertIsNone(delivery_ack_request(frame("a", "1", ack=False)))
        self.assertIsNone(delivery_ack_request("not json"))
        not_required = json.loads(frame("a", "1"))
        not_required["data"]["delivery_ack"]["required"] = False
        self.assertIsNone(delivery_ack_request(json.dumps(not_required)))
        other_version = json.loads(frame("a", "1"))
        other_version["data"]["delivery_ack"]["version"] = 2
        self.assertIsNone(delivery_ack_request(json.dumps(other_version)))


class DeliveryAckSecurityTests(unittest.TestCase):
    def test_other_origin_is_never_posted_to(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            for index, url in enumerate((
                "https://walkie.test:8787/api/notification-delivery/event-ack",
                "http://walkie.test:8788/api/notification-delivery/event-ack",
                "http://evil.test:8787/api/notification-delivery/event-ack",
            )):
                fixture.stream(frame(f"e{index}", str(780 + index), url=url))
            self.assertEqual(0, fixture.ack.flush())
            self.assertEqual([], fixture.walkie.posts)
            self.assertEqual({"cross_origin"}, {item["queued_ack"]["detail"] for item in fixture.ledger()})

    def test_default_port_counts_as_the_same_origin(self):
        self.assertEqual(
            event_delivery_ack.origin_of("https://walkie.example/api/x"),
            event_delivery_ack.origin_of("https://WALKIE.example:443/api/notification-delivery/event-ack"),
        )

    def test_redirect_is_refused_and_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory, walkie=_Walkie(fail=[302]))
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            fixture.clock.now += 60
            fixture.ack.flush()
            self.assertEqual(1, len(fixture.walkie.posts))
            self.assertEqual("dropped", fixture.ledger()[-1]["queued_ack"]["status"])
            self.assertIn("redirect_refused", fixture.ledger()[-1]["queued_ack"]["detail"])

    def test_real_opener_does_not_follow_a_redirect(self):
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                hits.append(self.path)
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            import urllib.request

            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/ack", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                event_delivery_ack._no_redirect_open(request, 5)
            self.assertEqual(307, caught.exception.code)
            self.assertEqual(["/ack"], hits)
        finally:
            server.shutdown()
            server.server_close()


class DeliveryAckRetryTests(unittest.TestCase):
    def test_retries_after_1_2_4_8_16_seconds_then_keeps_it_for_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            failures = [urllib.error.URLError("down")] * 6
            fixture = _Fixture(directory, walkie=_Walkie(fail=failures))
            fixture.stream(frame("e1", "781"))
            times = []
            for _ in range(200):
                before = len(fixture.walkie.posts)
                fixture.ack.flush()
                if len(fixture.walkie.posts) > before:
                    times.append(fixture.clock.now)
                fixture.clock.now += 0.5
            self.assertEqual([0.0, 1.0, 3.0, 7.0, 15.0, 31.0], [moment - times[0] for moment in times])
            fixture.clock.now += 120
            fixture.ack.flush()
            self.assertEqual(6, len(fixture.walkie.posts))
            self.assertEqual("failed", fixture.ledger()[-1]["queued_ack"]["status"])
            self.assertEqual(tuple(RETRY_DELAYS), (1.0, 2.0, 4.0, 8.0, 16.0))
            # A new router reads the ledger and sends it again.
            walkie = _Walkie()
            restarted = DeliveryAckService(Path(directory), Path(directory) / "runtime-input-status.jsonl",
                                           lambda *_a: None, urlopen=walkie, clock=fixture.clock)
            restarted.endpoint = fixture.service._ack_endpoint
            restarted.start(fixture.service._ack_endpoint)
            restarted.stop()
            restarted.flush()
            self.assertEqual("781", walkie.posts[-1]["body"]["acks"][0]["stream_id"])
            self.assertEqual("acked", restarted.ledger.load()["default e1"]["queued_ack"]["status"])

    def test_stream_reconnect_sends_a_failed_ack_again(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory, walkie=_Walkie(fail=[503] * 6))
            fixture.stream(frame("e1", "781"))
            for _ in range(200):
                fixture.ack.flush()
                fixture.clock.now += 0.5
            self.assertEqual("failed", fixture.ledger()[-1]["queued_ack"]["status"])
            fixture.stream()  # reconnects
            fixture.ack.flush()
            self.assertEqual("acked", fixture.ledger()[-1]["queued_ack"]["status"])
            self.assertEqual(7, len(fixture.walkie.posts))

    def test_client_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory, walkie=_Walkie(fail=[400]))
            fixture.stream(frame("e1", "781"))
            for _ in range(10):
                fixture.ack.flush()
                fixture.clock.now += 5
            self.assertEqual(1, len(fixture.walkie.posts))
            self.assertEqual(("dropped", "status=400"), (fixture.ledger()[-1]["queued_ack"]["status"], fixture.ledger()[-1]["queued_ack"]["detail"]))

    def test_unknown_stream_ids_are_recorded_and_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory, walkie=_Walkie(known=set()))
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            fixture.clock.now += 60
            fixture.ack.flush()
            self.assertEqual(1, len(fixture.walkie.posts))
            self.assertEqual("unknown", fixture.ledger()[-1]["queued_ack"]["status"])

    def test_an_ack_older_than_24_hours_expires_unsent(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory, walkie=_Walkie(fail=[503] * 6))
            fixture.stream(frame("e1", "781"))
            for _ in range(200):
                fixture.ack.flush()
                fixture.clock.now += 0.5
            fixture.clock.now += 24 * 3600
            fixture.stream()
            fixture.ack.flush()
            self.assertEqual(6, len(fixture.walkie.posts))
            self.assertEqual("expired", fixture.ledger()[-1]["queued_ack"]["status"])

    def test_one_post_carries_at_most_100_acks(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(*(frame(f"e{index}", str(1000 + index)) for index in range(150)))
            self.assertEqual(150, fixture.ack.flush())
            self.assertEqual([100, 50], [len(post["body"]["acks"]) for post in fixture.walkie.posts])


class DeliveryAckInjectionTests(unittest.TestCase):
    def _status(self, directory, *records):
        with (Path(directory) / "runtime-input-status.jsonl").open("a", encoding="utf-8") as stream:
            for request_id, status, at in records:
                stream.write(json.dumps({"request_id": request_id, "status": status, "updated_at_epoch": at}) + "\n")

    def test_injected_is_acknowledged_once_the_row_is_submitted(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            self._status(directory, (1, "queued", 1_799_999_000.0), (1, "submitted", 1_799_999_100.0))  # an older row 1
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            self.assertEqual(0, fixture.ack.scan_injections())  # the earlier row 1 is not this event
            self._status(directory, (1, "queued", 1_800_000_000.0))
            self.assertEqual(0, fixture.ack.scan_injections())
            self._status(directory, (1, "submitted", 1_800_000_012.5))
            self.assertEqual(1, fixture.ack.scan_injections())
            self.assertEqual(1, fixture.ack.flush())
            ack = fixture.walkie.posts[-1]["body"]["acks"][0]
            self.assertEqual({"stream_id": "781", "event_id": "e1", "stage": "injected",
                              "injected_at": event_delivery_ack.iso_utc(1_800_000_012.5)}, ack)
            self._status(directory, (1, "replied", 1_800_000_090.0))
            self.assertEqual(0, fixture.ack.scan_injections())
            self.assertEqual(0, fixture.ack.flush())
            entry = fixture.ledger()[-1]
            self.assertEqual("acked", entry["injected_ack"]["status"])
            self.assertEqual(event_delivery_ack.iso_utc(1_800_000_012.5), entry["injected_at"])

    def test_a_failed_submission_is_recorded_without_an_injected_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            self._status(directory, (1, "queued", 1_800_000_000.0), (1, "failed", 1_800_000_030.0))
            self.assertEqual(0, fixture.ack.scan_injections())
            self.assertEqual(1, len(fixture.walkie.posts))
            self.assertEqual("failed", fixture.ledger()[-1]["session_status"])


class DeliveryAckLedgerTests(unittest.TestCase):
    def test_entries_older_than_14_days_are_pruned_on_load(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("old", "1"))
            fixture.clock.now += LEDGER_RETENTION_SECONDS + 1
            fixture.ack._entries = None
            fixture.stream(frame("new", "2"))
            fixture.ack.flush()
            keys = set(fixture.ack.ledger.load())
            self.assertEqual({"default new"}, keys)

    def test_menu_row_reports_the_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            rows, values = panel_rows(fixture.service, "http://127.0.0.1:1")
            row = next(row for row in rows if row.startswith("Delivery acks (event-ack/1)"))
            self.assertIn("on · none yet", row)
            self.assertEqual("__info__", values[rows.index(row)])
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            rows, _values = panel_rows(fixture.service, "http://127.0.0.1:1")
            row = next(row for row in rows if row.startswith("Delivery acks (event-ack/1)"))
            self.assertIn("acked 1 · waiting 0 · failed 0", row)
            summary = ledger_summary(Path(directory) / "event-acks.jsonl", fixture.clock)
            self.assertEqual(1, summary["events"])



class DeliveryAckReviewTests(unittest.TestCase):
    """Defects found in review (2026-10-15) and their fixes."""

    def _status(self, directory, *records):
        with (Path(directory) / "runtime-input-status.jsonl").open("a", encoding="utf-8") as stream:
            for request_id, status, at in records:
                stream.write(json.dumps({"request_id": request_id, "status": status, "updated_at_epoch": at}) + "\n")

    def test_same_event_id_replay_is_not_queued_again_after_the_dedupe_window(self):
        # The queue's own dedupe scans only recent rows; the ledger still knows the event.
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)  # admission always reports a new row
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            fixture.ack.flush()
            fixture.stream(frame("conv:41:message.approved:e1", "781"))
            self.assertEqual(1, len(fixture.submitted))
            fixture.ack.flush()
            ack = fixture.walkie.posts[-1]["body"]["acks"][0]
            self.assertEqual(("781", "already_queued"), (ack["stream_id"], ack["outcome"]))

    def test_replay_keeps_injection_evidence_and_its_injected_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            self._status(directory, (1, "queued", 1_800_000_000.0), (1, "submitted", 1_800_000_005.0))
            self.assertEqual(1, fixture.ack.scan_injections())
            fixture.ack.flush()
            fixture.stream(frame("e1", "781"))
            entry = fixture.ack.entries()["default e1"]
            self.assertEqual(event_delivery_ack.iso_utc(1_800_000_005.0), entry["injected_at"])
            self.assertEqual("acked", entry["injected_ack"]["status"])
            fixture.ack.flush()
            self.assertEqual(0, fixture.ack.scan_injections())
            stages = [a["stage"] for post in fixture.walkie.posts for a in post["body"]["acks"]]
            self.assertEqual(["queued", "injected", "queued"], stages)

    def test_a_resent_event_gets_its_own_injected_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("e1", "781"))
            redelivery = {"event_id": "e1", "stream_id": "781", "attempt": 1}
            fixture.stream(frame("e9", "790", redelivery_of=redelivery))
            fixture.ack.flush()
            self._status(directory, (1, "queued", 1_800_000_000.0), (1, "submitted", 1_800_000_009.0))
            self.assertEqual(2, fixture.ack.scan_injections())
            fixture.ack.flush()
            injected = sorted(a["stream_id"] for post in fixture.walkie.posts for a in post["body"]["acks"] if a["stage"] == "injected")
            self.assertEqual(["781", "790"], injected)

    def test_a_success_that_does_not_list_the_ack_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            class Silent(_Walkie):
                def __call__(self, request, timeout):
                    self.posts.append({"body": json.loads(request.data)})
                    return _AckResponse({} if len(self.posts) == 1 else {"acked": ["781"], "unknown": [], "rejected": []})

            fixture = _Fixture(directory, walkie=Silent())
            fixture.stream(frame("e1", "781"))
            self.assertEqual(0, fixture.ack.flush())
            self.assertEqual("pending", fixture.ack.entries()["default e1"]["queued_ack"]["status"])
            fixture.clock.now += 1.0
            self.assertEqual(1, fixture.ack.flush())
            self.assertEqual("acked", fixture.ack.entries()["default e1"]["queued_ack"]["status"])

    def test_a_ledger_that_cannot_be_written_keeps_the_cursor_back(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("e0", "780"))
            (Path(directory) / "event-acks.jsonl").unlink()
            (Path(directory) / "event-acks.jsonl").mkdir()  # every append now fails
            fixture.frames = [frame("e1", "781")]
            fixture.sse_requests.clear()
            fixture.service._stop.clear()
            log = fixture.service.log

            def stop_after_disconnect(level, text):
                log(level, text)
                if "external_event_sse_disconnected" in text:
                    fixture.service._stop.set()  # one attempt; no reconnect loop in the test

            fixture.service.log = stop_after_disconnect
            fixture.service._run_sse("default")
            self.assertEqual("780", json.loads((Path(directory) / "cursors.json").read_text())["default"])
            self.assertTrue(any("event_ack_ledger_write_failed" in text for _level, text in fixture.logs))
            self.assertTrue(any("external_event_sse_disconnected" in text for _level, text in fixture.logs))
            self.assertNotIn("default e1", fixture.ack.entries())

    def test_load_keeps_every_step_younger_than_14_days(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(directory)
            fixture.stream(frame("e1", "781"))
            fixture.ack.flush()
            lines_before = len(fixture.ledger())
            self.assertGreaterEqual(lines_before, 2)
            fixture.ack.ledger.load()
            self.assertEqual(lines_before, len(fixture.ledger()))


if __name__ == "__main__":
    unittest.main()
