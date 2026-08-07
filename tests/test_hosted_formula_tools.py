import json
import unittest

from ciel_runtime_support.architecture import HostedToolPolicy
from ciel_runtime_support.hosted_formula_tools import HostedFormulaToolService


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class HostedFormulaToolServiceTests(unittest.TestCase):
    def test_catalog_is_merged_and_formula_call_is_resolved_append_only(self):
        requests = []

        def open_url(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/tools"):
                return _Response(
                    {
                        "tools": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "description": "Search the web",
                                    "parameters": {"type": "object"},
                                },
                            }
                        ]
                    }
                )
            return _Response({"context": {"output": "official search result"}})

        service = HostedFormulaToolService(
            lambda _provider, _config: HostedToolPolicy(
                "https://api.moonshot.ai/v1", ("moonshot/web-search:latest",)
            ),
            lambda _level, _message: None,
            open_url=open_url,
        )
        original = {
            "model": "kimi-k3",
            "messages": [{"role": "user", "content": "current task"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "Read", "parameters": {"type": "object"}},
                }
            ],
            "stream": True,
            "tool_choice": "required",
        }
        prepared, state = service.prepare(
            "kimi",
            {},
            original,
            {"authorization": "Bearer secret"},
            600,
        )

        self.assertTrue(state.enabled)
        self.assertTrue(original["stream"])
        self.assertFalse(prepared["stream"])
        self.assertEqual(["Read", "web_search"], [tool["function"]["name"] for tool in prepared["tools"]])
        first = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "preserved reasoning",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": '{"query":"Kimi"}'},
                            }
                        ],
                    }
                }
            ]
        }
        posted = []

        def post_chat(body):
            posted.append(body)
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

        final = service.resolve(state, prepared, first, post_chat, 600)

        self.assertEqual("done", final["choices"][0]["message"]["content"])
        self.assertEqual("current task", posted[0]["messages"][0]["content"])
        self.assertEqual("preserved reasoning", posted[0]["messages"][1]["reasoning_content"])
        self.assertEqual("official search result", posted[0]["messages"][2]["content"])
        self.assertEqual("auto", posted[0]["tool_choice"])
        self.assertEqual(10.0, requests[0][1])

    def test_client_tool_name_wins_on_collision(self):
        service = HostedFormulaToolService(
            lambda _provider, _config: HostedToolPolicy(
                "https://api.moonshot.ai/v1", ("moonshot/web-search:latest",)
            ),
            lambda _level, _message: None,
            open_url=lambda _request, timeout: _Response(
                {"tools": [{"type": "function", "function": {"name": "web_search"}}]}
            ),
        )
        request = {
            "tools": [{"type": "function", "function": {"name": "web_search"}}]
        }

        prepared, state = service.prepare(
            "kimi", {}, request, {"authorization": "Bearer secret"}, 3
        )

        self.assertIs(request, prepared)
        self.assertFalse(state.enabled)


if __name__ == "__main__":
    unittest.main()
