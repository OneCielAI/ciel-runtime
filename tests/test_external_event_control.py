import json
import unittest
import urllib.error

from ciel_runtime_support.external_event_control import ExternalEventControl


class _Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _limit):
        return self.raw


class ExternalEventControlTests(unittest.TestCase):
    def control(self, workspace="C:/work/a", health_workspace="C:/work/a"):
        self.seen = []

        def opener(request, timeout):
            self.seen.append((request.full_url, request.get_method(), dict(request.header_items())))
            if request.full_url.endswith("/health"):
                return _Response({"ok": True, "workspace": health_workspace})
            if request.full_url.endswith("/ca/events/receivers"):
                return _Response({"ok": True, "receivers": [{"id": "default", "status": {"state": "connected"}}]})
            return _Response({"ok": True})

        return ExternalEventControl("http://127.0.0.1:1", workspace, {"Authorization": "Bearer secret-token"},
                                    open_request=opener)

    def test_status_requires_the_same_workspace_router(self):
        self.assertEqual({"available": True, "status": {"state": "connected"}}, self.control().call("status"))
        mismatch = self.control(health_workspace="C:/work/other").call("status")
        self.assertEqual({"available": False, "reason": "workspace_mismatch"}, mismatch)

    def test_reconnect_only_posts_the_default_receiver_action(self):
        control = self.control()
        self.assertTrue(control.call("reconnect")["accepted"])
        self.assertEqual("http://127.0.0.1:1/ca/events/receivers/default/reconnect", self.seen[-1][0])
        self.assertEqual("POST", self.seen[-1][1])

    def test_unreachable_router_never_leaks_exception_text(self):
        def opener(_request, timeout):
            raise OSError("https://endpoint.invalid/stream?token=secret-token")

        result = ExternalEventControl("http://127.0.0.1:1", "C:/work/a", open_request=opener).call("status")
        self.assertEqual({"available": False, "reason": "router_unavailable"}, result)
        self.assertNotIn("secret-token", repr(result))

    def test_rejected_reconnect_is_not_router_unavailability(self):
        control = self.control()
        original = control.open_request
        def opener(request, timeout):
            if request.full_url.endswith("/reconnect"):
                raise urllib.error.HTTPError(request.full_url, 409, "private", {}, None)
            return original(request, timeout)
        control.open_request = opener
        self.assertEqual({"available": True, "accepted": False, "reason": "receiver_unavailable"},
                         control.call("reconnect"))

    def test_unknown_actions_are_not_sent(self):
        result = self.control().call("restart")
        self.assertEqual({"available": False, "reason": "unsupported_action"}, result)
        self.assertEqual([], self.seen)


if __name__ == "__main__":
    unittest.main()
