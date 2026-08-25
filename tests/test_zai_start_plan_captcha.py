import json
import urllib.error
import urllib.request
import unittest

from ciel_runtime_support.zai_start_plan_captcha import (
    CAPTCHA_PARAM_HEADER,
    CAPTCHA_REGION_HEADER,
    SESSION_ID_HEADER,
    ZaiStartPlanCaptchaBroker,
    ZaiStartPlanCaptchaConfig,
    ZaiStartPlanRuntimeHeaderPreparer,
    _CaptchaResultReceiver,
)


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _ImmediateReceiver:
    def __init__(self, config, state, timeout, **_options):
        self.config = config
        self.state = state
        self.timeout = timeout
        self.url = f"http://localhost/captcha?state={state}"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def wait(self):
        return "captcha-result"


class _HeaderBroker:
    def __init__(self, calls):
        self.calls = calls

    def headers(self, options):
        self.calls.append(options)
        return {
            CAPTCHA_PARAM_HEADER: "fresh-result",
            CAPTCHA_REGION_HEADER: "sgp",
            SESSION_ID_HEADER: "fresh-session",
        }


class ZaiStartPlanCaptchaTests(unittest.TestCase):
    def test_official_client_config_shape_is_parsed(self):
        config = ZaiStartPlanCaptchaConfig.from_envelope(
            {
                "code": 0,
                "data": {
                    "configs": {
                        "captcha": {
                            "enabled": True,
                            "region": "sgp",
                            "prefix": "prefix-value",
                            "sceneId": "scene-value",
                        }
                    }
                },
            }
        )

        self.assertEqual("sgp", config.region)
        self.assertEqual("prefix-value", config.prefix)
        self.assertEqual("scene-value", config.scene_id)

    def test_broker_fetches_config_and_returns_request_scoped_headers(self):
        requested_urls = []

        def urlopen(request, timeout):
            requested_urls.append((request.full_url, timeout, request.get_header("User-agent")))
            return _JsonResponse(
                {
                    "code": 0,
                    "data": {
                        "configs": {
                            "captcha": {
                                "enabled": True,
                                "region": "sgp",
                                "prefix": "prefix-value",
                                "sceneId": "scene-value",
                            }
                        }
                    },
                }
            )

        broker = ZaiStartPlanCaptchaBroker(
            open_url=lambda _url: True,
            urlopen=urlopen,
            random_state=lambda: "fixed-state",
            receiver_factory=_ImmediateReceiver,
        )

        headers = broker.headers({"zcode_app_version": "3.9.1"})

        self.assertIn("/api/v1/client/configs?", requested_urls[0][0])
        self.assertIn("app_version=3.9.1", requested_urls[0][0])
        self.assertEqual("ZCode/3.9.1", requested_urls[0][2])
        self.assertEqual("captcha-result", headers[CAPTCHA_PARAM_HEADER])
        self.assertEqual("sgp", headers[CAPTCHA_REGION_HEADER])
        self.assertTrue(headers[SESSION_ID_HEADER])

    def test_runtime_preparer_only_runs_for_start_plan_and_replaces_stale_headers(self):
        preparer = ZaiStartPlanRuntimeHeaderPreparer()
        calls = []
        preparer.broker = _HeaderBroker(calls)
        stale = {
            "Authorization": "Bearer jwt",
            CAPTCHA_PARAM_HEADER.lower(): "stale-result",
            CAPTCHA_REGION_HEADER: "old-region",
            SESSION_ID_HEADER: "old-session",
        }

        untouched = preparer("zai-coding-plan", {}, stale)
        self.assertEqual([], calls)
        refreshed = preparer("zai-start-plan", {"zcode_app_version": "3.9.1"}, stale)

        self.assertEqual("Bearer jwt", untouched["Authorization"])
        self.assertNotIn(CAPTCHA_PARAM_HEADER, untouched)
        self.assertEqual(1, len(calls))
        self.assertEqual("fresh-result", refreshed[CAPTCHA_PARAM_HEADER])
        self.assertEqual("sgp", refreshed[CAPTCHA_REGION_HEADER])
        self.assertEqual("fresh-session", refreshed[SESSION_ID_HEADER])

    def test_loopback_receiver_rejects_wrong_state_and_accepts_one_result(self):
        config = ZaiStartPlanCaptchaConfig(True, "sgp", "prefix", "scene")
        receiver = _CaptchaResultReceiver(config, "expected-state", 2.0)
        with receiver:
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(receiver.url.replace("expected-state", "wrong"))
            self.assertEqual(403, rejected.exception.code)
            result_url = receiver.url.split("?", 1)[0] + "/result?state=expected-state"
            request = urllib.request.Request(
                result_url,
                data=b"verified-value",
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(204, response.status)
            self.assertEqual("verified-value", receiver.wait())


if __name__ == "__main__":
    unittest.main()
