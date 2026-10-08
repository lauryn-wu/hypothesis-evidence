"""Offline transport and dependency checks, independent of the scientific task."""

from contextlib import contextmanager
from collections import Counter
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence_pilot.api import ProviderError
from evidence_pilot.common import read_json, write_json
from evidence_pilot.runner import ScriptedProvider, execute, load_results


CONFIG = {
    "seed": 10, "model": "gpt-4.1-mini-2025-04-14",
    "input_usd_per_million": 0.4, "output_usd_per_million": 1.6,
    "note_max_output_tokens": 256, "decision_max_output_tokens": 128,
}


def fixture_plan(config):
    cases = [{"id": f"case-{i}"} for i in range(3)]
    nodes = []
    for case in cases:
        prefix = case["id"]
        nodes.extend([
            {"id": prefix + "-direct", "case_id": prefix, "stage": "decision", "condition": "direct", "depends_on": None},
            {"id": prefix + "-note", "case_id": prefix, "stage": "note", "condition": "hypothetical", "depends_on": None},
            {"id": prefix + "-choice", "case_id": prefix, "stage": "decision", "condition": "hypothetical", "depends_on": prefix + "-note"},
        ])
    return {"config": copy.deepcopy(config), "cases": cases, "nodes": nodes,
            "planned_calls": len(nodes), "design_version": "RUNTIME_TEST_FIXTURE"}


def fixture_response(case, node, policy="correct", note=None):
    return json.dumps({"value": "NOTE" if node["stage"] == "note" else
                       "wrong" if policy == "contaminated" and note else "correct"})


def fixture_parse(case, node, text):
    data = json.loads(text)
    allowed = ("NOTE",) if node["stage"] == "note" else ("correct", "wrong")
    if not isinstance(data, dict) or set(data) != {"value"} or data["value"] not in allowed:
        raise ValueError("Invalid fixture response")
    return data


def fixture_summary(plan, results, backend, cost=None):
    return {"statuses": dict(sorted(Counter(r["status"] for r in results.values()).items())),
            "planned_calls": plan["planned_calls"], "recorded_calls": len(results), "cost": cost,
            "evidence_type": "SCRIPTED_TEST_DATA" if backend == "scripted" else "LIVE_MODEL_DEVELOPMENT_DATA"}


def mocked_http_response(request, timeout):
    payload = json.loads(request.data)
    data = {"value": payload["text"]["format"]["schema"]["properties"]["value"]["enum"][0]}
    response = {
        "id": "mock_response", "model": payload["model"], "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(data)}]}],
    }
    return io.BytesIO(json.dumps(response).encode())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "run"
        self.config = copy.deepcopy(CONFIG)
        self.plan = fixture_plan(self.config)
        def schema(case, node):
            return {"type": "object", "properties": {"value": {"type": "string", "enum": ["NOTE"] if node["stage"] == "note" else ["correct", "wrong"]}},
                    "required": ["value"], "additionalProperties": False}
        replacements = {
            "make_plan": fixture_plan,
            "messages_for": lambda case, node, note=None: [{"role": "user", "content": json.dumps({"id": node["id"], "note": note})}],
            "note_text": lambda data: data["value"], "schema_for": schema,
            "parse_and_score": fixture_parse, "scripted_response": fixture_response,
        }
        for target, replacement in replacements.items():
            patcher = patch("evidence_pilot.runner." + target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, replacement in {"summarize": fixture_summary, "render_markdown": lambda summary: json.dumps(summary)}.items():
            patcher = patch("evidence_pilot.analysis." + target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def live(self, **kwargs):
        defaults = {"backend": "openai", "budget_usd": 1, "max_calls": self.plan["planned_calls"]}
        defaults.update(kwargs)
        return execute(self.config, self.directory, **defaults)

    def test_complete_scripted_run_and_resume_preserve_saved_records(self):
        with patch("urllib.request.urlopen") as network:
            summary = execute(self.config, self.directory)
        network.assert_not_called()
        self.assertEqual(summary["statuses"], {"valid": 9})
        self.assertEqual(summary["evidence_type"], "SCRIPTED_TEST_DATA")
        before = {p.name: p.read_bytes() for p in (self.directory / "results").glob("*.json")}
        manifest_before = (self.directory / "manifest.json").read_bytes()
        with patch.object(ScriptedProvider, "complete_task", side_effect=AssertionError("replayed")):
            self.assertEqual(execute(self.config, self.directory, resume=True), summary)
        self.assertEqual(manifest_before, (self.directory / "manifest.json").read_bytes())
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.directory / "results").glob("*.json")})
        self.assertEqual(len(list((self.directory / "requests").glob("*.json"))), 9)
        self.assertEqual(len(list((self.directory / "responses").glob("*.json"))), 9)

    def test_generated_note_is_carried_only_to_dependent_decision(self):
        execute(self.config, self.directory, policy="contaminated")
        records = load_results(self.directory)
        for node in self.plan["nodes"]:
            record = records[node["id"]]
            if node["stage"] == "decision":
                self.assertEqual(record["data"]["value"], "wrong" if node["depends_on"] else "correct")
            prompt = read_json(self.directory / "requests" / (node["id"] + ".json"))
            value = json.loads(prompt["messages"][0]["content"])["note"]
            self.assertEqual(value, "NOTE" if node["depends_on"] else None)

    def test_interrupted_note_is_never_replayed_and_blocks_its_dependent(self):
        original = ScriptedProvider.complete_task
        interrupted = []
        def interrupt(provider, case, node, note):
            if node["stage"] == "note":
                interrupted.append(node["id"])
                raise KeyboardInterrupt()
            return original(provider, case, node, note)
        with patch.object(ScriptedProvider, "complete_task", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute(self.config, self.directory)
        request_id = interrupted[0]
        self.assertEqual(load_results(self.directory)[request_id]["status"], "started")
        visited = []
        def track(provider, case, node, note):
            visited.append(node["id"])
            return original(provider, case, node, note)
        with patch.object(ScriptedProvider, "complete_task", track):
            summary = execute(self.config, self.directory, resume=True)
        self.assertNotIn(request_id, visited)
        self.assertEqual(summary["statuses"], {"blocked": 1, "interrupted": 1, "valid": 7})

    def test_invalid_note_blocks_only_its_dependent_and_is_not_replaced(self):
        original = ScriptedProvider.complete_task
        invalid = []
        def malformed(provider, case, node, note):
            result = original(provider, case, node, note)
            if node["stage"] == "note" and not invalid:
                invalid.append(node["id"])
                result["text"] = '{"value": null}'
            return result
        with patch.object(ScriptedProvider, "complete_task", malformed):
            summary = execute(self.config, self.directory)
        self.assertEqual(summary["statuses"], {"blocked": 1, "invalid": 1, "valid": 7})
        self.assertNotIn("data", load_results(self.directory)[invalid[0]])
        with patch.object(ScriptedProvider, "complete_task", side_effect=AssertionError("replayed")):
            self.assertEqual(summary, execute(self.config, self.directory, resume=True))

    def test_missing_key_and_invalid_limits_create_no_run(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("urllib.request.urlopen") as network:
            with self.assertRaises(ProviderError):
                self.live()
            self.assertFalse(self.directory.exists())
        network.assert_not_called()
        for overrides in ({"budget_usd": None}, {"budget_usd": 0}, {"budget_usd": float("nan")},
                          {"max_calls": None}, {"max_calls": 10}, {"max_calls": True}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.live(**overrides)
        self.assertFalse(self.directory.exists())

    def test_live_call_cap_prevents_an_extra_http_request(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-key"}), \
             patch("urllib.request.urlopen", side_effect=mocked_http_response) as network:
            summary = self.live(max_calls=2)
        self.assertEqual(network.call_count, 2)
        self.assertEqual(summary["cost"]["api_calls"], 2)
        self.assertEqual(summary["statuses"], {"not_run": 1, "valid": 2})
        self.assertEqual(summary["recorded_calls"], 3)

    def test_network_failure_resume_retains_reservation_and_never_replays_id(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-key"}):
            with patch("urllib.request.urlopen", side_effect=TimeoutError()) as network:
                first = self.live()
            self.assertEqual(network.call_count, 1)
            unknown = first["cost"]["reserved_unknown_usd"]
            self.assertGreater(unknown, 0)
            with patch("urllib.request.urlopen", side_effect=mocked_http_response) as network:
                resumed = self.live(resume=True)
        self.assertEqual(network.call_count, 8)
        self.assertEqual(resumed["cost"]["reserved_unknown_usd"], unknown)
        self.assertEqual(resumed["statuses"], {"invalid": 1, "valid": 8})
        calls = read_json(self.directory / "ledger.json")["calls"]
        self.assertEqual(len({c["request_id"] for c in calls}), len(calls))
        self.assertEqual(calls[0]["state"], "reserved")

    def test_resume_rejects_changed_configuration_code_policy_or_saved_plan(self):
        execute(self.config, self.directory)
        with self.assertRaises(ValueError):
            execute(self.config, self.directory)
        with self.assertRaises(ValueError):
            execute(dict(self.config, seed=11), self.directory, resume=True)
        with patch("evidence_pilot.runner.source_fingerprint", return_value="changed-source"):
            with self.assertRaises(ValueError):
                execute(self.config, self.directory, resume=True)
        with self.assertRaises(ValueError):
            execute(self.config, self.directory, policy="contaminated", resume=True)
        plan = read_json(self.directory / "plan.json")
        plan["config"]["seed"] += 1
        write_json(self.directory / "plan.json", plan)
        with self.assertRaises(ValueError):
            execute(self.config, self.directory, resume=True)

    def test_new_run_rechecks_ownership_after_acquiring_lock(self):
        @contextmanager
        def concurrent_initializer(directory):
            write_json(directory / "manifest.json", {"marker": "other process"})
            write_json(directory / "plan.json", {"marker": "other plan"})
            yield
        with patch("evidence_pilot.runner.run_lock", concurrent_initializer):
            with self.assertRaises(ValueError):
                execute(self.config, self.directory)
        self.assertEqual(read_json(self.directory / "manifest.json"), {"marker": "other process"})

    def test_schedule_rejects_unknown_cases_duplicate_ids_and_forward_dependencies(self):
        bad_plans = []
        bad = copy.deepcopy(self.plan)
        bad["nodes"][0]["case_id"] = "unknown"
        bad_plans.append(bad)
        bad = copy.deepcopy(self.plan)
        bad["nodes"][0]["id"] = bad["nodes"][1]["id"]
        bad_plans.append(bad)
        bad = copy.deepcopy(self.plan)
        bad["nodes"][0]["depends_on"] = bad["nodes"][1]["id"]
        bad_plans.append(bad)
        for bad in bad_plans:
            with self.subTest(bad=bad), patch("evidence_pilot.runner.make_plan", return_value=bad):
                with self.assertRaises(ValueError):
                    execute(self.config, self.directory)
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
