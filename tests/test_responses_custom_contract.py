"""Deterministic custom Responses contract tests."""

import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough, ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE
from ciel_runtime_support.responses_custom_tool_bridge import (
    ResponsesCustomToolStreamProjector, project_response_payload, tool_definitions,
)

SCRIPT = "const text = '" + "\ud55c\uae00 caf\u00e9 \U0001f680" + "';" + chr(10) + "console.log(text);"
CUSTOM = {"type": "custom", "name": "exec", "format": {"type": "text"}}
FUNCTION = {
    "type": "function", "name": "lookup",
    "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}},
}


def namespace(name="functions"):
    return {"type": "namespace", "name": name, "tools": [copy.deepcopy(CUSTOM)]}


def function_item(name="exec", **extra):
    return {
        "id": "fc_exec", "type": "function_call", "status": "completed",
        "call_id": "call_exec", "name": name,
        "arguments": json.dumps({"input": SCRIPT}, ensure_ascii=False), **extra,
    }


def response_payload(item):
    return {"id": "resp_contract", "object": "response", "status": "completed",
            "output": [item], "usage": {"input_tokens": 10, "output_tokens": 4}}


def stream_events(item):
    return [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {**item, "status": "in_progress", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": item["id"],
         "output_index": 0, "delta": item["arguments"][:17]},
        {"type": "response.function_call_arguments.done", "item_id": item["id"],
         "output_index": 0, "arguments": item["arguments"]},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response_payload(item)},
    ]


def encode_events(events, *, space=" ", newline="\n", event_only_type=False):
    frames = []
    for event in events:
        data = dict(event)
        if event_only_type:
            data.pop("type")
        frames.append(newline.join([
            "event: " + event["type"],
            "data:" + space + json.dumps(data, ensure_ascii=False), "", "",
        ]).encode("utf-8"))
    return b"".join(frames)


def decode_events(wire):
    events = []
    for frame in wire.replace(b"\r\n", b"\n").split(b"\n\n"):
        lines = frame.split(b"\n")
        data = b"\n".join(line[5:].lstrip(b" ") for line in lines
                           if line.startswith(b"data:"))
        if not data or data == b"[DONE]":
            continue
        event = json.loads(data)
        if "type" not in event:
            event["type"] = next(line[6:].strip().decode() for line in lines
                                 if line.startswith(b"event:"))
        events.append(event)
    return events


def project_chunks(wire, tools, cuts=()):
    projector = ResponsesCustomToolStreamProjector(tools)
    output = []
    start = 0
    for stop in cuts:
        output.append(projector.feed(wire[start:stop]))
        start = stop
    output.append(projector.feed(wire[start:]))
    output.append(projector.finish())
    return b"".join(output)


class CustomContractAssertions:
    def assert_custom(self, item, *, namespace_name=None, raw=SCRIPT):
        self.assertEqual("custom_tool_call", item["type"])
        self.assertEqual("exec", item["name"])
        self.assertEqual(raw, item["input"])
        self.assertNotIn("arguments", item)
        self.assertEqual("call_exec", item["call_id"])
        self.assertEqual("fc_exec", item["id"])
        if namespace_name is not None:
            self.assertEqual(namespace_name, item["namespace"])
        else:
            self.assertNotIn("namespace", item)

    def assert_stream_custom(self, wire, *, namespace_name=None):
        events = decode_events(wire)
        self.assertEqual([
            "response.output_item.added", "response.custom_tool_call_input.delta",
            "response.custom_tool_call_input.done", "response.output_item.done",
            "response.completed",
        ], [event["type"] for event in events])
        self.assert_custom(events[0]["item"], namespace_name=namespace_name, raw="")
        self.assertEqual(SCRIPT, events[1]["delta"])
        self.assertEqual(SCRIPT, events[2]["input"])
        self.assertNotIn("arguments", events[2])
        self.assertEqual("fc_exec", events[2]["item_id"])
        self.assertEqual(0, events[2]["output_index"])
        self.assert_custom(events[3]["item"], namespace_name=namespace_name)
        self.assert_custom(events[4]["response"]["output"][0],
                           namespace_name=namespace_name)


class ResponsesCustomHelperTests(CustomContractAssertions, unittest.TestCase):
    def test_top_level_custom_exec_unwraps_input_without_mutating_sources(self):
        body = {"tools": [copy.deepcopy(CUSTOM)]}
        payload = response_payload(function_item())
        before = copy.deepcopy((body, payload))
        result = project_response_payload(payload, tool_definitions(body))
        self.assert_custom(result["output"][0])
        self.assertEqual(before, (body, payload))

    def test_unique_namespace_accepts_bare_alias_and_explicit_identity(self):
        tools = tool_definitions({"tools": [namespace()]})
        for item in (function_item(), function_item("functions__exec"),
                     function_item(namespace="functions")):
            with self.subTest(item=item):
                self.assert_custom(project_response_payload(response_payload(item), tools)
                                   ["output"][0], namespace_name="functions")

    def test_additional_tools_catalogue_is_collected_with_empty_top_level_tools(self):
        body = {"tools": [], "input": [{"type": "additional_tools",
                                        "tools": [namespace()]}]}
        result = project_response_payload(response_payload(function_item("functions__exec")),
                                          tool_definitions(body))
        self.assert_custom(result["output"][0], namespace_name="functions")

    def test_ambiguous_bare_name_is_not_guessed_but_aliases_resolve(self):
        tools = tool_definitions({"tools": [namespace(), namespace("other")]})
        payload = response_payload(function_item())
        self.assertEqual(payload, project_response_payload(payload, tools))
        for name in ("functions", "other"):
            for item in (function_item(name + "__exec"), function_item(namespace=name)):
                with self.subTest(name=name, item=item):
                    self.assert_custom(project_response_payload(response_payload(item), tools)
                                       ["output"][0], namespace_name=name)

    def test_exact_top_level_function_owns_bare_exec_not_namespace_custom(self):
        declaration = {**FUNCTION, "name": "exec"}
        tools = tool_definitions({"tools": [namespace(), declaration]})
        payload = response_payload(function_item())
        item = project_response_payload(payload, tools)["output"][0]
        self.assertEqual("function_call", item["type"])
        self.assertEqual("exec", item["name"])
        self.assertNotIn("namespace", item)
        self.assertNotIn("input", item)
        self.assertEqual(json.loads(payload["output"][0]["arguments"]),
                         json.loads(item["arguments"]))
        self.assert_custom(project_response_payload(
            response_payload(function_item("functions__exec")), tools)["output"][0],
            namespace_name="functions")

    def test_native_custom_item_is_not_reinterpreted(self):
        item = function_item()
        item.update(type="custom_tool_call", input=SCRIPT)
        item.pop("arguments")
        payload = response_payload(item)
        self.assertEqual(payload, project_response_payload(payload,
                         tool_definitions({"tools": [CUSTOM]})))

    def test_unrelated_function_arguments_preserve_whitespace_and_float(self):
        item = function_item("lookup", arguments=' { "count" : 1000.0 } ')
        payload = response_payload(item)
        self.assertEqual(payload, project_response_payload(payload,
                         tool_definitions({"tools": [CUSTOM]})))

    def test_sse_spacing_crlf_and_bytewise_utf8_boundaries(self):
        tools = tool_definitions({"tools": [namespace()]})
        for space in ("", " "):
            for newline in ("\n", "\r\n"):
                with self.subTest(space=space, newline=newline):
                    wire = encode_events(stream_events(function_item("functions__exec")),
                                         space=space, newline=newline)
                    self.assert_stream_custom(project_chunks(wire, tools, range(1, len(wire))),
                                              namespace_name="functions")

    def test_sse_every_single_split_including_multibyte_utf8(self):
        tools = tool_definitions({"tools": [CUSTOM]})
        wire = encode_events(stream_events(function_item()), newline="\r\n", space="")
        expected = project_chunks(wire, tools)
        self.assert_stream_custom(expected)
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                self.assertEqual(expected, project_chunks(wire, tools, [split]))

    def test_sse_event_field_supplies_missing_json_type(self):
        wire = encode_events(stream_events(function_item()), event_only_type=True, space="")
        self.assert_stream_custom(project_chunks(wire, tool_definitions({"tools": [CUSTOM]}),
                                                 range(1, len(wire), 7)))

    def test_argument_done_can_match_by_index_without_item_id(self):
        events = stream_events(function_item())
        events[2].pop("item_id")
        projected = decode_events(project_chunks(encode_events(events),
                                  tool_definitions({"tools": [CUSTOM]})))
        done = next(event for event in projected
                    if event["type"] == "response.custom_tool_call_input.done")
        self.assertEqual(SCRIPT, done["input"])
        self.assertEqual(0, done["output_index"])

    def test_native_and_unrelated_sse_frames_keep_exact_wire_bytes(self):
        native = function_item()
        native.update(type="custom_tool_call", input=SCRIPT)
        native.pop("arguments")
        for item in (native, function_item("lookup", arguments=' { "count" : 1000.0 } ')):
            with self.subTest(item_type=item["type"]):
                wire = encode_events([
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": response_payload(item)},
                ], newline="\r\n", space="")
                self.assertEqual(wire, project_chunks(wire,
                                 tool_definitions({"tools": [CUSTOM]}), [3, 19, 53]))


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, wire, *, stream):
        super().__init__(wire)
        self.headers = {"content-type": "text/event-stream" if stream else "application/json",
                        "content-length": str(len(wire))}


class ResponsesCustomPassthroughTests(CustomContractAssertions, unittest.TestCase):
    def forward(self, wire, body, config=None, *, remote=False):
        copied_headers = {}
        opened = []
        begin_delivery = mock.Mock()
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(),
                                  send_header=mock.Mock(), end_headers=mock.Mock())
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, remote)

        def urlopen(request, **_kwargs):
            opened.append(json.loads(request.data))
            return FakeResponse(wire, stream=body.get("stream", True))

        service = ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
            project_channel_context=lambda request: (request, {"delivery": True}),
            begin_channel_delivery=begin_delivery,
            normalize_model=lambda _provider, _config, model: model,
            normalize_request=lambda _provider, _config, request: request,
            upstream_base=lambda _provider, _config: "https://upstream.invalid",
            join_url=lambda base, path: base + path,
            headers=lambda _provider, _config, _inbound: {}, urlopen=urlopen,
            timeout_seconds=lambda _config: 1,
            copy_response_headers=lambda _handler, headers: copied_headers.update(headers),
        ))
        delivery = service.forward(handler, "test-provider", config or {}, body)
        self.assertEqual(1, len(opened), "fixture must never trigger a retry or continuation")
        handler.send_response.assert_called_once_with(200)
        if remote:
            begin_delivery.assert_not_called()
            self.assertEqual({}, delivery)
        else:
            begin_delivery.assert_called_once()
        return handler.wfile.getvalue(), copied_headers

    def body(self, tools=None, *, stream=True, **extra):
        return {"model": "fixture", "stream": stream, "input": "run the declared tool",
                "tools": copy.deepcopy(tools if tools is not None else [CUSTOM]), **extra}

    def test_nonstream_custom_repair_needs_no_flag_or_false_flag(self):
        for config in ({}, {"responses_custom_tools_as_functions": False}):
            for declared in ([CUSTOM], [namespace()]):
                with self.subTest(config=config, declared=declared):
                    item = function_item("functions__exec" if declared[0]["type"] == "namespace"
                                         else "exec")
                    output, headers = self.forward(json.dumps(response_payload(item)).encode(),
                                                   self.body(declared, stream=False), config)
                    self.assert_custom(json.loads(output)["output"][0], namespace_name=(
                        "functions" if declared[0]["type"] == "namespace" else None))
                    self.assertNotIn("content-length", {key.lower(): value
                                                       for key, value in headers.items()})

    def test_native_buffered_stream_repairs_custom_without_flag(self):
        for config in ({}, {"responses_custom_tools_as_functions": False}):
            for name in ("exec", "functions__exec"):
                with self.subTest(config=config, name=name):
                    output, _ = self.forward(encode_events(stream_events(function_item(name))),
                                             self.body([namespace()]), config)
                    self.assert_stream_custom(output, namespace_name="functions")

    def test_unbuffered_stream_collects_additional_tools_before_normalizing(self):
        body = self.body([], tool_choice="none", input=[
            {"type": "additional_tools", "tools": [namespace()]},
            {"type": "message", "role": "user", "content": "run"},
        ])
        output, _ = self.forward(encode_events(stream_events(function_item("functions__exec"))),
                                 body)
        self.assert_stream_custom(output, namespace_name="functions")

    def test_ambiguous_namespace_bare_is_untouched_but_alias_is_repaired(self):
        for name in ("exec", "functions__exec"):
            with self.subTest(name=name):
                payload = response_payload(function_item(name))
                output, _ = self.forward(json.dumps(payload).encode(),
                                         self.body([namespace(), namespace("other")], stream=False))
                if name == "exec":
                    self.assertEqual(payload, json.loads(output))
                else:
                    self.assert_custom(json.loads(output)["output"][0], namespace_name="functions")

    def test_top_level_function_precedence_does_not_normalize_without_flag(self):
        item = function_item(arguments=' { "count" : 1000.0 } ')
        payload = response_payload(item)
        output, _ = self.forward(json.dumps(payload).encode(), self.body(
            [namespace(), {**FUNCTION, "name": "exec"}], stream=False))
        self.assertEqual(payload, json.loads(output))

    def test_genuine_function_wire_is_exact_without_conversion_flag(self):
        for config in ({}, {"responses_custom_tools_as_functions": False}):
            for stream in (False, True):
                with self.subTest(config=config, stream=stream):
                    item = function_item("lookup", arguments=' { "count" : 1000.0 } ')
                    wire = (encode_events(stream_events(item), newline="\r\n", space="")
                            if stream else json.dumps(response_payload(item), indent=2).encode())
                    output, _ = self.forward(wire, self.body([FUNCTION], stream=stream), config)
                    self.assertEqual(wire, output)

    def test_unrelated_function_wire_is_exact_with_custom_catalogue_present(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                item = function_item("lookup", arguments=' { "count" : 1000.0 } ')
                wire = (encode_events(stream_events(item), newline="\r\n", space="")
                        if stream else json.dumps(response_payload(item), indent=2).encode())
                output, _ = self.forward(wire, self.body([CUSTOM, FUNCTION], stream=stream))
                self.assertEqual(wire, output)

    def test_native_custom_payload_remains_native(self):
        item = function_item()
        item.update(type="custom_tool_call", input=SCRIPT)
        item.pop("arguments")
        payload = response_payload(item)
        output, _ = self.forward(json.dumps(payload).encode(), self.body(stream=False))
        self.assertEqual(payload, json.loads(output))

    def test_flagged_meta_integer_normalization_stays_enabled(self):
        item = function_item("lookup", arguments='{"count":10000.0}')
        config = {"responses_custom_tools_as_functions": True}
        for stream in (False, True):
            with self.subTest(stream=stream):
                wire = (encode_events(stream_events(item)) if stream
                        else json.dumps(response_payload(item)).encode())
                output, _ = self.forward(wire, self.body([FUNCTION], stream=stream), config)
                if stream:
                    events = decode_events(output)
                    done = next(event for event in events
                                if event["type"] == "response.function_call_arguments.done")
                    arguments = done["arguments"]
                    self.assertEqual(arguments, events[-1]["response"]["output"][0]["arguments"])
                else:
                    arguments = json.loads(output)["output"][0]["arguments"]
                self.assertEqual('{"count":10000}', arguments)
                self.assertIs(type(json.loads(arguments)["count"]), int)

    def test_remote_bridge_new_restoration_guard_is_not_applied_without_flag(self):
        for config in ({}, {"responses_custom_tools_as_functions": False}):
            for stream in (False, True):
                with self.subTest(config=config, stream=stream):
                    item = function_item()
                    wire = (encode_events(stream_events(item)) if stream
                            else json.dumps(response_payload(item), indent=2).encode())
                    output, _ = self.forward(wire, self.body(stream=stream), config, remote=True)
                    self.assertEqual(wire, output)


if __name__ == "__main__":
    unittest.main()
