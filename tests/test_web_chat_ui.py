import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime


class WebChatUiTests(unittest.TestCase):
    def _cfg(self, provider: str = "ollama-cloud"):
        return {
            "current_provider": provider,
            "providers": {
                provider: copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"][provider]),
            },
        }

    def test_router_home_links_browser_web_chat(self):
        cfg = self._cfg()
        provider, pcfg = ciel_runtime.get_current_provider(cfg)

        html = ciel_runtime.render_router_home_html(cfg, provider, pcfg)

        self.assertIn("/ca/web/chat", html)
        self.assertIn("active coding-agent session", html)
        self.assertIn('<a class="chat-tab" href="/ca/web/chat">Web Chat</a>', html)
        self.assertIn('<a class="chat-tab" href="/ca/web/chat">Open Web Chat</a>', html)
        self.assertLess(html.index(">Web Chat</a>"), html.index(">LLM Settings</button>"))

    def test_web_chat_posts_to_channel_bridge_and_streams_replies(self):
        cfg = self._cfg()
        provider, pcfg = ciel_runtime.get_current_provider(cfg)
        model = ciel_runtime.current_alias(cfg)

        html = ciel_runtime.render_web_chat_html(cfg, provider, pcfg)

        self.assertIn("Session Web Chat", html)
        self.assertIn("/ca/channel/messages", html)
        self.assertIn("/ca/channel/stream", html)
        self.assertIn("active coding-agent session", html)
        self.assertIn("configured tools and MCP servers remain available", html)
        self.assertIn("ciel-runtime-router send_message tool", html)
        self.assertIn("delivery: ['llm', 'native']", html)
        self.assertNotIn("TEXT_ONLY_SYSTEM_PROMPT", html)
        self.assertNotIn("system: TEXT_ONLY_SYSTEM_PROMPT", html)
        self.assertIn(model, html)
        self.assertIn(".bubble", html)
        self.assertIn("bubble.className = 'bubble'", html)
        self.assertIn("function renderMarkdown(text)", html)
        self.assertIn("function renderMarkdownTable(lines, startIndex)", html)
        self.assertIn("new URLSearchParams(location.search)", html)
        self.assertIn("urlParams.set('session', sessionId)", html)
        self.assertIn("function loadInitialHistory()", html)
        self.assertIn("function loadOlderHistory()", html)
        self.assertIn("before: String(oldestId)", html)
        self.assertIn("mode === 'prepend'", html)
        self.assertIn(".markdown table", html)
        self.assertIn("bubble.innerHTML = renderMarkdown(text)", html)
        self.assertIn("bubble.textContent = text", html)
        self.assertIn("if (json.message) renderIncomingMessage(json.message);", html)
        self.assertNotIn("addBubble('user', outboundText);\n        const response", html)
        self.assertIn("Copy Chat Link", html)
        self.assertIn("Attach files", html)
        self.assertIn("id=\"fileInput\" type=\"file\" multiple", html)
        self.assertIn("function uploadAttachment(file)", html)
        self.assertIn("fetch('/ca/channel/files'", html)
        self.assertIn("announce: false", html)
        self.assertIn("attachments: uploads", html)
        self.assertIn("Use send_file when returning a file attachment", html)
        self.assertIn("Attached files:", html)
        self.assertIn("Speech Settings", html)
        self.assertIn("Start live voice", html)
        self.assertIn("/ca/speech/config", html)
        self.assertIn("/v1/audio/transcriptions", html)
        self.assertIn("/v1/audio/speech", html)
        self.assertIn("function startVoiceInput()", html)
        self.assertIn("function encodePcmWav(chunks, sampleRate)", html)
        self.assertIn("type: 'audio/wav'", html)
        self.assertIn("audioContext.createScriptProcessor", html)
        self.assertIn("function processVadFrame(event)", html)
        self.assertIn("finishVadUtterance()", html)
        self.assertIn("await sendMessage(transcriptText, [], {inputMode: 'voice'})", html)
        self.assertIn("stopActiveSpeech()", html)
        self.assertIn("echoCancellation: true", html)
        self.assertIn("function speakText(text, options = {})", html)
        self.assertIn("function unlockSpeechPlayback()", html)
        self.assertIn("function playSpeechBlob(blob, controller)", html)
        self.assertIn("context.decodeAudioData", html)
        self.assertIn("function playPcmSpeechStream", html)
        self.assertIn("function enqueueSpeech(text)", html)
        self.assertIn("return speakText(text, {interrupt: false})", html)
        self.assertIn("mode === 'append'", html)
        self.assertIn("renderIncomingMessage(message, 'history')", html)
        self.assertIn("function normalizeAsrTranscript(value)", html)
        self.assertIn("vadBargeInPending", html)
        self.assertIn("function voiceSensitivityPolicy()", html)
        self.assertIn("candidateMs < sensitivity.onsetMs", html)
        self.assertIn("transcriptLength < minimumVoiceTranscriptLength()", html)
        self.assertIn('id="voiceSensitivity"', html)
        self.assertIn('id="minimumTranscriptChars"', html)
        self.assertIn("function scrollTranscriptToBottom()", html)
        self.assertIn("height: 100dvh", html)
        self.assertIn("overflow: hidden", html)
        self.assertIn("stream_format: 'audio'", html)
        self.assertIn('id="ttsStreaming"', html)
        self.assertIn('id="ttsModel"', html)
        self.assertIn("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", html)
        self.assertIn("Qwen/Qwen3-ASR-1.7B", html)
        self.assertIn("Test voice", html)
        self.assertIn("Tailscale base URL", html)
        self.assertIn("Colab CLI connection", html)
        self.assertIn('id="colabDistribution"', html)
        self.assertIn('id="colabProfile"', html)
        self.assertIn('id="colabAsrSession"', html)
        self.assertIn('id="colabTtsSession"', html)
        self.assertIn("asr_accelerator: document.getElementById('colabAsrAccelerator').value", html)
        self.assertIn("/ca/speech/colab/action", html)
        self.assertIn("/ca/speech/colab/job", html)
        self.assertIn("Recover &amp; deploy", html)
        self.assertIn("Recreate all", html)
        self.assertIn('id="ttsReferenceAudio" type="file" accept="audio/*"', html)
        self.assertIn("pendingTtsReferenceAudio", html)
        self.assertIn('id="liveTranscript"', html)
        self.assertIn("requestLivePartial(now)", html)
        self.assertIn("Live: ' + text", html)
        self.assertIn("inputMode: 'voice'", html)
        self.assertIn("response_contract:", html)
        self.assertIn("structuredWebResponse(message)", html)
        self.assertIn("structured.spoken || structured.overview", html)
        self.assertIn("EXPECTED_INSTANCE_ID", html)
        self.assertIn("ORIGIN_INSTANCE_KEY", html)
        self.assertIn("verifyRuntimeIdentity", html)
        self.assertIn("Web Chat stopped to prevent cross-instance delivery", html)
        self.assertIn("instance_id", html)

    def test_web_chat_markdown_renderer_sanitizes_and_supports_tables(self):
        cfg = self._cfg()
        provider, pcfg = ciel_runtime.get_current_provider(cfg)

        html = ciel_runtime.render_web_chat_html(cfg, provider, pcfg)

        self.assertIn("escapeHtml(value)", html)
        self.assertIn("safeHref(value)", html)
        self.assertIn("isMarkdownTableDelimiter(line)", html)
        self.assertIn("<table>", html)
        self.assertIn("<thead><tr>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("rel=\"noopener noreferrer\"", html)
        self.assertNotIn("marked.min.js", html)
        self.assertNotIn("cdn.jsdelivr", html)

    def test_web_chat_reports_anthropic_routed_mode(self):
        cfg = self._cfg("anthropic")
        pcfg = cfg["providers"]["anthropic"]
        pcfg["api_key"] = "sk-ant-real"
        pcfg["route_through_router"] = True

        html = ciel_runtime.render_web_chat_html(cfg, "anthropic", pcfg)

        self.assertIn("anthropic-routed", html)
        self.assertIn("API key: set (Anthropic routed; primary sk-a...real; fp", html)

    def test_chat_file_upload_stores_base64_file_with_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ciel_runtime, "CHAT_FILES_DIR", Path(td)):
                upload = ciel_runtime.store_chat_file_upload(
                    {
                        "name": "notes.md",
                        "encoding": "base64",
                        "content": "SGVsbG8=",
                        "content_type": "text/markdown",
                    }
                )

            stored = Path(td) / upload["name"]
            self.assertTrue(stored.exists())
            self.assertEqual(b"Hello", stored.read_bytes())

        self.assertEqual("notes.md", upload["original_name"])
        self.assertEqual("text/markdown", upload["content_type"])
        self.assertEqual(5, upload["bytes"])
        self.assertIn("/ca/chat/files/", upload["path"])
        self.assertTrue(upload["url"].endswith(upload["path"]))

    def test_chat_file_upload_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(ciel_runtime, "CHAT_FILES_DIR", Path(td)),
                mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHAT_FILE_MAX_BYTES": "3"}),
            ):
                with self.assertRaises(OverflowError):
                    ciel_runtime.store_chat_file_upload({"name": "big.txt", "content": "four"})


if __name__ == "__main__":
    unittest.main()
