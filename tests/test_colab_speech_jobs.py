import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.colab_speech_jobs import ColabSpeechJobManager


class _Process:
    def __init__(self, return_code=None):
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code


class ColabSpeechJobManagerTests(unittest.TestCase):
    def test_deploy_script_treats_not_found_output_as_a_missing_session(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "deploy_colab_speech.ps1"
        source = script.read_text(encoding="utf-8")

        self.assertIn("$sessionMissing = $statusOutput -match", source)
        self.assertIn("session\\s+.+\\s+not found", source)
        self.assertIn("$statusExit -eq 0 -and -not $sessionMissing", source)
        self.assertIn("Write-Host $asrOutput", source)
        self.assertIn("$ErrorActionPreference = 'Continue'", source)
        self.assertIn("$asrFailed = $LASTEXITCODE -ne 0 -or $asrOutput -match", source)
        self.assertIn("@('exec', '--session', $AsrSession, '--timeout', '1800')", source)
        self.assertIn("function New-EphemeralBootstrap", source)
        self.assertIn("Remove-Item \"Env:$secretName\"", source)
        self.assertNotIn("@('--env', \"TAILSCALE_AUTHKEY=", source)

    def test_login_command_uses_isolated_profile_and_can_reset_authentication(self):
        manager = ColabSpeechJobManager(Path("C:/runtime/scripts/deploy_colab_speech.ps1"), Path("C:/state"))

        command = manager.login_command(
            {"profile": "second-account", "distribution": "Ubuntu-26.04", "auth": "oauth2"},
            reset=True,
        )

        self.assertIn('-Action Login', command)
        self.assertIn('-Profile "second-account"', command)
        self.assertIn('-ResetAuthentication', command)

    @unittest.skipUnless(os.name == "nt", "Windows job launcher")
    def test_deploy_runs_in_background_without_exposing_secrets_in_command_or_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "scripts" / "deploy_colab_speech.ps1"
            script.parent.mkdir()
            script.write_text("Write-Host ready", encoding="utf-8")
            manager = ColabSpeechJobManager(script, root / "jobs")
            process = _Process()
            with mock.patch("ciel_runtime_support.colab_speech_jobs.subprocess.Popen", return_value=process) as popen:
                launched = manager.launch(
                    "deploy",
                    {"profile": "personal"},
                    {"tailscale_auth_key": "tskey-auth-secret", "speech_api_key": "voice-secret"},
                )

            command = popen.call_args.args[0]
            environment = popen.call_args.kwargs["env"]
            self.assertNotIn("tskey-auth-secret", " ".join(command))
            self.assertNotIn("voice-secret", " ".join(command))
            self.assertEqual("tskey-auth-secret", environment["TAILSCALE_AUTHKEY"])
            self.assertTrue(launched["job"]["running"])
            job_id = launched["job"]["id"]
            (root / "jobs" / f"{job_id}.log").write_text("key tskey-auth-secret\n", encoding="utf-8")
            self.assertNotIn("tskey-auth-secret", manager.status(job_id)["job"]["output"])

    def test_rejects_shell_metacharacters_in_profile(self):
        manager = ColabSpeechJobManager(Path("deploy.ps1"), Path("jobs"))

        with self.assertRaises(ValueError):
            manager.login_command({"profile": "personal; reboot"})


if __name__ == "__main__":
    unittest.main()
