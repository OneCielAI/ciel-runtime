"""Per-priority Work-fork models and the SSE side-fork model (2026-11-14).

Stored per provider in config.json, chosen from the current provider's models
only, projected to every settings surface and to work_parallel_status, where
the ciel-work-gate plugin reads them for each fork request.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_fork_models as models
from ciel_runtime_support.work_live_settings import settings
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_tree import WorkPlanError


def config_for(provider="cielairouter", current="_Walkie_Opus55", custom=("_Walkie_Fable5.2",), **extra):
    return {"current_provider": provider,
            "providers": {provider: {"current_model": current, "custom_models": list(custom)}}, **extra}


class ForkModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        patcher = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", Path(self.root.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_inherit_for_every_slot(self):
        config = config_for()
        self.assertEqual({p: "inherit" for p in (*models.PRIORITIES, "side")}, models.stored(config))
        view = models.projection(config)
        self.assertEqual({p: None for p in (*models.PRIORITIES, "side")}, view["models"])
        self.assertEqual({key: "inherit" for key in models.KEYS}, view["status"])

    def test_set_validates_against_current_provider_models_and_stores_per_provider(self):
        config = config_for()
        models.set_value(config, "work_parallel_model_p0", "_Walkie_Fable5.2")
        models.set_value(config, "work_side_intake_model", "_Walkie_Opus55")
        self.assertEqual({"cielairouter": {"P0": "_Walkie_Fable5.2", "side": "_Walkie_Opus55"}},
                         config["work_parallel_models"])
        with self.assertRaises(ValueError):
            models.set_value(config, "work_parallel_model_p1", "not-a-model")
        # Another provider keeps its own table.
        config["current_provider"] = "anthropic"
        config["providers"]["anthropic"] = {"current_model": "claude-opus-5-5"}
        self.assertEqual("inherit", models.value(config, "work_parallel_model_p0"))
        models.set_value(config, "work_parallel_model_p0", "claude-opus-5-5")
        self.assertEqual({"P0": "claude-opus-5-5"}, config["work_parallel_models"]["anthropic"])
        self.assertEqual({"P0": "_Walkie_Fable5.2", "side": "_Walkie_Opus55"},
                         config["work_parallel_models"]["cielairouter"])

    def test_reset_words_return_to_inherit_and_drop_empty_tables(self):
        config = config_for()
        models.set_value(config, "work_parallel_model_p3", "_Walkie_Fable5.2")
        for word in ("inherit", "default", "reset", "off", ""):
            models.set_value(config, "work_parallel_model_p3", "_Walkie_Fable5.2")
            models.set_value(config, "work_parallel_model_p3", word)
            self.assertNotIn("work_parallel_models", config)

    def test_no_provider_is_refused(self):
        with self.assertRaises(ValueError):
            models.set_value({}, "work_parallel_model_p0", "inherit")

    def test_projection_gives_router_alias_and_upstream_id(self):
        config = config_for()
        models.set_value(config, "work_parallel_model_p2", "_Walkie_Fable5.2")
        view = models.projection(config)
        self.assertEqual("ciel-runtime-cielairouter-_walkie_fable5.2", view["models"]["P2"])
        self.assertEqual("_Walkie_Fable5.2", view["upstream"]["P2"])
        self.assertEqual("_Walkie_Fable5.2", view["ids"]["P2"])
        self.assertEqual("active", view["status"]["work_parallel_model_p2"])
        self.assertFalse(view["forced_model"])

    def test_model_removed_from_provider_list_resolves_to_inherit(self):
        config = config_for()
        models.set_value(config, "work_parallel_model_p1", "_Walkie_Fable5.2")
        config["providers"]["cielairouter"]["custom_models"] = []
        view = models.projection(config)
        self.assertIsNone(view["models"]["P1"])
        self.assertEqual("unavailable_inherits", view["status"]["work_parallel_model_p1"])

    def test_forced_model_is_reported(self):
        config = config_for()
        models.set_value(config, "work_parallel_model_p1", "_Walkie_Fable5.2")
        config["providers"]["cielairouter"]["forced_model"] = "_Walkie_Opus55"
        self.assertEqual("forced_model_overrides", models.projection(config)["status"]["work_parallel_model_p1"])

    def test_catalog_registry_then_list_cache_supply_choices(self):
        root = Path(self.root.name)
        config = config_for(custom=())
        (root / "model-list-cache.json").write_text(json.dumps(
            {"key": json.dumps({"provider": "cielairouter"}), "models": ["_Cached_A"]}), encoding="utf-8")
        self.assertEqual(["_Walkie_Opus55", "_Cached_A"], models.model_ids(config))
        (root / "model-list-cache.json").write_text(json.dumps(
            {"key": json.dumps({"provider": "other"}), "models": ["_Other"]}), encoding="utf-8")
        self.assertEqual(["_Walkie_Opus55"], models.model_ids(config))
        (root / "model-registry.json").write_text(json.dumps(
            {"providers": {"cielairouter": {"models": ["_Reg_A", "_Walkie_Opus55"]}}}), encoding="utf-8")
        self.assertEqual(["_Walkie_Opus55", "_Reg_A"], models.model_ids(config))


class ForkModelSurfaceTests(unittest.TestCase):
    def test_settings_write_and_rows_on_the_shared_path(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / "ledger.json")
            config = config_for()
            saved = []
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=ledger), \
                 patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", Path(root)):
                result = settings(config, {"key": "work_parallel_model_p0", "value": "_Walkie_Fable5.2"},
                                  lambda c: saved.append(json.loads(json.dumps(c))))
                rows = {row["key"]: row for row in result["settings"]}
                self.assertEqual(("_Walkie_Fable5.2", "active"),
                                 (rows["work_parallel_model_p0"]["value"], rows["work_parallel_model_p0"]["status"]))
                self.assertEqual({"P0": "_Walkie_Fable5.2"}, saved[-1]["work_parallel_models"]["cielairouter"])
                with self.assertRaises(WorkPlanError):
                    settings(config, {"key": "work_side_intake_model", "value": "nope"}, lambda c: None)
                # Fingerprint covers the model rows.
                before = result["settings_fingerprint"]
                after = settings(config, {"key": "work_parallel_model_p0", "value": "inherit"}, lambda c: None)
                self.assertNotEqual(before, after["settings_fingerprint"])

    def test_status_projection_carries_fork_models_for_the_gate(self):
        from ciel_runtime_support.work_parallel_mcp import project, public_projection
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "ledger.json")
            config = config_for()
            with patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", Path(root)):
                models.set_value(config, "work_side_intake_model", "_Walkie_Fable5.2")
                view = project(store, "claude:s1", None, host_view=True, config=config)
            self.assertEqual("ciel-runtime-cielairouter-_walkie_fable5.2", view["fork_models"]["models"]["side"])
            self.assertEqual("_Walkie_Fable5.2", view["fork_models"]["upstream"]["side"])
            self.assertEqual(view["fork_models"], public_projection(view, "claude:s1")["fork_models"])
            self.assertNotIn("fork_models", project(store, "claude:s1", None, host_view=True))


if __name__ == "__main__":
    unittest.main()
