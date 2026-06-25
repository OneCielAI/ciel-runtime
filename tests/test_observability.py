import unittest

from ciel_runtime_support.observability import EventBus, EventConfig


class ObservabilityTests(unittest.TestCase):
    def test_event_bus_filters_by_level_and_category(self):
        bus = EventBus(EventConfig(enabled=True, level="debug", buffer_size=10))
        self.assertIsNone(bus.publish(level="trace", category="router.trace", message="too noisy"))
        bus.publish(level="debug", category="router.request", message="request")
        bus.publish(level="warn", category="upstream.retry", message="retry")

        self.assertEqual(2, len(bus.recent()))
        self.assertEqual(1, len(bus.recent(level="warn")))
        self.assertEqual("upstream.retry", bus.recent(category="upstream")[0]["category"])

    def test_event_bus_redacts_secret_fields(self):
        bus = EventBus(EventConfig(enabled=True, level="trace", buffer_size=10))
        bus.publish(
            level="info",
            category="config",
            message="loaded",
            data={"api_key": "secret", "nested": {"Authorization": "Bearer secret"}, "safe": "ok"},
        )

        event = bus.recent()[0]
        self.assertEqual("[redacted]", event["data"]["api_key"])
        self.assertEqual("[redacted]", event["data"]["nested"]["Authorization"])
        self.assertEqual("ok", event["data"]["safe"])


if __name__ == "__main__":
    unittest.main()

