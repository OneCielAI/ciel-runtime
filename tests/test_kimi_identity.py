import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import kimi_identity


class KimiIdentityTests(unittest.TestCase):
    def _home(self, root: str, *, expires_at: int) -> Path:
        home = Path(root)
        code_home = home / ".kimi-code"
        credentials = code_home / "credentials"
        credentials.mkdir(parents=True)
        (credentials / "kimi-code.json").write_text(
            json.dumps(
                {
                    "access_token": "expired-access",
                    "refresh_token": "refresh-value",
                    "expires_at": expires_at,
                    "expires_in": 900,
                }
            ),
            encoding="utf-8",
        )
        (code_home / "config.toml").write_text(
            '[providers."managed:kimi-code"]\napi_key = ""\n'
            '[providers."managed:kimi-code".oauth]\nstorage = "file"\n',
            encoding="utf-8",
        )
        return home

    def test_refresh_token_keeps_oauth_configured_after_access_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            home = self._home(root, expires_at=1)
            self.assertTrue(kimi_identity.oauth_configured(home))

    def test_expired_access_token_is_refreshed_and_saved(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "access_token": "fresh-access",
                        "refresh_token": "fresh-refresh",
                        "expires_in": 900,
                        "token_type": "Bearer",
                    }
                ).encode()

        with tempfile.TemporaryDirectory() as root:
            home = self._home(root, expires_at=1)
            with (
                patch.object(kimi_identity, "identity_headers", return_value={}),
                patch.object(kimi_identity.urllib.request, "urlopen", return_value=Response()) as urlopen,
            ):
                token = kimi_identity.oauth_access_token(home)

            self.assertEqual("fresh-access", token)
            request = urlopen.call_args.args[0]
            self.assertEqual("https://auth.kimi.com/api/oauth/token", request.full_url)
            saved = kimi_identity.oauth_token_record(home)
            self.assertEqual("fresh-access", saved["access_token"])
            self.assertEqual("fresh-refresh", saved["refresh_token"])
            self.assertGreater(saved["expires_at"], time.time())
            if os.name != "nt":
                self.assertEqual(
                    0o600,
                    (home / ".kimi-code/credentials/kimi-code.json").stat().st_mode
                    & 0o777,
                )


if __name__ == "__main__":
    unittest.main()
