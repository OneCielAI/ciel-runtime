import unittest
from unittest import mock

from ciel_runtime_support.event_receiver_connection import SseConnection, idle_timeout_seconds


class ConnectionTests(unittest.TestCase):
    def test_nonheartbeat_stream_has_no_watchdog_timeout(self):
        clock = mock.Mock(return_value=100.0)
        response = mock.Mock()
        connection = SseConnection(response, clock=clock)
        connection.activity(b"data: event\\n")
        clock.return_value = 10000.0
        self.assertFalse(connection.check_idle())
        self.assertFalse(connection.heartbeat_observed)

    def test_heartbeat_activity_enables_bounded_timeout(self):
        now = [100.0]
        response = mock.Mock()
        connection = SseConnection(response, clock=lambda: now[0])
        connection.activity(b": heartbeat\\n")
        now[0] = 160.1
        self.assertTrue(connection.check_idle())
        self.assertTrue(connection.timed_out)

    def test_explicit_timeout_applies_before_heartbeat(self):
        now = [100.0]
        response = mock.Mock()
        connection = SseConnection(response, 2.0, clock=lambda: now[0])
        now[0] = 102.1
        self.assertTrue(connection.check_idle())

    def test_timeout_configuration_is_bounded(self):
        self.assertIsNone(idle_timeout_seconds(None))
        self.assertEqual(45.0, idle_timeout_seconds("45"))
        with self.assertRaises(ValueError):
            idle_timeout_seconds(0)
        with self.assertRaises(ValueError):
            idle_timeout_seconds(301)


if __name__ == "__main__":
    unittest.main()
