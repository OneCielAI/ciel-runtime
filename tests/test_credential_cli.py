import unittest
from types import SimpleNamespace

from ciel_runtime_support.credential_cli import (
    CredentialCliController,
    CredentialCliIO,
    CredentialCliPolicy,
    CredentialCliPorts,
)


class CredentialCliControllerTest(unittest.TestCase):
    def controller(self, *, isatty=False):
        output = []
        calls = []
        config = {"providers": {"required": {"api_key": "secret"}, "optional": {}}}
        controller = CredentialCliController(
            CredentialCliPolicy(frozenset({"required"})),
            CredentialCliPorts(
                normalize_provider=lambda value: value,
                load_config=lambda: config,
                key_count=lambda provider, pcfg: 1 if pcfg.get("api_key") else 0,
                primary_key=lambda provider, pcfg: pcfg.get("api_key", ""),
                mask=lambda value: "masked",
                fingerprint=lambda value: "fingerprint",
                clear_requested=lambda value: value == "clear",
                clear=lambda provider: calls.append(("clear", provider)) or ["cleared"],
                store_input=lambda provider, key: calls.append(("store", provider, key)) or ["stored"],
                store_many=lambda provider, keys: calls.append(("many", provider, keys)) or ["stored many"],
            ),
            CredentialCliIO(lambda: isatty, lambda prompt: "prompted-key", output.append),
        )
        return controller, output, calls

    def test_status_projects_required_and_optional_providers(self):
        controller, output, _calls = self.controller()

        controller.manage(SimpleNamespace(provider=None))

        self.assertTrue(any("required" in line and "set" in line for line in output))
        self.assertTrue(any("optional" in line and "not required" in line for line in output))
        self.assertFalse(any("secret" in line for line in output))

    def test_non_tty_manage_refuses_secret_prompt(self):
        controller, output, calls = self.controller(isatty=False)

        controller.manage(SimpleNamespace(provider="required", action=""))

        self.assertEqual([], calls)
        self.assertTrue(any("do not paste API keys" in line for line in output))


if __name__ == "__main__":
    unittest.main()
