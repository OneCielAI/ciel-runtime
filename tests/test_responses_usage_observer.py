import unittest

from ciel_runtime_support.responses_usage_observer import ResponsesUsageObserver


class ResponsesUsageObserverTests(unittest.TestCase):
    def test_observes_split_sse_response_completed_usage(self):
        observer = ResponsesUsageObserver()
        payload = (
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"usage":{'
            '"input_tokens":1000,"output_tokens":25,'
            '"input_tokens_details":{"cached_tokens":800,"cache_write_tokens":50}'
            '}}}\n\n'
        ).encode()

        observer.feed(payload[:47])
        observer.feed(payload[47:])

        self.assertEqual(
            {
                "input_tokens": 1000,
                "output_tokens": 25,
                "cache_read_tokens": 800,
                "cache_creation_tokens": 50,
                "uncached_input_tokens": 150,
            },
            observer.finish(),
        )

    def test_observes_non_streaming_response_usage(self):
        observer = ResponsesUsageObserver()
        observer.feed(
            b'{"id":"resp","usage":{"input_tokens":200,'
            b'"output_tokens":10,"input_tokens_details":{"cached_tokens":125}}}'
        )

        self.assertEqual(75, observer.finish()["uncached_input_tokens"])


if __name__ == "__main__":
    unittest.main()
