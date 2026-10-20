from contextlib import contextmanager, nullcontext
from dataclasses import replace
import errno
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy, exclusive_file_lock
from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
from ciel_runtime_support.channel_wake_claim_repository import (
    ChannelWakeClaimRepository,
    prompt_message_ids,
    prompt_references_message_id,
)


class ChannelWakeClaimRepositoryTests(unittest.TestCase):
    def repository(self, path: Path, now: list[float]) -> ChannelWakeClaimRepository:
        return ChannelWakeClaimRepository(
            path=path,
            file_lock=nullcontext,
            now=lambda: now[0],
            ttl_seconds=lambda: 30.0,
            log=lambda _level, _message: None,
        )

    def test_prompt_reference_supports_ids_and_normalized_claim_text(self):
        self.assertEqual({7, 8}, prompt_message_ids("pending_ids=7, 8"))
        self.assertTrue(prompt_references_message_id("wake id=9", 9))
        self.assertTrue(prompt_references_message_id("hello   world", 4, ["hello world"]))

    def test_visible_wake_ignores_id_like_text_in_untrusted_body(self):
        prompt = (
            "[ciel-wake] pending_ids=1218\n\n"
            "original body says id=999 and pending_ids=1000"
        )

        self.assertEqual({1218}, prompt_message_ids(prompt))

    def test_claim_is_exclusive_and_clear_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self.repository(Path(tmp) / "claims.json", [100.0])
            self.assertTrue(repository.claim(7, "wake id=7"))
            self.assertFalse(repository.claim(7, "wake id=7"))
            self.assertEqual("wake id=7", repository.prompt(7))
            repository.clear(7)
            self.assertTrue(repository.claim(7, "wake id=7"))

    def test_injector_releases_only_owned_claims_after_file_acquisition_failure(self):
        from test_channel_pending_injection import _InjectionHarness, _message

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claims.json"
            durable = self.repository(path, [100.0])
            self.assertTrue(durable.claim(77, "unrelated"))
            calls = [0]

            @contextmanager
            def file_lock():
                calls[0] += 1
                if calls[0] == 2:
                    raise ChannelFileLockBusy(errno.EACCES, "injected pre-acquire contention")
                with exclusive_file_lock(path):
                    yield

            repository = replace(durable, file_lock=file_lock)
            harness = _InjectionHarness([_message(40), _message(41)])
            base = harness.services()
            services = replace(
                base,
                wake_store=replace(base.wake_store, claim_prompt=repository.claim, clear_claim=repository.clear),
                policy=replace(base.policy, wake_batch_limit=lambda: 10),
            )
            with self.assertRaises(ChannelFileLockBusy):
                inject_pending_channel_messages(99, 39, services=services, wake_for_llm_delivery=True)
            self.assertEqual("", durable.prompt(40))
            self.assertEqual("", durable.prompt(41))
            self.assertEqual("unrelated", durable.prompt(77))
            self.assertEqual([], harness.written)
            self.assertFalse(harness.delivery.failed)
            returned = inject_pending_channel_messages(99, 39, services=services, wake_for_llm_delivery=True)
            self.assertEqual(39, returned)
            self.assertEqual(["body-40 body-41"], harness.written)
            self.assertEqual("body-40 body-41", durable.prompt(40))
            self.assertEqual("body-40 body-41", durable.prompt(41))
            self.assertEqual("unrelated", durable.prompt(77))

    def test_injector_releases_first_claim_when_second_is_already_owned(self):
        from test_channel_pending_injection import _InjectionHarness, _message

        with tempfile.TemporaryDirectory() as tmp:
            repository = self.repository(Path(tmp) / "claims.json", [100.0])
            self.assertTrue(repository.claim(41, "another scan"))
            harness = _InjectionHarness([_message(40), _message(41)])
            base = harness.services()
            services = replace(
                base,
                wake_store=replace(base.wake_store, claim_prompt=repository.claim, clear_claim=repository.clear),
                policy=replace(base.policy, wake_batch_limit=lambda: 10),
            )
            returned = inject_pending_channel_messages(99, 39, services=services, wake_for_llm_delivery=True)
            self.assertEqual(39, returned)
            self.assertEqual("", repository.prompt(40))
            self.assertEqual("another scan", repository.prompt(41))
            self.assertEqual([], harness.written)
            self.assertFalse(harness.delivery.failed)

    def test_expired_claim_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            repository = self.repository(Path(tmp) / "claims.json", now)
            self.assertTrue(repository.claim(8, "old"))
            now[0] = 131.0
            self.assertEqual("", repository.prompt(8))
            self.assertTrue(repository.claim(8, "new"))


if __name__ == "__main__":
    unittest.main()
