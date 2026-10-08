"""Mocked network checks for spending, replay protection and response handling."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from evidence_pilot.api import (BudgetExceeded, DEFAULT_MODEL, DuplicateRequest,
                                  Ledger, OpenAIProvider, ProviderError,
                                  require_api_key)


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string"}},
          "required": ["choice"], "additionalProperties": False}
MESSAGES = [{"role": "user", "content": "Return a choice."}]


def response():
    return {"id": "resp_test", "model": DEFAULT_MODEL, "status": "completed",
            "usage": {"input_tokens": 120, "output_tokens": 10, "total_tokens": 130},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": '{"choice":"A"}'}]}]}


class APITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.key = patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret-never-log"})
        self.key.start()
        self.addCleanup(self.key.stop)

    def ledger(self, cap=1.0, max_calls=10):
        return Ledger(self.directory / "ledger.json", cap, 0.4, 1.6, max_calls)

    def complete(self, provider, request_id="trial-0"):
        return provider.complete(MESSAGES, SCHEMA, 200, request_id)

    def test_http_contract_and_usage_accounting(self):
        ledger = self.ledger()
        provider = OpenAIProvider(ledger)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response()).encode())) as network:
            result = self.complete(provider)
        request = network.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"]["schema"], SCHEMA)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(result["text"], '{"choice":"A"}')
        self.assertAlmostEqual(ledger.total(), (120 * 0.4 + 10 * 1.6) / 1_000_000)
        self.assertEqual(ledger.summary()["reserved_unknown_usd"], 0)
        self.assertTrue((self.directory / "responses" / "call-000000.json").exists())
        for path in self.directory.rglob("*.json"):
            self.assertNotIn("test-secret-never-log", path.read_text())

    def test_missing_key_makes_no_network_call(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("urllib.request.urlopen") as network:
            with self.assertRaises(ProviderError):
                require_api_key()
            with self.assertRaises(ProviderError):
                OpenAIProvider(None)
        network.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_budget_reserves_before_request(self):
        ledger = self.ledger(cap=0.000001)
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(BudgetExceeded):
                self.complete(OpenAIProvider(ledger))
        network.assert_not_called()
        self.assertEqual(ledger.summary()["api_calls"], 0)

    def test_timeout_keeps_reservation_and_blocks_replay_after_resume(self):
        ledger = self.ledger()
        with patch("urllib.request.urlopen", side_effect=TimeoutError("test-secret-never-log")) as network:
            with self.assertRaises(ProviderError) as raised:
                self.complete(OpenAIProvider(ledger))
        self.assertNotIn("test-secret-never-log", str(raised.exception))
        network.assert_called_once()
        self.assertGreater(ledger.summary()["reserved_unknown_usd"], 0)
        resumed = self.ledger()
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(DuplicateRequest):
                self.complete(OpenAIProvider(resumed))
        network.assert_not_called()

    def test_http_error_has_no_retry_and_no_response_body_logging(self):
        error = urllib.error.HTTPError("https://api.openai.com/v1/responses", 429,
                                       "test-secret-never-log", {}, None)
        ledger = self.ledger()
        with patch("urllib.request.urlopen", side_effect=error) as network:
            with self.assertRaisesRegex(ProviderError, "HTTP 429") as raised:
                self.complete(OpenAIProvider(ledger))
        self.assertNotIn("test-secret-never-log", str(raised.exception))
        network.assert_called_once()
        self.assertGreater(ledger.total(), 0)

    def test_malformed_json_keeps_reservation(self):
        ledger = self.ledger()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"not json")):
            with self.assertRaises(ProviderError):
                self.complete(OpenAIProvider(ledger))
        self.assertGreater(ledger.summary()["reserved_unknown_usd"], 0)

    def test_missing_and_invalid_usage_cannot_erase_reservation(self):
        for usage in (None, {}, {"input_tokens": True, "output_tokens": 2},
                      {"input_tokens": -2, "output_tokens": 2},
                      {"input_tokens": 1.0, "output_tokens": 2},
                      {"input_tokens": 1, "output_tokens": 2, "total_tokens": 9}):
            with self.subTest(usage=usage):
                path = self.directory / str(len(list(self.directory.iterdir()))) / "ledger.json"
                ledger = Ledger(path, 1, 0.4, 1.6, 10)
                bad = response()
                bad["usage"] = usage
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(bad).encode())):
                    with self.assertRaises(ProviderError):
                        self.complete(OpenAIProvider(ledger))
                self.assertGreater(ledger.summary()["reserved_unknown_usd"], 0)
                self.assertEqual(ledger.summary()["settled_usd"], 0)

    def test_invalid_response_with_valid_usage_is_accounted(self):
        invalid = []
        incomplete = response()
        incomplete["status"] = "incomplete"
        invalid.append(incomplete)
        empty = response()
        empty["output"] = []
        invalid.append(empty)
        refused = response()
        refused["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
        invalid.append(refused)
        wrong_model = response()
        wrong_model["model"] = "some-other-model"
        invalid.append(wrong_model)
        for index, bad in enumerate(invalid):
            with self.subTest(index=index):
                ledger = Ledger(self.directory / str(index) / "ledger.json", 1, 0.4, 1.6, 10)
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(bad).encode())):
                    with self.assertRaises(ProviderError):
                        self.complete(OpenAIProvider(ledger))
                self.assertEqual(ledger.summary()["reserved_unknown_usd"], 0)
                self.assertGreater(ledger.summary()["settled_usd"], 0)

    def test_max_calls_and_successful_request_replay_blocked(self):
        ledger = self.ledger(max_calls=1)
        provider = OpenAIProvider(ledger)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response()).encode())):
            self.complete(provider)
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(DuplicateRequest):
                self.complete(provider)
            with self.assertRaises(BudgetExceeded):
                self.complete(provider, "trial-1")
        network.assert_not_called()

    def test_overrun_records_usage_and_halts_future_calls(self):
        ledger = self.ledger()
        bad = response()
        bad["usage"] = {"input_tokens": 1000000, "output_tokens": 1000000}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(bad).encode())):
            with self.assertRaisesRegex(ProviderError, "ledger halted"):
                self.complete(OpenAIProvider(ledger))
        self.assertAlmostEqual(ledger.summary()["settled_usd"], 2)
        resumed = self.ledger()
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(BudgetExceeded):
                self.complete(OpenAIProvider(resumed), "trial-1")
        network.assert_not_called()

    def test_resume_cannot_change_prices_or_limits(self):
        self.ledger()
        for args in ((2, 0.4, 1.6, 10), (1, 0.2, 1.6, 10),
                     (1, 0.4, 1.2, 10), (1, 0.4, 1.6, 11)):
            with self.subTest(args=args), self.assertRaises(ProviderError):
                Ledger(self.directory / "ledger.json", *args)

    def test_multiple_ledger_objects_recheck_durable_limits(self):
        first, second = self.ledger(max_calls=1), self.ledger(max_calls=1)
        payload = {"model": DEFAULT_MODEL, "max_output_tokens": 10}
        first.reserve(payload, "one")
        with self.assertRaises(BudgetExceeded):
            second.reserve(payload, "two")

    def test_invalid_config_rejected_before_files(self):
        for args in ((float("nan"), 0.4, 1.6, 10), (1, 0, 1.6, 10),
                     (1, 0.4, float("inf"), 10), (1, 0.4, 1.6, True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Ledger(self.directory / "ledger.json", *args)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_text_only_prevents_unbounded_external_input(self):
        ledger = self.ledger()
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                OpenAIProvider(ledger).complete(
                    [{"role": "user", "content": [{"type": "input_file", "file_id": "f"}]}],
                    SCHEMA, 200, "one")
        network.assert_not_called()
        self.assertEqual(ledger.summary()["api_calls"], 0)


if __name__ == "__main__":
    unittest.main()
