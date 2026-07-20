import threading
import unittest

from ciel_runtime_support.credential_management import (
    CredentialManagementService,
    CredentialPersistencePorts,
    CredentialPresentationPorts,
    CredentialRotationRepository,
    ExternalCredentialPorts,
)
from ciel_runtime_support.credentials import api_key_clear_requested, parse_api_key_list


class CredentialManagementServiceTest(unittest.TestCase):
    def service(self, config, *, external_provider=""):
        saved = []
        cleared = []
        external_keys = []
        cursor = {"target": 2}
        service = CredentialManagementService(
            persistence=CredentialPersistencePorts(
                load_config=lambda: config,
                save_config=lambda value: saved.append(value),
                clear_model_cache=lambda: cleared.append(True),
                parse_keys=parse_api_key_list,
                clear_requested=api_key_clear_requested,
                rotation_name=lambda provider, pcfg: "target",
            ),
            external=ExternalCredentialPorts(
                enabled=frozenset({external_provider}).__contains__,
                store=lambda key: external_keys.append(key),
                clear=lambda: external_keys.clear(),
                has_key=lambda: bool(external_keys),
                normalize_provider_config=lambda pcfg: False,
                location="external.env",
            ),
            presentation=CredentialPresentationPorts(lambda value: "masked", lambda value: "fingerprint"),
            rotation=CredentialRotationRepository(cursor, threading.Lock()),
            config_location="config.json",
        )
        return service, saved, cleared, external_keys, cursor

    def test_store_many_persists_keys_and_resets_rotation(self):
        config = {"providers": {"deepseek": {}}}
        service, saved, cleared, _external, cursor = self.service(config)

        lines = service.store_many("deepseek", ["sk-one", "sk-two"])

        self.assertEqual(["sk-one", "sk-two"], config["providers"]["deepseek"]["api_keys"])
        self.assertEqual({}, cursor)
        self.assertEqual(1, len(saved))
        self.assertEqual(1, len(cleared))
        self.assertIn("Round-robin: enabled", lines)

    def test_clear_preserves_other_provider_credentials(self):
        config = {
            "providers": {
                "deepseek": {"api_key": "remove", "api_keys": ["remove", "remove-2"]},
                "other": {"api_key": "keep", "api_keys": ["keep", "keep-2"]},
            }
        }
        service, _saved, _cleared, _external, cursor = self.service(config)

        lines = service.clear("deepseek")

        self.assertNotIn("api_key", config["providers"]["deepseek"])
        self.assertEqual("keep", config["providers"]["other"]["api_key"])
        self.assertEqual(["keep", "keep-2"], config["providers"]["other"]["api_keys"])
        self.assertEqual({}, cursor)
        self.assertIn("Other providers unchanged", lines[0])


if __name__ == "__main__":
    unittest.main()
