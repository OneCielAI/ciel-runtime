import copy
import io
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
import tempfile
from pathlib import Path
from unittest import mock

import ciel_runtime

from ciel_runtime_support.zai_start_plan_captcha import (
    ALIYUN_CAPTCHA_SDK_URL,
    CAPTCHA_PARAM_HEADER,
    CAPTCHA_REGION_HEADER,
    _CaptchaResultReceiver,
    ZaiStartPlanCaptchaBroker,
    ZaiStartPlanCaptchaConfig,
    apply_zai_start_plan_runtime_headers,
)
from ciel_runtime_support.runtime_interaction import RuntimeInteractionRepository


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ZaiStartPlanCaptchaTests(unittest.TestCase):
    def test_server_config_contract_is_parsed(self):
        config = ZaiStartPlanCaptchaConfig.from_envelope(
            {
                "code": 0,
                "data": {
                    "configs": {
                        "captcha": {
                            "enabled": True,
                            "region": "sgp",
                            "prefix": "no8xfe",
                            "sceneId": "11xygtvd",
                        }
                    }
                },
            }
        )

        self.assertEqual("sgp", config.region)
        self.assertEqual("no8xfe", config.prefix)
        self.assertEqual("11xygtvd", config.scene_id)

    def test_broker_serves_official_sdk_and_receives_state_bound_result(self):
        opened = []

        def config_urlopen(request, timeout):
            self.assertEqual(15.0, timeout)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            self.assertEqual(["3.8.1"], query["app_version"])
            return _JsonResponse(
                {
                    "code": 0,
                    "data": {
                        "configs": {
                            "captcha": {
                                "enabled": True,
                                "region": "sgp",
                                "prefix": "no8xfe",
                                "sceneId": "11xygtvd",
                            }
                        }
                    },
                }
            )

        def open_url(url):
            opened.append(url)
            with urllib.request.urlopen(url, timeout=2.0) as response:
                page = response.read().decode("utf-8")
            self.assertIn(ALIYUN_CAPTCHA_SDK_URL, page)
            self.assertIn('"region": "sgp"', page)
            parsed = urllib.parse.urlsplit(url)
            state = urllib.parse.parse_qs(parsed.query)["state"][0]
            result_url = (
                f"http://localhost:{parsed.port}/zai-start-plan-captcha/result?"
                + urllib.parse.urlencode({"state": state})
            )

            request = urllib.request.Request(
                result_url,
                data=b'{"certifyId":"one-time"}',
                method="POST",
                headers={"Content-Type": "text/plain"},
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                self.assertEqual(204, response.status)
            return True

        broker = ZaiStartPlanCaptchaBroker(
            open_url=open_url,
            urlopen=config_urlopen,
            random_state=lambda: "state-1",
        )

        headers = broker.headers({"zcode_app_version": "3.8.1"})

        self.assertEqual(1, len(opened))
        self.assertEqual('{"certifyId":"one-time"}', headers[CAPTCHA_PARAM_HEADER])
        self.assertEqual("sgp", headers[CAPTCHA_REGION_HEADER])

    def test_remote_receiver_uses_configured_bind_host_port_and_public_url(self):
        opened = []

        def config_urlopen(_request, timeout):
            self.assertEqual(15.0, timeout)
            return _JsonResponse(
                {
                    "code": 0,
                    "data": {
                        "configs": {
                            "captcha": {
                                "enabled": True,
                                "region": "sgp",
                                "prefix": "no8xfe",
                                "sceneId": "11xygtvd",
                            }
                        }
                    },
                }
            )

        def open_url(url):
            opened.append(url)
            parsed = urllib.parse.urlsplit(url)
            state = urllib.parse.parse_qs(parsed.query)["state"][0]
            local_url = urllib.parse.urlunsplit(
                (
                    "http",
                    f"127.0.0.1:{parsed.port}",
                    "/zai-start-plan-captcha/result",
                    urllib.parse.urlencode({"state": state}),
                    "",
                )
            )
            request = urllib.request.Request(
                local_url,
                data=b"remote-browser-result",
                method="POST",
                headers={"Content-Type": "text/plain"},
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                self.assertEqual(204, response.status)
            return False

        broker = ZaiStartPlanCaptchaBroker(
            open_url=open_url,
            urlopen=config_urlopen,
            random_state=lambda: "remote-state",
        )
        headers = broker.headers(
            {
                "zai_captcha_bind_host": "127.0.0.1",
                "zai_captcha_port": 0,
                "zai_captcha_public_base_url": "http://100.95.132.58:{port}",
            }
        )

        self.assertRegex(
            opened[0],
            r"^http://100\.95\.132\.58:\d+/zai-start-plan-captcha\?state=remote-state$",
        )
        self.assertEqual("remote-browser-result", headers[CAPTCHA_PARAM_HEADER])

    def test_broker_exposes_pending_url_then_marks_verification_completed(self):
        observations = []

        def config_urlopen(_request, timeout):
            self.assertEqual(15.0, timeout)
            return _JsonResponse(
                {
                    "code": 0,
                    "data": {
                        "configs": {
                            "captcha": {
                                "enabled": True,
                                "region": "sgp",
                                "prefix": "no8xfe",
                                "sceneId": "11xygtvd",
                            }
                        }
                    },
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            interactions = RuntimeInteractionRepository(
                Path(directory) / "runtime-interaction.json"
            )

            def open_url(url):
                pending = interactions.read()
                observations.append(pending)
                self.assertIsNotNone(pending)
                self.assertEqual("pending", pending.status)
                self.assertEqual(url, pending.url)
                parsed = urllib.parse.urlsplit(url)
                state = urllib.parse.parse_qs(parsed.query)["state"][0]
                result_url = urllib.parse.urlunsplit(
                    (
                        "http",
                        f"localhost:{parsed.port}",
                        "/zai-start-plan-captcha/result",
                        urllib.parse.urlencode({"state": state}),
                        "",
                    )
                )
                request = urllib.request.Request(
                    result_url,
                    data=b"verified",
                    method="POST",
                    headers={"Content-Type": "text/plain"},
                )
                with urllib.request.urlopen(request, timeout=2.0) as response:
                    self.assertEqual(204, response.status)
                return True

            broker = ZaiStartPlanCaptchaBroker(
                open_url=open_url,
                urlopen=config_urlopen,
                random_state=lambda: "visible-state",
                interactions=interactions,
            )

            headers = broker.headers({"zcode_app_version": "3.8.1"})

            self.assertEqual("verified", headers[CAPTCHA_PARAM_HEADER])
            self.assertEqual(1, len(observations))
            completed = interactions.read()
            self.assertEqual("completed", completed.status)
            self.assertEqual("visible-state", completed.request_id)

    def test_remote_receiver_rejects_invalid_public_base_url(self):
        receiver = _CaptchaResultReceiver(
            ZaiStartPlanCaptchaConfig(True, "sgp", "prefix", "scene"),
            "state",
            15,
            public_base_url="javascript:alert(1)",
        )
        with receiver:
            with self.assertRaisesRegex(RuntimeError, "HTTP\\(S\\) origin"):
                _ = receiver.url

    def test_receiver_fixed_port_can_be_reused_after_a_completed_http_request(self):
        config = ZaiStartPlanCaptchaConfig(True, "sgp", "prefix", "scene")
        first = _CaptchaResultReceiver(config, "first-state", 15)
        with first:
            port = first._server.server_port
            with urllib.request.urlopen(first.url, timeout=2.0) as response:
                self.assertEqual(200, response.status)

        second = _CaptchaResultReceiver(config, "second-state", 15, port=port)
        with second:
            with urllib.request.urlopen(second.url, timeout=2.0) as response:
                self.assertEqual(200, response.status)

    def test_runtime_headers_are_applied_only_to_start_plan(self):
        class Broker:
            def headers(self, options):
                self.options = options
                return {
                    CAPTCHA_PARAM_HEADER: "fresh",
                    CAPTCHA_REGION_HEADER: "sgp",
                }

        broker = Broker()
        existing = {
            "authorization": "Bearer oauth",
            CAPTCHA_PARAM_HEADER: "stale",
        }

        projected = apply_zai_start_plan_runtime_headers(
            "zai-start-plan", {"plan_type": "start-plan"}, existing, broker=broker
        )
        other = apply_zai_start_plan_runtime_headers(
            "zai-coding-plan", {}, existing, broker=broker
        )

        self.assertEqual("Bearer oauth", projected["authorization"])
        self.assertEqual("fresh", projected[CAPTCHA_PARAM_HEADER])
        self.assertEqual("sgp", projected[CAPTCHA_REGION_HEADER])
        self.assertNotIn(CAPTCHA_PARAM_HEADER, other)

    def test_each_upstream_attempt_gets_a_fresh_captcha_header(self):
        class Broker:
            def __init__(self):
                self.calls = 0

            def headers(self, _options):
                self.calls += 1
                return {
                    CAPTCHA_PARAM_HEADER: f"fresh-{self.calls}",
                    CAPTCHA_REGION_HEADER: "sgp",
                }

        class Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b'{"choices":[{"message":{"content":"OK"}}]}'

        broker = Broker()
        attempts = []
        first_error = urllib.error.HTTPError(
            "https://zcode.z.ai/api/v1/zcode-plan/chat/completions",
            500,
            "temporary",
            {},
            io.BytesIO(b'{"error":{"message":"temporary"}}'),
        )

        def urlopen(request, timeout):
            del timeout
            attempts.append(dict(request.header_items()))
            if len(attempts) == 1:
                raise first_error
            return Response()

        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        pcfg.update({"api_key": "oauth-jwt", "gateway_retries": 1})

        def prepare_headers(provider, config, headers):
            return apply_zai_start_plan_runtime_headers(
                provider, config, headers, broker=broker
            )

        with (
            mock.patch.object(
                ciel_runtime,
                "prepare_provider_runtime_headers",
                side_effect=prepare_headers,
            ),
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=urlopen),
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime.time, "sleep"),
        ):
            result = ciel_runtime.post_json_with_rate_retry(
                "https://zcode.z.ai/api/v1/zcode-plan/chat/completions",
                {"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]},
                ciel_runtime.provider_headers("zai-start-plan", pcfg),
                30.0,
                "zai-start-plan",
                pcfg,
                "glm-5.3",
            )

        self.assertEqual("OK", result["choices"][0]["message"]["content"])
        self.assertEqual(2, broker.calls)
        self.assertEqual("fresh-1", attempts[0][CAPTCHA_PARAM_HEADER.capitalize()])
        self.assertEqual("fresh-2", attempts[1][CAPTCHA_PARAM_HEADER.capitalize()])


if __name__ == "__main__":
    unittest.main()
