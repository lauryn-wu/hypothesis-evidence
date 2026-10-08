"""Source-status task invariants, known counterexamples, and offline execution."""

from collections import Counter, defaultdict
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence_pilot.common import read_json, write_json
from evidence_pilot import source_status as design
from evidence_pilot import source_status_analysis as analysis
from evidence_pilot.runner import ScriptedProvider, execute, load_results, write_summary


ROOT = Path(__file__).resolve().parents[1]


def config(smoke=True, **changes):
    value = read_json(ROOT / "configs" / ("source-status-smoke.json" if smoke else "source-status.json"))
    value.update(changes)
    return value


def node(condition="elaborate_immediate", stage="decision"):
    return {"stage": stage, "condition": condition}


def saved_results(plan):
    cases = {c["id"]: c for c in plan["cases"]}
    results = {}
    for n in plan["nodes"]:
        data = design.parse_and_score(cases[n["case_id"]], n, design.scripted_response(cases[n["case_id"]], n))
        results[n["id"]] = {"id": n["id"], "node": n, "status": "valid", "data": data}
    return results


class SourceStatusDesignTests(unittest.TestCase):
    def test_fixed_plan_counts_and_shared_notes_for_both_lags(self):
        for count in (4, 32):
            plan = design.make_plan(config(cases=count))
            self.assertEqual(plan, design.make_plan(config(cases=count)))
            self.assertEqual(plan["planned_calls"], count * 6)
            self.assertEqual(plan["note_calls"], count * 2)
            self.assertEqual(plan["classification_calls"], count * 4)
            seen = set()
            for n in plan["nodes"]:
                if n["depends_on"]:
                    self.assertIn(n["depends_on"], seen)
                self.assertNotIn(n["id"], seen)
                seen.add(n["id"])
            for c in plan["cases"]:
                nodes = {n["condition"]: n for n in plan["nodes"] if n["case_id"] == c["id"]}
                self.assertEqual(set(nodes), set(design.CONDITIONS) | set(design.STYLES))
                for style in design.STYLES:
                    self.assertEqual(nodes[style + "_immediate"]["depends_on"], nodes[style + "_interleaved"]["depends_on"])

    def test_every_finding_rotates_across_statuses_within_its_family(self):
        plan = design.make_plan(config(smoke=False))
        roles = defaultdict(Counter)
        self.assertEqual(plan["template_families"], 8)
        for c in plan["cases"]:
            self.assertEqual(Counter(e["status"] for e in c["events"]),
                             {"observed": 1, "hypothetical": 2, "not_mentioned": 1})
            for event in c["events"]:
                roles[(c["frame"], event["finding_slot"])][event["status"]] += 1
        self.assertEqual(len(roles), 32)
        self.assertTrue(all(v == {"observed": 1, "not_mentioned": 1, "hypothetical": 2} for v in roles.values()))

    def test_foil_absent_from_encoding_prompts_schema_and_intervening_records(self):
        for case in design.make_plan(config(smoke=False))["cases"]:
            for style in design.STYLES:
                n = node(style, "note")
                before = json.dumps(design.messages_for(case, n)) + json.dumps(design.schema_for(case, n))
                before += json.dumps(case["intervening_records"])
                for identity in case["foil_event_ids"]:
                    self.assertNotIn(identity, before)
            for record in case["intervening_records"]:
                self.assertNotIn(case["case_label"], record)
                for event in case["events"]:
                    self.assertNotIn(event["id"], record)

    def test_interleaving_only_inserts_eight_records_and_preserves_exact_note(self):
        for case in design.make_plan(config())["cases"]:
            for style in design.STYLES:
                raw = 'If this occurred, the proposed check might fail.\nLiteral "saved" note.'
                a = design.messages_for(case, node(style + "_immediate"), raw)
                b = design.messages_for(case, node(style + "_interleaved"), raw)
                self.assertEqual(a, b[:4] + b[-1:])
                self.assertEqual(len(b) - len(a), 8)
                self.assertEqual(a[3], {"role": "assistant", "content": raw})
                self.assertEqual(a[1]["content"], design.record_text(case))
                self.assertEqual(design.schema_for(case, node(style + "_immediate")),
                                 design.schema_for(case, node(style + "_interleaved")))

    def test_style_pairs_have_same_record_question_response_schema_and_length_target(self):
        case = design.make_plan(config())["cases"][0]
        a = design.messages_for(case, node("restate_immediate"), "NOTE")
        b = design.messages_for(case, node("elaborate_immediate"), "NOTE")
        self.assertEqual(a[:2], b[:2])
        self.assertEqual(a[3:], b[3:])
        self.assertNotEqual(a[2], b[2])
        for m in (a[2], b[2]):
            self.assertIn("80-word", m["content"])
            self.assertIn("exactly once", m["content"])
            self.assertIn("conditional throughout", m["content"])
        self.assertEqual(design.schema_for(case, node("restate_immediate")),
                         design.schema_for(case, node("elaborate_immediate")))

    def test_config_rejects_accidental_large_runs_bad_prices_and_wrong_versions(self):
        for changes in ({"cases": 1000}, {"cases": True}, {"intervening_cases": 0},
                        {"intervening_cases": True}, {"seed": -1}, {"design_version": "unknown"},
                        {"model": "unreviewed-model"}, {"input_usd_per_million": float("nan")},
                        {"output_usd_per_million": 0.01}, {"decision_max_output_tokens": 10}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                design.make_plan(config(**changes))


class SourceStatusScoringTests(unittest.TestCase):
    def setUp(self):
        self.case = design.make_plan(config())["cases"][0]
        self.truth = {e["id"]: e["status"] for e in self.case["events"]}

    def score(self, answers):
        return design.parse_and_score(self.case, node(), json.dumps({"classifications": answers}))

    def test_target_status_error_is_distinct_from_recognition_and_other_source_errors(self):
        answers = dict(self.truth)
        answers[self.case["target_event_ids"][0]] = "observed"
        result = self.score(answers)
        self.assertEqual(result["target_false_observed_rate"], 0.5)
        self.assertTrue(result["any_target_false_observed"])
        self.assertEqual(result["recognition_error_rate"], 0)
        self.assertEqual(result["classification_error_rate"], 0.25)
        self.assertEqual(result["observed_miss_rate"], 0)
        self.assertEqual(result["foil_false_mention_rate"], 0)

    def test_forgetting_a_target_and_inventing_a_foil_do_not_become_target_false_observations(self):
        answers = dict(self.truth)
        answers[self.case["target_event_ids"][0]] = "not_mentioned"
        answers[self.case["foil_event_ids"][0]] = "observed"
        result = self.score(answers)
        self.assertEqual(result["target_false_observed_rate"], 0)
        self.assertEqual(result["recognition_error_rate"], 0.5)
        self.assertEqual(result["foil_false_mention_rate"], 1)

    def test_incomplete_unknown_and_duplicate_labels_are_invalid_not_safe(self):
        missing = dict(self.truth)
        missing.pop(next(iter(missing)))
        bad_label = dict(self.truth)
        bad_label[next(iter(bad_label))] = "unknown"
        for answers in (missing, {**self.truth, "T-EXTRA1": "observed"}, bad_label, [], None):
            with self.subTest(answers=answers), self.assertRaises(ValueError):
                self.score(answers)
        first = next(iter(self.truth))
        text = '{"classifications":{"' + first + '":"observed","' + first + '":"hypothetical"}}'
        with self.assertRaises(ValueError):
            design.parse_and_score(self.case, node(), text)

    def test_note_diagnostics_do_not_filter_or_claim_semantic_validity(self):
        first, second = self.case["target_event_ids"]
        foil = self.case["foil_event_ids"][0]
        text = f"Completed results: {first} failed; {first} failed again; {foil} passed."
        result = design.parse_and_score(self.case, node("elaborate", "note"), json.dumps({"note": text}))
        self.assertEqual(result["note"], text)
        self.assertEqual(result["semantic_review"], "pending")
        self.assertEqual(result["target_id_mentions"], {first: 2, second: 0})
        self.assertFalse(result["mention_check_passed"])
        self.assertTrue(result["foil_id_mentioned"])
        self.assertFalse(result["within_requested_length_band"])

    def test_correct_real_observations_are_not_scored_as_contamination(self):
        for c in design.make_plan(config())["cases"]:
            data = design.parse_and_score(c, node(), design.scripted_response(c, node()))
            self.assertTrue(all(data[metric] == 0 for metric in design.METRICS))


class SourceStatusAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.plan = design.make_plan(config())
        self.results = saved_results(self.plan)

    def inject(self, case_index, condition, wrong_target_n):
        case = self.plan["cases"][case_index]
        request_id = f"{case['id']}__{condition}__decision"
        record = self.results[request_id]
        answers = dict(record["data"]["classifications"])
        for identity in case["target_event_ids"][:wrong_target_n]:
            answers[identity] = "observed"
        record["data"] = design.parse_and_score(case, record["node"], json.dumps({"classifications": answers}))

    def test_known_case_contribution_gives_positive_interaction_with_correct_denominator(self):
        self.inject(0, "elaborate_interleaved", 2)
        summary = analysis.summarize(self.plan, self.results, "scripted")
        contrast = summary["contrasts"]["elaboration_by_interleaving"]
        metric = contrast["metrics"]["target_false_observed_rate"]
        self.assertEqual(contrast["complete_case_n"], 4)
        self.assertEqual(metric["mean_difference"], 0.25)
        self.assertEqual((metric["positive_case_n"], metric["negative_case_n"], metric["tied_case_n"]), (1, 0, 3))
        self.assertEqual(metric["all_planned_bounds"], [0.25, 0.25])
        self.assertEqual(summary["conditions"]["elaborate_interleaved"]["valid_target_finding_n"], 8)

    def test_general_interleaving_penalty_cancels_in_interaction(self):
        for i in range(4):
            for style in design.STYLES:
                self.inject(i, style + "_interleaved", 1)
        summary = analysis.summarize(self.plan, self.results, "scripted")
        metric = lambda name: summary["contrasts"][name]["metrics"]["target_false_observed_rate"]["mean_difference"]
        self.assertEqual(metric("elaboration_by_interleaving"), 0)
        self.assertEqual(metric("interleaving_effect_restate"), 0.5)
        self.assertEqual(metric("interleaving_effect_elaborate"), 0.5)

    def test_empty_run_has_unknown_means_and_full_missingness_bounds(self):
        summary = analysis.summarize(self.plan, {}, "scripted")
        for condition in summary["conditions"].values():
            self.assertEqual(condition["valid_n"], 0)
            for metric in condition["metrics"].values():
                self.assertIsNone(metric["mean"])
                self.assertEqual(metric["all_planned_bounds"], [0, 1])
        interaction = summary["contrasts"]["elaboration_by_interleaving"]["metrics"]["target_false_observed_rate"]
        self.assertIsNone(interaction["mean_difference"])
        self.assertEqual(interaction["all_planned_bounds"], [-2, 2])

    def test_incomplete_case_is_not_an_all_correct_quadruple(self):
        identity = self.plan["cases"][0]["id"] + "__elaborate_interleaved__decision"
        self.results[identity] = {"id": identity, "node": self.results[identity]["node"], "status": "invalid"}
        summary = analysis.summarize(self.plan, self.results, "scripted")
        contrast = summary["contrasts"]["elaboration_by_interleaving"]
        self.assertEqual(contrast["complete_case_n"], 3)
        self.assertEqual(contrast["metrics"]["target_false_observed_rate"]["all_planned_bounds"], [0, 0.25])

    def test_analysis_rejects_tampered_scores_unknown_ids_and_cross_style_dependencies(self):
        bad = copy.deepcopy(self.results)
        decision = next(n for n in self.plan["nodes"] if n["stage"] == "decision")
        bad[decision["id"]]["data"]["target_false_observed_rate"] = 1
        with self.assertRaises(ValueError):
            analysis.summarize(self.plan, bad, "scripted")
        with self.assertRaises(ValueError):
            analysis.summarize(self.plan, {**self.results, "unknown": {}}, "scripted")
        plan = copy.deepcopy(self.plan)
        n = next(n for n in plan["nodes"] if n["stage"] == "decision")
        other_style = "restate" if n["condition"].startswith("elaborate") else "elaborate"
        n["depends_on"] = f"{n['case_id']}__{other_style}__note"
        with self.assertRaises(ValueError):
            analysis.summarize(plan, {}, "scripted")


class SourceStatusRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "run"

    def test_full_schedule_and_resume_are_offline_and_preserve_checkpoints(self):
        with patch("urllib.request.urlopen") as network:
            first = execute(config(smoke=False), self.directory)
            before = {str(p): p.read_bytes() for p in (self.directory / "results").glob("*.json")}
            with patch.object(ScriptedProvider, "complete_task", side_effect=AssertionError("replayed")):
                resumed = execute(config(smoke=False), self.directory, resume=True)
        network.assert_not_called()
        self.assertEqual(first, resumed)
        self.assertEqual(first["statuses"], {"valid": 192})
        self.assertEqual(first["evidence_type"], "SCRIPTED_TEST_DATA")
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.directory / "results").glob("*.json")})
        self.assertEqual(first, write_summary(self.directory))
        for name in ("notes.md", "summary.md", "summary.json", "manifest.json", "plan.json"):
            self.assertTrue((self.directory / name).exists())

    def test_contaminated_fixture_has_no_built_in_elaboration_or_interleaving_effect(self):
        summary = execute(config(), self.directory, policy="contaminated")
        for condition in summary["conditions"].values():
            self.assertEqual(condition["metrics"]["target_false_observed_rate"]["mean"], 1)
        for contrast in summary["contrasts"].values():
            self.assertEqual(contrast["metrics"]["target_false_observed_rate"]["mean_difference"], 0)

    def test_interrupted_note_blocks_both_lags_and_never_replays(self):
        original = ScriptedProvider.complete_task
        seen = []
        def interrupt(provider, case, n, note):
            if n["stage"] == "note":
                seen.append(n["id"])
                raise KeyboardInterrupt()
            return original(provider, case, n, note)
        with patch.object(ScriptedProvider, "complete_task", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute(config(), self.directory)
        summary = execute(config(), self.directory, resume=True)
        self.assertEqual(summary["statuses"], {"blocked": 2, "interrupted": 1, "valid": 21})
        self.assertEqual(load_results(self.directory)[seen[0]]["status"], "interrupted")
        self.assertEqual(summary["contrasts"]["elaboration_by_interleaving"]["complete_case_n"], 3)

    def test_missing_key_creates_no_run_and_live_cap_stops_before_an_extra_request(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("urllib.request.urlopen") as network:
            with self.assertRaises(RuntimeError):
                execute(config(), self.directory, backend="openai", budget_usd=1, max_calls=24)
        network.assert_not_called()
        self.assertFalse(self.directory.exists())
        # A mocked provider exercises the actual new prompts, parser, ledger,
        # and runner. No real HTTP requests or credentials are used.
        class Provider:
            def __init__(self, ledger, model, raw_directory):
                self.ledger, self.model = ledger, model
            def complete(self, messages, schema, maximum, request_id):
                call = self.ledger.reserve({"model": self.model, "max_output_tokens": maximum,
                                            "input": messages, "schema": schema}, request_id)
                text = json.dumps({"note": "This is a hypothetical test note."})
                response = {"model": self.model, "id": request_id,
                            "usage": {"input_tokens": 50, "output_tokens": 20}}
                self.ledger.settle(call, response)
                return {"text": text, "model": self.model, "usage": response["usage"]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}), patch("urllib.request.urlopen") as network:
            summary = execute(config(), self.directory, backend="openai", budget_usd=1,
                              max_calls=1, provider_factory=Provider)
        network.assert_not_called()
        self.assertEqual(summary["cost"]["api_calls"], 1)
        self.assertEqual(summary["statuses"], {"missing": 22, "not_run": 1, "valid": 1})


if __name__ == "__main__":
    unittest.main()
