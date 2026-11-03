import copy
import os
import unittest
from unittest import mock
from ciel_runtime_support.external_event_receiver import (
    ExternalEventReceiverService, MAX_WORK_DISCOVERY_INSTRUCTION_CHARS,
    TEST_WORK_DISCOVERY_INSTRUCTION, effective_work_discovery,
)
from ciel_runtime_support.external_event_menu import panel_rows, update_config

class WorkDiscoveryTests(unittest.TestCase):
    def receiver(self, **fields):
        return {"enabled": True, "transport": "sse", "url": "https://invalid.test/events",
                "discovery_enabled": True, "discovery_instruction": "Find work", **fields}

    def service(self):
        self.config = {"external_event_receivers": {"workspace": {"default": self.receiver()}}}
        vault = mock.Mock()
        vault.load.return_value = {}
        vault.status.return_value = {"stored_webhook_secret": False, "stored_authorization": False}
        return ExternalEventReceiverService(
            load_config=lambda: self.config, save_config=mock.Mock(),
            write_json=mock.Mock(), submit_event=mock.Mock(), vault=vault,
            workspace_key="workspace", log=mock.Mock(), urlopen=mock.Mock())

    def test_disabled_and_non_sse_sources(self):
        for fields in ({"enabled": False}, {"transport": "webhook"},
                       {"discovery_enabled": False}, {"discovery_enabled": "true"}):
            self.assertIsNone(effective_work_discovery({"default": self.receiver(**fields)}))
        self.assertIsNone(effective_work_discovery({"default": {"enabled": True, "transport": "sse"}}))

    def test_forced_default(self):
        expected = "작업큐에 여유가 생기면 와키에서 최근 미결 아이템이 있는지 확인하고 스스로 할 일을 찾아라. 우선순위 P1→P5, 같은 우선순위에서는 최신순으로 검색해서 픽업하라."
        self.assertEqual(expected, TEST_WORK_DISCOVERY_INSTRUCTION)
        result = effective_work_discovery({"default": self.receiver(
            test_default_override=True, discovery_instruction="%MISSING_DISCOVERY_TEST%")})
        self.assertEqual(expected, result["instruction"])
        self.assertTrue(result["used_test_default"])
        self.assertIsNone(effective_work_discovery({"default": self.receiver(
            test_default_override=True, discovery_enabled=False)}))

    def test_environment_fail_closed_and_bounds(self):
        with mock.patch.dict(os.environ, {"DISCOVERY_TEST": "Environment instruction"}, clear=True):
            for reference in ("%DISCOVERY_TEST%", "${DISCOVERY_TEST}", "{DISCOVERY_TEST}"):
                result = effective_work_discovery({"default": self.receiver(discovery_instruction=reference)})
                self.assertEqual("Environment instruction", result["instruction"])
            for text in ("%MISSING%", "", "  "):
                self.assertIsNone(effective_work_discovery({"default": self.receiver(discovery_instruction=text)}))
            os.environ["DISCOVERY_TEST"] = "x" * (MAX_WORK_DISCOVERY_INSTRUCTION_CHARS + 1)
            self.assertIsNone(effective_work_discovery({"default": self.receiver(discovery_instruction="%DISCOVERY_TEST%")}))

    def test_literal_deterministic_selection_and_no_mutation(self):
        config = {"z": self.receiver(), "a": self.receiver(discovery_instruction="literal ${NAME}")}
        original = copy.deepcopy(config)
        result = effective_work_discovery(config)
        self.assertEqual("a", result["receiver_id"])
        self.assertEqual("literal ${NAME}", result["instruction"])
        self.assertEqual(config["a"], result["config"])
        self.assertFalse(result["used_test_default"])
        self.assertEqual(original, config)
        config["a"]["discovery_instruction"] = "%MISSING%"
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(effective_work_discovery(config))

    def test_save_defaults_preservation_public_env_metadata(self):
        service = self.service()
        body = {"transport": "sse", "url": "https://invalid.test/events"}
        result = service.save_receiver("new", body)
        self.assertFalse(result["discovery_enabled"])
        self.assertFalse(result["test_default_override"])
        self.assertEqual("", result["discovery_instruction"])
        with mock.patch.dict(os.environ, {"DISCOVERY_TEST": "not public"}, clear=True):
            result = service.save_receiver("default", {**body, "discovery_instruction": "%DISCOVERY_TEST%"})
        self.assertTrue(result["discovery_enabled"])
        self.assertEqual("%DISCOVERY_TEST%", result["discovery_instruction"])
        self.assertEqual({"name": "DISCOVERY_TEST", "available": True}, result["environment_references"]["discovery_instruction"])
        self.assertNotIn("not public", str(result))
        service.urlopen.assert_not_called()
        self.assertEqual({}, service._threads)

    def test_invalid_types_and_bound(self):
        service = self.service()
        for fields in ({"discovery_enabled": "false"}, {"test_default_override": 1},
                       {"discovery_instruction": {}}, {"discovery_instruction": None},
                       {"discovery_instruction": "x" * (MAX_WORK_DISCOVERY_INSTRUCTION_CHARS + 1)}):
            with self.assertRaises(ValueError):
                service.save_receiver("default", {**self.receiver(), **fields})
        text = "x" * MAX_WORK_DISCOVERY_INSTRUCTION_CHARS
        self.assertEqual(text, service.save_receiver("default", self.receiver(discovery_instruction=text))["discovery_instruction"])

    def test_item_content_is_not_configuration(self):
        event = {"data": self.receiver(test_default_override=True)}
        self.assertIsNone(effective_work_discovery({"default": event}))
        self.assertEqual("Find work", effective_work_discovery({"default": self.receiver(data=event)})["instruction"])

    def test_menu_rows_and_editor(self):
        service = self.service()
        control = mock.Mock(return_value={"available": False})
        rows, values = panel_rows(service, "http://unused", control=control)
        self.assertEqual(len(rows), len(values))
        for key in ("discovery_enabled", "discovery_instruction", "test_default_override"):
            self.assertIn(key, values)
        update_config(service, "discovery_enabled", "toggle", control=control)
        update_config(service, "test_default_override", True, control=control)
        update_config(service, "discovery_instruction", "Configured from menu", control=control)
        saved = service.receiver_configs()["default"]
        self.assertFalse(saved["discovery_enabled"])
        self.assertTrue(saved["test_default_override"])
        self.assertEqual("Configured from menu", saved["discovery_instruction"])
        self.assertEqual("Configured from menu", control.call_args.args[1]["discovery_instruction"])
        service.urlopen.assert_not_called()
