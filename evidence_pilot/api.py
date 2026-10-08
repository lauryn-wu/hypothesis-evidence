"""Responses API transport and durable, conservative client-side accounting.

Only text requests are supported. There are no automatic retries. An interrupted
or uncertain request keeps its reservation and cannot be replayed under the same
request ID. This is a local spending safeguard, not a provider billing limit.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request


DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"
ENDPOINT = "https://api.openai.com/v1/responses"


class ProviderError(RuntimeError):
    """A request or response failed; the request is never silently retried."""


class BudgetExceeded(ProviderError):
    """The next reservation would exceed a spending or request-count limit."""


class DuplicateRequest(ProviderError):
    """The request ID has already been reserved, including uncertain requests."""


def require_api_key():
    """Validate credentials before the caller creates a live run directory."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or any(character.isspace() for character in key):
        raise ProviderError(
            "Set OPENAI_API_KEY locally before starting a live run. "
            "No API call was made. Do not paste the key into chat."
        )
    return key


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _write_json(path, value):
    """Write and fsync before replacing; readers never observe partial JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _positive_number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


class Ledger:
    """Serialize reservations across processes on macOS/Linux.

    Input reservation uses the serialized request's byte length plus 4,096 tokens
    of overhead, and reserves the entire output allowance. Cached input is charged
    at the ordinary input rate when accounting, so settled cost can overestimate
    the provider bill. Prices, cap and maximum requests are fixed on resume.
    """

    def __init__(self, path, cap_usd, input_usd_per_million,
                 output_usd_per_million, max_calls):
        for name, value in (("cap_usd", cap_usd),
                            ("input_usd_per_million", input_usd_per_million),
                            ("output_usd_per_million", output_usd_per_million)):
            _positive_number(value, name)
        if type(max_calls) is not int or max_calls <= 0:
            raise ValueError("max_calls must be a positive integer")
        self.path = Path(path)
        self.config = {
            "cap_usd": float(cap_usd),
            "input_usd_per_million": float(input_usd_per_million),
            "output_usd_per_million": float(output_usd_per_million),
            "max_calls": max_calls,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if self.path.exists():
                self._load()
            else:
                self.data = {"version": 1, **self.config, "calls": [],
                             "halted_reason": None}
                _write_json(self.path, self.data)

    @contextmanager
    def _locked(self):
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("Unsupported ledger")
            if any(data.get(key) != value for key, value in self.config.items()):
                raise ValueError("Resume requires the original budget, prices and max_calls")
            calls = data["calls"]
            if not isinstance(calls, list):
                raise ValueError("Malformed calls")
            seen = set()
            for entry in calls:
                request_id = entry["request_id"]
                if not isinstance(request_id, str) or not request_id or request_id in seen:
                    raise ValueError("Invalid or duplicate request IDs")
                seen.add(request_id)
                if entry["state"] not in ("reserved", "settled"):
                    raise ValueError("Invalid request state")
                _positive_number(entry["reserved_usd"], "reservation")
                if entry["state"] == "settled":
                    cost = entry["actual_usd"]
                    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                        raise ValueError("Invalid settled cost")
            self.data = data
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ProviderError("Cannot safely read or resume budget ledger: " + str(error)) from None

    def _total(self):
        return sum(entry["actual_usd"] if entry["state"] == "settled"
                   else entry["reserved_usd"] for entry in self.data["calls"])

    def total(self):
        with self._locked():
            self._load()
            return self._total()

    def summary(self):
        with self._locked():
            self._load()
            calls = self.data["calls"]
            return {
                "api_calls": len(calls), "max_calls": self.config["max_calls"],
                "cap_usd": self.config["cap_usd"],
                "settled_usd": sum(c["actual_usd"] for c in calls if c["state"] == "settled"),
                "reserved_unknown_usd": sum(c["reserved_usd"] for c in calls if c["state"] == "reserved"),
                "halted_reason": self.data.get("halted_reason"),
            }

    def reserve(self, payload, request_id):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be a nonempty string")
        maximum_output = payload.get("max_output_tokens")
        if type(maximum_output) is not int or maximum_output <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        serialized = _canonical(payload).encode("utf-8")
        bound = len(serialized) + 4096
        reservation = (bound * self.config["input_usd_per_million"] +
                       maximum_output * self.config["output_usd_per_million"]) / 1_000_000
        with self._locked():
            self._load()
            if self.data.get("halted_reason"):
                raise BudgetExceeded("Ledger is halted after a reservation overrun; inspect accounting")
            calls = self.data["calls"]
            if any(entry["request_id"] == request_id for entry in calls):
                raise DuplicateRequest("Request ID was already reserved; automatic replay is disabled")
            if calls and payload.get("model") != calls[0]["model"]:
                raise ProviderError("A ledger cannot mix models with one pricing configuration")
            if len(calls) >= self.config["max_calls"]:
                raise BudgetExceeded("Maximum API request count reached")
            if self._total() + reservation > self.config["cap_usd"]:
                raise BudgetExceeded("Next conservative reservation exceeds the run budget")
            call_id = len(calls)
            calls.append({
                "call_id": call_id, "request_id": request_id,
                "model": payload.get("model"), "state": "reserved",
                "reserved_usd": reservation, "input_token_bound": bound,
                "max_output_tokens": maximum_output,
                "payload_sha256": hashlib.sha256(serialized).hexdigest(),
                "started_at": time.time(),
            })
            _write_json(self.path, self.data)
            return call_id

    def settle(self, call_id, response):
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            raise ProviderError("Response has no valid usage; reservation retained")
        inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
        if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
            raise ProviderError("Response has invalid token usage; reservation retained")
        total = usage.get("total_tokens", inputs + outputs)
        if type(total) is not int or total != inputs + outputs:
            raise ProviderError("Response has inconsistent token usage; reservation retained")
        cost = (inputs * self.config["input_usd_per_million"] +
                outputs * self.config["output_usd_per_million"]) / 1_000_000
        with self._locked():
            self._load()
            if type(call_id) is not int or not 0 <= call_id < len(self.data["calls"]):
                raise ProviderError("Unknown reservation")
            entry = self.data["calls"][call_id]
            if entry["state"] != "reserved":
                raise DuplicateRequest("Reservation has already been settled")
            exceeded = cost > entry["reserved_usd"] + 1e-12
            entry.update({"state": "settled", "actual_usd": cost, "usage": usage,
                          "response_id": response.get("id"),
                          "resolved_model": response.get("model")})
            if exceeded:
                self.data["halted_reason"] = "actual_cost_exceeded_reservation"
            _write_json(self.path, self.data)
            if exceeded:
                raise ProviderError("Usage exceeded the conservative reservation; ledger halted")


class OpenAIProvider:
    """Send one Responses request per call, keeping credentials out of records."""

    def __init__(self, ledger, model=DEFAULT_MODEL, timeout_seconds=60,
                 raw_directory=None, temperature=0):
        require_api_key()
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a nonempty string")
        _positive_number(timeout_seconds, "timeout_seconds")
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        self.ledger, self.model = ledger, model
        self.timeout_seconds, self.temperature = timeout_seconds, temperature
        self.raw_directory = Path(raw_directory) if raw_directory else ledger.path.parent / "responses"

    def complete(self, messages, schema, max_output_tokens, request_id):
        key = require_api_key()
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a nonempty list")
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] not in ("system", "developer", "user", "assistant")
                    or not isinstance(message["content"], str)):
                raise ValueError("Only text messages with role and content are supported")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("schema must be an object JSON schema")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
            raise ValueError("max_output_tokens must be between 1 and 32768")
        payload = {
            "model": self.model, "input": messages, "store": False,
            "temperature": self.temperature, "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": "pilot_response",
                                 "schema": schema, "strict": True}},
        }
        call_id = self.ledger.reserve(payload, request_id)
        request = urllib.request.Request(
            ENDPOINT, data=_canonical(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as handle:
                response = json.load(handle)
        except urllib.error.HTTPError as error:
            raise ProviderError(f"OpenAI HTTP {error.code}; no retry; reservation retained") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("Network or response-decoding error; no retry; reservation retained") from None
        _write_json(self.raw_directory / f"call-{call_id:06d}.json", response)
        self.ledger.settle(call_id, response)
        if response.get("model") != self.model:
            raise ProviderError("Response model differs from the requested pinned model")
        if response.get("status") != "completed":
            raise ProviderError("Response was not completed; usage accounted, outcome invalid")
        texts = []
        output = response.get("output")
        if not isinstance(output, list):
            raise ProviderError("Response has no valid output list")
        for item in output:
            if not isinstance(item, dict):
                raise ProviderError("Response contains malformed output")
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise ProviderError("Response contains unexpected non-message output")
            if item.get("status") != "completed" or not isinstance(item.get("content"), list):
                raise ProviderError("Response message was not completed")
            for part in item["content"]:
                if (not isinstance(part, dict) or part.get("type") != "output_text"
                        or not isinstance(part.get("text"), str)):
                    raise ProviderError("Response refused or returned non-text content")
                texts.append(part["text"])
        text = "".join(texts)
        if not text.strip():
            raise ProviderError("Response contains no nonempty output text")
        return {"text": text, "response": response, "model": response["model"],
                "usage": response["usage"]}
