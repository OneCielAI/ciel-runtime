import unittest

from ciel_runtime_support.request_body_policy import (
    RequestBodyCapacityExceeded,
    RequestBodyTooLarge,
    RouterRequestBodyPolicy,
)
from ciel_runtime_support.request_limits_config import WorkspaceRequestLimits


def _limits(*, reference=100, inflight=1000):
    return WorkspaceRequestLimits(
        workspace="C:/work/alpha",
        model_request_max_bytes=100,
        chat_attachment_max_bytes=100,
        speech_audio_max_bytes=100,
        tts_reference_audio_max_bytes=reference,
        configured_inflight_request_max_bytes=inflight,
        inflight_request_max_bytes=inflight,
        sources={},
    )


class TransformedRequestAdmissionTests(unittest.TestCase):
    def test_nested_transformed_admission_reserves_only_five_times_delta(self):
        policy = RouterRequestBodyPolicy(environment={}, limits=_limits())

        with policy.admit("/v1/audio/speech", 10):
            self.assertEqual(50, policy.inflight_bytes)
            with policy.admit_transformed("/v1/audio/speech", 10, 100):
                self.assertEqual(500, policy.inflight_bytes)
            self.assertEqual(50, policy.inflight_bytes)

        self.assertEqual(0, policy.inflight_bytes)

    def test_transformed_admission_rejects_capacity_and_releases_outer_reservation(self):
        policy = RouterRequestBodyPolicy(
            environment={},
            limits=_limits(inflight=499),
        )

        with policy.admit("/v1/audio/speech", 10):
            with self.assertRaises(RequestBodyCapacityExceeded):
                with policy.admit_transformed("/v1/audio/speech", 10, 100):
                    pass
            self.assertEqual(50, policy.inflight_bytes)

        self.assertEqual(0, policy.inflight_bytes)

    def test_transformed_admission_validates_final_route_wire_limit(self):
        policy = RouterRequestBodyPolicy(
            environment={},
            limits=_limits(reference=3, inflight=10_000_000),
        )
        maximum = policy.limit_for("/v1/audio/speech")

        with self.assertRaises(RequestBodyTooLarge):
            with policy.admit_transformed(
                "/v1/audio/speech",
                10,
                maximum + 1,
            ):
                pass


if __name__ == "__main__":
    unittest.main()
