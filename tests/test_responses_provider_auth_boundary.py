import io
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.openai_responses_router import (
    _handle_codex_route,
    _handle_provider_responses_route,
)
from ciel_runtime_support.upstream_error_policy import UpstreamFailure


class ResponsesProviderAuthBoundaryTests(unittest.TestCase):
    def test_only_non_native_upstream_auth_is_marked_for_dependency_projection(self):
        for native in (False, True):
            for structured in (False, True):
                with self.subTest(native=native, structured=structured):
                    error = (
                        UpstreamFailure("test-provider", "test-model", status=401,
                                        message="invalid credentials")
                        if structured else urllib.error.HTTPError(
                            "https://provider.invalid/chat", 401, "Unauthorized", {},
                            io.BytesIO(b'{"error":"invalid credentials"}'),
                        )
                    )
                    services = SimpleNamespace(
                        core=SimpleNamespace(request_id=lambda: "test", event_bus=mock.Mock(),
                                             input_as_list=lambda x: x, is_client_disconnect=lambda x: False),
                        routing=SimpleNamespace(dump_request=mock.Mock(),
                                                forward_codex=mock.Mock(side_effect=error),
                                                forward_provider_responses=mock.Mock(side_effect=error)),
                        delivery=mock.Mock(),
                        output=SimpleNamespace(write_error=mock.Mock(),
                                               upstream_error_message=lambda *args: "invalid credentials",
                                               codex_auth_error_message=lambda x: x),
                    )
                    handler = SimpleNamespace(path="/v1/responses")
                    route = _handle_codex_route if native else _handle_provider_responses_route
                    route(handler, "test-provider", {}, {"input": [], "stream": True}, services)
                    call = services.output.write_error.call_args
                    self.assertIsNotNone(call)
                    self.assertEqual(401, call.kwargs["status"])
                    if native:
                        self.assertNotIn("upstream_provider", call.kwargs)
                    else:
                        self.assertEqual("test-provider", call.kwargs["upstream_provider"])


if __name__ == "__main__":
    unittest.main()
