import json
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.remote_instructions import RemoteInstructionResult
from ciel_runtime_support.remote_memory import (
    MEMORY_POINTER_BEGIN,
    MEMORY_REFERENCE_INSTRUCTION,
    RemoteMemoryResult,
)


class RemoteMemoryRuntimeIntegrationTests(unittest.TestCase):
    @staticmethod
    def _active_memory(root: Path):
        state = root / "workspace-state"
        workspace = root / "workspace"
        index = workspace / ".ciel" / "memory" / "index.md"
        index.parent.mkdir(parents=True)
        index.write_text("# memory\n", encoding="utf-8")
        state.mkdir(parents=True)
        (state / "remote-memory.json").write_text(
            json.dumps(
                {
                    "workspace": str(workspace.resolve()),
                    "root": ".ciel/memory",
                    "index": "index.md",
                }
            ),
            encoding="utf-8",
        )
        return state, workspace, index, {"remote_memory": {"enabled": True}}

    @staticmethod
    def _stale_pointer():
        return (
            "<!-- ciel-runtime:remote-memory:begin -->\n"
            "Memory root: C:/stale/memory\n"
            "Memory index: C:/stale/memory/index.md\n"
            "stale guidance\n"
            "<!-- ciel-runtime:remote-memory:end -->"
        )

    def test_synchronizers_use_the_router_launch_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "project"
            with (
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime.Path,
                    "cwd",
                    return_value=Path(td) / "router-process-directory",
                ),
            ):
                instruction = ciel_runtime.remote_instruction_synchronizer()
                memory = ciel_runtime.remote_memory_synchronizer()
                self.assertEqual(workspace, instruction.workspace())
                self.assertEqual(workspace, memory.workspace())

    def test_every_interactive_launch_synchronizes_instructions_and_memory(self):
        for name in (
            "launch_claude",
            "launch_codex",
            "launch_codex_app_server",
            "launch_agy",
            "launch_kimi",
            "launch_grok",
        ):
            with self.subTest(name=name):
                launch = getattr(ciel_runtime, name)
                self.assertIs(ciel_runtime.sync_remote_launch_assets, launch.synchronize)

    def test_instruction_refresh_restores_the_current_native_memory_pointer(self):
        instruction_service = mock.Mock()
        instruction_service.sync.return_value = RemoteInstructionResult(
            "codex", "https://instructions.example/AGENTS.md", Path("AGENTS.md"), "updated"
        )
        memory_service = mock.Mock()
        with (
            mock.patch.object(
                ciel_runtime,
                "remote_instruction_synchronizer",
                return_value=instruction_service,
            ),
            mock.patch.object(
                ciel_runtime,
                "remote_memory_synchronizer",
                return_value=memory_service,
            ),
        ):
            result = ciel_runtime.sync_remote_instruction("codex", reason="pre-compact")

        self.assertEqual("updated", result.status)
        memory_service.project_current_pointer.assert_called_once_with("codex")

    def test_managed_memory_prompt_is_injected_for_all_router_protocol_families(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            prompt = (
                "<!-- ciel-runtime:remote-memory:begin -->\n"
                f"Memory root: {index.parent.resolve()}\n"
                f"Memory index: {index.resolve()}\n"
                f"{MEMORY_REFERENCE_INSTRUCTION}\n"
                "<!-- ciel-runtime:remote-memory:end -->"
            )
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value={"remote_memory": {"enabled": True}},
                ),
            ):
                responses = ciel_runtime.body_with_remote_memory_prompt(
                    {"instructions": "base"}, "openai_responses"
                )
                chat = ciel_runtime.body_with_remote_memory_prompt(
                    {"messages": [{"role": "user", "content": "hello"}]},
                    "openai_chat",
                )
                anthropic = ciel_runtime.body_with_remote_memory_prompt(
                    {"system": "base", "messages": []}, "anthropic_messages"
                )

        self.assertIn(prompt, responses["instructions"])
        self.assertTrue(responses["instructions"].rstrip().endswith(prompt))
        self.assertEqual("system", chat["messages"][0]["role"])
        self.assertEqual(prompt, chat["messages"][0]["content"])
        self.assertEqual(prompt, anthropic["system"][-1]["text"])

    def test_managed_memory_prompt_injection_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value={"remote_memory": {"enabled": True}},
                ),
            ):
                once = ciel_runtime.body_with_remote_memory_prompt(
                    {"instructions": "base"}, "openai_responses"
                )
                twice = ciel_runtime.body_with_remote_memory_prompt(
                    once, "openai_responses"
                )

        self.assertEqual(once, twice)
        self.assertEqual(1, twice["instructions"].count("Memory root:"))
        self.assertEqual(1, twice["instructions"].count("Memory index:"))
        self.assertEqual(1, twice["instructions"].count(MEMORY_REFERENCE_INSTRUCTION))

    def test_chat_moves_an_existing_memory_block_to_final_privileged_tail(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            existing = (
                "<!-- ciel-runtime:remote-memory:begin -->\n"
                f"Memory root: {index.parent.resolve()}\n"
                f"Memory index: {index.resolve()}\n"
                f"{MEMORY_REFERENCE_INSTRUCTION}\n"
                "<!-- ciel-runtime:remote-memory:end -->"
            )
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value={"remote_memory": {"enabled": True}},
                ),
            ):
                projected = ciel_runtime.body_with_remote_memory_prompt(
                    {
                        "messages": [
                            {
                                "role": "system",
                                "content": f"base\n\n{existing}\n\nlate system text",
                            },
                            {"role": "developer", "content": "final developer text"},
                            {"role": "user", "content": "hello"},
                        ]
                    },
                    "openai_chat",
                )

        rendered = json.dumps(projected, ensure_ascii=False)
        self.assertEqual(1, rendered.count("ciel-runtime:remote-memory:begin"))
        self.assertNotIn("ciel-runtime:remote-memory:begin", projected["messages"][0]["content"])
        self.assertTrue(
            projected["messages"][1]["content"].rstrip().endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )

    def test_protocol_finalizers_replace_stale_blocks_in_supported_content_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            state, workspace, index, config = self._active_memory(Path(td))
            stale = self._stale_pointer()
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(ciel_runtime, "load_config", return_value=config),
            ):
                chat = ciel_runtime.body_with_remote_memory_prompt(
                    {
                        "messages": [
                            {
                                "role": "system",
                                "content": [
                                    {"type": "text", "text": f"base\n{stale}"}
                                ],
                            },
                            {
                                "role": "developer",
                                "content": [
                                    {"type": "text", "text": "late developer"}
                                ],
                            },
                        ]
                    },
                    "openai_chat",
                )
                responses = []
                for input_value in (
                    [
                        {
                            "type": "message",
                            "role": "developer",
                            "content": [
                                {"type": "input_text", "text": stale}
                            ],
                        }
                    ],
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": stale}],
                    },
                    [
                        {
                            "type": "message",
                            "role": "developer",
                            "text": f"DEV_TEXT_FALLBACK\n{stale}",
                        }
                    ],
                    {
                        "type": "message",
                        "role": "developer",
                        "content": {"type": "input_text", "text": stale},
                    },
                ):
                    responses.append(
                        ciel_runtime.body_with_remote_memory_prompt(
                            {"instructions": "base", "input": input_value},
                            "openai_responses",
                        )
                    )
                anthropic = ciel_runtime.body_with_remote_memory_prompt(
                    {
                        "system": [{"type": "text", "text": "base"}],
                        "messages": [
                            {
                                "role": "system",
                                "content": [{"type": "text", "text": stale}],
                            }
                        ],
                    },
                    "anthropic_messages",
                )
                google = ciel_runtime.body_with_remote_memory_prompt(
                    {
                        "systemInstruction": {
                            "parts": [
                                {"text": stale},
                                {"text": "late google context"},
                            ]
                        }
                    },
                    "google_generative",
                )

        for projected in [chat, *responses, anthropic, google]:
            rendered = json.dumps(projected, ensure_ascii=False)
            self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
            self.assertNotIn("C:/stale/memory", rendered)
            self.assertIn(
                f"Memory root: {index.parent.resolve()}",
                rendered.replace("\\\\", "\\"),
            )
        self.assertIn("DEV_TEXT_FALLBACK", responses[2]["input"][0]["text"])
        self.assertTrue(
            chat["messages"][1]["content"][-1]["text"].endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )
        for projected in responses:
            self.assertTrue(
                projected["instructions"].endswith(
                    "<!-- ciel-runtime:remote-memory:end -->"
                )
            )
        self.assertTrue(
            anthropic["system"][-1]["text"].endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )
        self.assertTrue(
            google["systemInstruction"]["parts"][-1]["text"].endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )
        self.assertNotIn(
            {"text": ""},
            google["systemInstruction"]["parts"],
        )

    def test_disabled_memory_scrubs_cached_blocks_without_changing_clean_bodies(self):
        stale = self._stale_pointer()
        with (
            mock.patch.object(
                ciel_runtime,
                "load_config",
                return_value={"remote_memory": {"enabled": False}},
            ),
            mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", Path("missing-state")),
        ):
            dirty = {
                "anthropic_messages": {
                    "system": stale,
                    "messages": [],
                },
                "openai_responses": {
                    "instructions": stale,
                    "input": [],
                },
                "openai_chat": {
                    "messages": [{"role": "system", "content": stale}],
                },
            }
            for protocol, body in dirty.items():
                projected = ciel_runtime.body_with_remote_memory_prompt(body, protocol)
                self.assertNotIn(
                    MEMORY_POINTER_BEGIN,
                    json.dumps(projected, ensure_ascii=False),
                )
            clean = {"instructions": "base", "input": []}
            self.assertIs(
                clean,
                ciel_runtime.body_with_remote_memory_prompt(
                    clean,
                    "openai_responses",
                ),
            )

    def test_direct_chat_and_responses_wire_bodies_finalize_memory_after_policy(self):
        with tempfile.TemporaryDirectory() as td:
            state, workspace, index, config = self._active_memory(Path(td))
            stale = self._stale_pointer()
            captured = {}

            class Response:
                status = 200
                headers = {"content-type": "application/json"}

                def __init__(self, payload):
                    self.stream = BytesIO(payload)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, size=-1):
                    return self.stream.read(size)

            def policy(_provider, _pcfg, body):
                projected = dict(body)
                if "messages" in projected:
                    projected["messages"] = [
                        *projected["messages"],
                        {"role": "developer", "content": "late chat policy"},
                    ]
                else:
                    projected["input"] = [
                        *(
                            projected.get("input")
                            if isinstance(projected.get("input"), list)
                            else [projected.get("input")]
                            if isinstance(projected.get("input"), dict)
                            else []
                        ),
                        {
                            "type": "message",
                            "role": "developer",
                            "content": [
                                {"type": "input_text", "text": stale}
                            ],
                        },
                    ]
                return projected

            def urlopen(request, **_kwargs):
                captured[request.full_url] = json.loads(request.data)
                return Response(b'{}')

            handler = mock.Mock()
            handler.headers = {}
            handler.wfile = BytesIO()
            adapter = mock.Mock()
            adapter.responses_request_max_bytes.return_value = None
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(ciel_runtime, "load_config", return_value=config),
                mock.patch.object(ciel_runtime, "resolve_requested_model", return_value="model"),
                mock.patch.object(ciel_runtime, "provider_upstream_model", return_value="model"),
                mock.patch.object(ciel_runtime, "apply_provider_adapter_request_policy", side_effect=policy),
                mock.patch.object(ciel_runtime, "provider_upstream_request_base", return_value="https://provider.example/v1"),
                mock.patch.object(ciel_runtime, "provider_headers", return_value={}),
                mock.patch.object(ciel_runtime, "provider_chat_headers", return_value={}),
                mock.patch.object(ciel_runtime, "provider_urlopen", side_effect=urlopen),
                mock.patch.object(ciel_runtime, "provider_request_timeout_seconds", return_value=30.0),
                mock.patch.object(ciel_runtime, "_copy_upstream_response_headers"),
                mock.patch.object(ciel_runtime, "configured_provider_adapter", return_value=adapter),
                mock.patch.object(ciel_runtime, "body_with_pending_channel_messages", side_effect=lambda body: body),
                mock.patch.object(ciel_runtime, "body_with_channel_tool_result_context", side_effect=lambda body: body),
            ):
                ciel_runtime.forward_provider_chat(
                    handler,
                    "provider",
                    {},
                    {
                        "model": "alias",
                        "messages": [
                            {
                                "role": "system",
                                "content": [{"type": "text", "text": stale}],
                            }
                        ],
                    },
                )
                ciel_runtime.forward_provider_responses(
                    handler,
                    "provider",
                    {},
                    {
                        "model": "alias",
                        "instructions": "base",
                        "input": {
                            "type": "message",
                            "role": "developer",
                            "content": [{"type": "input_text", "text": stale}],
                        },
                        "stream": False,
                    },
                )

        chat = captured["https://provider.example/v1/chat/completions"]
        responses = captured["https://provider.example/v1/responses"]
        for projected in (chat, responses):
            rendered = json.dumps(projected, ensure_ascii=False)
            self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
            self.assertNotIn("C:/stale/memory", rendered)
            self.assertIn(
                f"Memory root: {index.parent.resolve()}",
                rendered.replace("\\\\", "\\"),
            )
        self.assertTrue(
            chat["messages"][-1]["content"].endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )
        self.assertTrue(
            responses["instructions"].endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )

    def test_translated_chat_wire_builders_restore_memory_after_hard_compaction(self):
        with tempfile.TemporaryDirectory() as td:
            state, workspace, index, remote_config = self._active_memory(Path(td))
            provider_config = {
                "context_window": 16384,
                "max_model_len": 16384,
                "max_output_tokens": 4096,
            }
            body = {
                "system": "BASE_SYSTEM",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [
                    {
                        "name": f"large_tool_{number}",
                        "description": "x" * 12_000,
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                        },
                    }
                    for number in range(100)
                ],
            }
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value=remote_config,
                ),
            ):
                openai = ciel_runtime.openai_compatible_chat_request(
                    "vllm",
                    "model",
                    body,
                    provider_config,
                )
                ollama = ciel_runtime.ollama_chat_request(
                    "model",
                    body,
                    provider_config,
                    provider="ollama",
                )

        for request in (openai, ollama):
            rendered = json.dumps(request["messages"], ensure_ascii=False)
            privileged = [
                message
                for message in request["messages"]
                if message.get("role") in {"system", "developer"}
            ]
            self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
            self.assertIn(
                f"Memory root: {index.parent.resolve()}",
                rendered.replace("\\\\", "\\"),
            )
            self.assertTrue(
                privileged[-1]["content"].endswith(
                    "<!-- ciel-runtime:remote-memory:end -->"
                )
            )

    def test_responses_memory_pointer_survives_to_ollama_system_wire_message(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            config = {"remote_memory": {"enabled": True}}
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(ciel_runtime, "load_config", return_value=config),
            ):
                responses = ciel_runtime.body_with_codex_compat_instructions(
                    config,
                    "ollama-cloud",
                    {},
                    {
                        "model": "model",
                        "instructions": "LONG_SYSTEM_PREFIX\n" + ("x" * 25_000),
                        "input": [
                            {
                                "type": "message",
                                "role": "developer",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "DEVELOPER_SENTINEL",
                                    }
                                ],
                            },
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "hello"}
                                ],
                            },
                        ],
                    },
                )
                self.assertTrue(
                    responses["instructions"].rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )
                anthropic = ciel_runtime.openai_responses_to_anthropic_messages(
                    responses,
                    "model",
                )
                anthropic = ciel_runtime.body_with_remote_memory_prompt(
                    anthropic,
                    "anthropic_messages",
                )
                wire = ciel_runtime.ollama_chat_request(
                    "model",
                    anthropic,
                    {},
                    stream=True,
                    provider="ollama-cloud",
                )

        first = wire["messages"][0]
        self.assertEqual("system", first["role"])
        self.assertTrue(first["content"].startswith("LONG_SYSTEM_PREFIX"))
        self.assertLess(len(first["content"]), 21_000)
        self.assertIn(f"Memory root: {index.parent.resolve()}", first["content"])
        self.assertIn(f"Memory index: {index.resolve()}", first["content"])
        self.assertIn(MEMORY_REFERENCE_INSTRUCTION, first["content"])
        self.assertEqual(1, first["content"].count("Memory root:"))
        self.assertEqual(1, first["content"].count("Memory index:"))
        self.assertTrue(
            first["content"].rstrip().endswith(
                "<!-- ciel-runtime:remote-memory:end -->"
            )
        )

    def test_launch_assets_sync_instruction_before_replacing_memory(self):
        memory = RemoteMemoryResult(
            "https://memory.example/manifest.json",
            Path(".ciel/memory"),
            Path(".ciel/memory/index.okf"),
            ".ciel/memory/index.okf",
            "updated",
            1,
        )
        calls = []
        with (
            mock.patch.object(
                ciel_runtime,
                "sync_remote_instruction",
                side_effect=lambda *_args, **_kwargs: calls.append("instruction"),
            ),
            mock.patch.object(
                ciel_runtime,
                "sync_remote_memory",
                side_effect=lambda *_args, **_kwargs: calls.append("memory") or memory,
            ),
        ):
            result = ciel_runtime.sync_remote_launch_assets("codex", reason="launch")

        self.assertIs(memory, result)
        self.assertEqual(["instruction", "memory"], calls)


if __name__ == "__main__":
    unittest.main()
