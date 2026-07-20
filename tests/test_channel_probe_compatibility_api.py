import unittest
from pathlib import Path

from ciel_runtime_support.channel_probe_cache import ChannelProbeCompatibilityApi


class FakeRepository:
    def __init__(self):
        self.cache = {"servers": []}

    def read(self):
        return self.cache

    def write(self, cache):
        self.cache = cache


class FakeProbeService:
    def __init__(self, marker):
        self.marker = marker
        self.repository = FakeRepository()

    def builtin_record(self):
        return {"name": self.marker}

    def transport_label(self, server):
        return server.get("type", "unknown")

    def probe(self, paths, cwd, **options):
        return [{"paths": list(paths), "cwd": cwd, **options}]

    def refresh(self, *args):
        return {"args": args}

    def servers(self):
        return [{"name": self.marker, "capable": True}]

    def bucket(self, record):
        return "capable" if record.get("capable") else "inconclusive"

    def capable_names(self):
        return [self.marker]

    def external_capable_names(self):
        return [self.marker]

    def source_paths(self, specs):
        return [Path(spec) for spec in specs]

    def server_names_from_specs(self, specs):
        return [spec.split(":", 1)[-1] for spec in specs]


class ChannelProbeCompatibilityApiTests(unittest.TestCase):
    def test_explicit_adapter_projects_repository_and_service_methods(self):
        service = FakeProbeService("server-a")
        api = ChannelProbeCompatibilityApi(lambda: service)
        self.assertEqual("server-a", api.builtin_record()["name"])
        self.assertEqual("stdio", api.transport_label({"type": "stdio"}))
        self.assertEqual(["server-a"], api.capable_names())
        self.assertEqual([Path("config.json")], api.source_paths(["config.json"]))
        api.write_cache({"servers": [{"name": "cached"}]})
        self.assertEqual("cached", api.read_cache()["servers"][0]["name"])

    def test_service_factory_is_resolved_per_call(self):
        marker = ["first"]
        api = ChannelProbeCompatibilityApi(lambda: FakeProbeService(marker[0]))
        self.assertEqual("first", api.builtin_record()["name"])
        marker[0] = "second"
        self.assertEqual("second", api.builtin_record()["name"])


if __name__ == "__main__":
    unittest.main()
