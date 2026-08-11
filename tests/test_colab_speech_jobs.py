import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.colab_speech_jobs import ColabSpeechJobManager, PortableEncryptedSecretStore


class _Process:
    def __init__(self, return_code=None):
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code


class _MemorySecretStore:
    def __init__(self):
        self.values = {}

    def save(self, profile, secrets):
        self.values.setdefault(profile, {}).update(secrets)

    def load(self, profile):
        return dict(self.values.get(profile, {}))

    def clear(self, profile):
        self.values.pop(profile, None)

    def status(self, profile):
        values = self.values.get(profile, {})
        return {
            "stored_tailscale_auth_key": bool(values.get("tailscale_auth_key")),
            "stored_speech_api_key": bool(values.get("speech_api_key")),
        }


class ColabSpeechJobManagerTests(unittest.TestCase):
    def test_deploy_script_treats_not_found_output_as_a_missing_session(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "deploy_colab_speech.ps1"
        source = script.read_text(encoding="utf-8")

        self.assertIn("$sessionMissing = $statusOutput -match", source)
        self.assertIn("session\\s+.+\\s+not found", source)
        self.assertIn("$statusExit -eq 0 -and -not $sessionMissing", source)
        self.assertIn("Write-Host $asrOutput", source)
        self.assertIn("$ErrorActionPreference = 'Continue'", source)
        self.assertIn("$asrFailed = $asrExitCode -ne 0 -or $asrOutput -match", source)
        self.assertIn("@('exec', '--session', $AsrSession, '--timeout', '1800')", source)
        self.assertIn("function New-EphemeralBootstrap", source)
        self.assertIn("Remove-Item \"Env:$secretName\"", source)
        self.assertNotIn("@('--env', \"TAILSCALE_AUTHKEY=", source)
        self.assertIn("[Text.UTF8Encoding]::new($false)", source)
        self.assertIn("ASR session became stale; creating a replacement and retrying once", source)
        self.assertIn("TTS session became stale; creating a replacement and retrying once", source)
        self.assertIn("$asrExitCode = $LASTEXITCODE", source)
        self.assertIn("function Release-ColabEndpoint", source)
        self.assertIn("state.client.unassign", source)
        self.assertIn('Release-ColabEndpoint $asrKnownEndpoint "ASR"', source)

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
            store = _MemorySecretStore()
            manager = ColabSpeechJobManager(script, root / "jobs", store)
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
            self.assertTrue(manager.credential_status("personal")["stored_tailscale_auth_key"])

    def test_job_status_classifies_colab_capacity_errors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "deploy.ps1"
            script.write_text("ready", encoding="utf-8")
            manager = ColabSpeechJobManager(script, root / "jobs", _MemorySecretStore())
            process = _Process(return_code=1)
            with mock.patch("ciel_runtime_support.colab_speech_jobs.os.name", "nt"), mock.patch(
                "ciel_runtime_support.colab_speech_jobs.subprocess.Popen", return_value=process
            ):
                launched = manager.launch("deploy", {"profile": "personal"}, {})
            job_id = launched["job"]["id"]
            (root / "jobs" / f"{job_id}.log").write_text("TooManyAssignmentsError: Precondition Failed", encoding="utf-8")

            self.assertEqual("colab_capacity", manager.status(job_id)["job"]["failure_kind"])

    def test_saved_credentials_are_reused_and_can_be_forgotten(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "deploy.ps1"
            script.write_text("ready", encoding="utf-8")
            store = _MemorySecretStore()
            manager = ColabSpeechJobManager(script, root / "jobs", store)
            process = _Process()
            with mock.patch("ciel_runtime_support.colab_speech_jobs.os.name", "nt"), mock.patch(
                "ciel_runtime_support.colab_speech_jobs.subprocess.Popen", return_value=process
            ) as popen:
                manager.launch("deploy", {"profile": "personal"}, {"tailscale_auth_key": "saved-key"})
                manager._jobs.clear()
                manager.launch("deploy", {"profile": "personal"}, {})
                self.assertEqual("saved-key", popen.call_args.kwargs["env"]["TAILSCALE_AUTHKEY"])
                manager._jobs.clear()
                manager.launch("start", {"profile": "personal"}, {"forget_saved_credentials": "1"})

            self.assertFalse(manager.credential_status("personal")["stored_tailscale_auth_key"])

    def test_portable_store_keeps_only_ciphertext_and_overwrites_one_profile_slot(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "credentials.json"
            store = PortableEncryptedSecretStore(path, master_key=b"x" * 32)

            store.save("default", {"tailscale_auth_key": "first-secret"})
            store.save("default", {"tailscale_auth_key": "second-secret"})

            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("first-secret", raw)
            self.assertNotIn("second-secret", raw)
            self.assertEqual("second-secret", store.load("default")["tailscale_auth_key"])
            self.assertEqual(1, len(__import__("json").loads(raw)["profiles"]))

    def test_rejects_shell_metacharacters_in_profile(self):
        manager = ColabSpeechJobManager(Path("deploy.ps1"), Path("jobs"))

        with self.assertRaises(ValueError):
            manager.login_command({"profile": "personal; reboot"})

    def test_asr_bootstrap_cleans_orphaned_gpu_workers_before_restart(self):
        bootstrap = Path(__file__).resolve().parents[1] / "scripts" / "colab" / "bootstrap_qwen_asr.py"
        source = bootstrap.read_text(encoding="utf-8")

        self.assertIn("def stop_existing_server()", source)
        self.assertIn("nvidia-smi --query-compute-apps=pid", source)
        self.assertIn("stop_existing_server()", source)

    def test_bootstraps_can_reuse_existing_tailscale_state_without_a_new_key(self):
        root = Path(__file__).resolve().parents[1] / "scripts" / "colab"
        for name in ("bootstrap_qwen_asr.py", "bootstrap_cosyvoice3.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn('auth_key = secret("TAILSCALE_AUTHKEY")', source)
            self.assertIn('status_data.get("BackendState") == "Running"', source)
            self.assertIn("elif auth_key:", source)


if __name__ == "__main__":
    unittest.main()
