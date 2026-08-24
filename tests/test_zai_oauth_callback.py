import socket
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from ciel_runtime_support.zai_oauth_callback import ZaiOAuthLocalCallbackReceiver


class ZaiOAuthLocalCallbackReceiverTests(unittest.TestCase):
    def request(self, url):
        with urllib.request.urlopen(url, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")

    def test_receives_one_matching_loopback_callback_without_exposing_code(self):
        receiver = ZaiOAuthLocalCallbackReceiver("state-1", 1.0, port=0)

        with receiver:
            callback = (
                f"{receiver.redirect_uri}?code=private-code&state=state-1"
            )
            status, body = self.request(callback)
            received = receiver.wait()

        self.assertEqual(200, status)
        self.assertIn("authorization received", body.lower())
        self.assertEqual(callback, received)
        self.assertNotIn("private-code", body)

    def test_wrong_state_is_rejected_and_listener_keeps_waiting(self):
        receiver = ZaiOAuthLocalCallbackReceiver("expected", 0.05, port=0)

        with receiver:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(f"{receiver.redirect_uri}?code=private&state=wrong")
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                receiver.wait()

        self.assertEqual(400, raised.exception.code)

    def test_wrong_path_is_rejected(self):
        receiver = ZaiOAuthLocalCallbackReceiver("expected", 0.05, port=0)

        with receiver:
            parsed = urllib.parse.urlsplit(receiver.redirect_uri)
            wrong_path = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, "/wrong", "state=expected", "")
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(wrong_path)

        self.assertEqual(404, raised.exception.code)

    def test_occupied_port_fails_without_reusing_listener(self):
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        receiver = ZaiOAuthLocalCallbackReceiver("expected", 0.05, port=port)
        try:
            with self.assertRaisesRegex(RuntimeError, "could not bind"):
                with receiver:
                    self.fail("occupied port must not be reused")
        finally:
            occupied.close()

    def test_callback_wait_can_run_before_browser_request(self):
        receiver = ZaiOAuthLocalCallbackReceiver("expected", 5.0, port=0)
        result = []

        with receiver:
            waiter = threading.Thread(target=lambda: result.append(receiver.wait()))
            waiter.start()
            callback = f"{receiver.redirect_uri}?code=private&state=expected"
            self.request(callback)
            waiter.join(timeout=6.0)

        self.assertEqual([callback], result)


if __name__ == "__main__":
    unittest.main()
