"""Scientific invariants: evidence status, matched branches, and exact oracles."""

from collections import Counter
import json
import unittest

from evidence_pilot import design


def config(cases=24, seed=17):
    return {
        "design_version": design.VERSION,
        "seed": seed,
        "cases": cases,
        "model": "gpt-4.1-mini-2025-04-14",
        "input_usd_per_million": 0.4,
        "output_usd_per_million": 1.6,
        "note_max_output_tokens": 256,
        "decision_max_output_tokens": 256,
    }


def node(condition, stage="decision"):
    return {"condition": condition, "stage": stage}


class DesignTests(unittest.TestCase):
    def test_counterfactual_observations_change_oracle_only_when_they_become_observed(self):
        # Multiple seeds test the generator's invariant rather than one lucky instance.
        for seed in (0, 1, 17, 2026, 2**32 - 1):
            for case in design.make_plan(config(seed=seed))["cases"]:
                with self.subTest(seed=seed, case=case["id"]):
                    original = set(case["observed_event_ids"])
                    predicted = set(case["target_event_ids"])
                    self.assertFalse(original & predicted)
                    self.assertEqual(len(predicted), 2)
                    for condition in design.CONDITIONS:
                        current = original | predicted if condition == "observed_control" else original
                        # Calculate from event rows independently of oracle()/observed_ids().
                        scores = {
                            fault["id"]: sum(event["scores"][fault["id"]]
                                             for event in case["events"] if event["id"] in current)
                            for fault in case["faults"]
                        }
                        ordered = sorted(scores, key=scores.get, reverse=True)
                        self.assertGreater(scores[ordered[0]], scores[ordered[1]])
                        self.assertEqual(design.oracle(case, condition), (ordered[0], scores))
                        expected = case["target_fault"] if condition == "observed_control" else case["base_winner"]
                        self.assertEqual(ordered[0], expected)
                    self.assertEqual(case["target_fault"] == case["base_winner"],
                                     case["target_alignment"] == "aligned")

    def test_roles_and_alignments_are_balanced_within_each_surface_frame(self):
        plan = design.make_plan(config())
        self.assertEqual(len({case["frame"] for case in plan["cases"]}), 4)
        for frame in {case["frame"] for case in plan["cases"]}:
            cases = [case for case in plan["cases"] if case["frame"] == frame]
            self.assertEqual(Counter(case["target_alignment"] for case in cases),
                             {"aligned": 3, "conflict": 3})
            roles = Counter((next(i for i, f in enumerate(case["faults"])
                                 if f["id"] == case["base_winner"]), case["target_alignment"])
                            for case in cases)
            self.assertEqual(roles, {(position, alignment): 1 for position in range(3)
                                     for alignment in ("aligned", "conflict")})

    def test_schedules_are_reproducible_paired_and_dependency_ordered(self):
        for count in (6, 24):
            plan = design.make_plan(config(cases=count))
            self.assertEqual(plan, design.make_plan(config(cases=count)))
            self.assertEqual(plan["planned_calls"], 8 * count)
            self.assertEqual(len({n["id"] for n in plan["nodes"]}), 8 * count)
            completed = set()
            for item in plan["nodes"]:
                if item["depends_on"]:
                    self.assertIn(item["depends_on"], completed)
                completed.add(item["id"])
            for case in plan["cases"]:
                decisions = [n for n in plan["nodes"] if n["case_id"] == case["id"] and n["stage"] == "decision"]
                self.assertEqual(Counter(n["condition"] for n in decisions),
                                 {condition: 1 for condition in design.CONDITIONS})
                paired = {n["condition"]: n for n in decisions}
                self.assertEqual(paired["hypothesis_generated"]["depends_on"],
                                 paired["hypothesis_replay"]["depends_on"])
        self.assertNotEqual(design.make_plan(config(seed=17))["cases"],
                            design.make_plan(config(seed=18))["cases"])

    def test_attribution_contrast_keeps_note_text_and_requests_identical(self):
        case = design.make_plan(config(cases=6))["cases"][0]
        note = "If this fault were responsible, both possible findings could occur."
        generated = design.messages_for(case, node("hypothesis_generated"), note)
        replay = design.messages_for(case, node("hypothesis_replay"), note)
        self.assertEqual(generated[-2]["role"], "assistant")
        self.assertEqual(replay[-2]["role"], "user")
        replay[-2]["role"] = "assistant"
        self.assertEqual(generated, replay)
        minimal = design.messages_for(case, node("hypothesis_list"))
        self.assertEqual(generated[:3], minimal[:3])
        self.assertEqual(generated[-1], minimal[-1])
        self.assertTrue(generated[-2]["content"].startswith(minimal[-2]["content"]))
        for messages in (generated, minimal):
            self.assertIn("predictions, not additional observations", messages[-2]["content"])
            for identity in case["target_event_ids"]:
                self.assertIn(identity, messages[-2]["content"])

    def test_main_conditions_preserve_observation_record_and_positive_control_adds_events(self):
        case = design.make_plan(config(cases=6))["cases"][0]
        baseline = design.messages_for(case, node("direct"))[1]["content"]
        for condition in ("observed_summary", "hypothesis_list", "hypothesis_generated", "hypothesis_replay"):
            messages = design.messages_for(case, node(condition), "A test-only note.")
            self.assertEqual(messages[1]["content"], baseline)
        control = design.messages_for(case, node("observed_control"))[1]["content"]
        original_log = baseline.split("Current observation record:", 1)[1]
        positive_log = control.split("Current observation record:", 1)[1]
        for identity in case["target_event_ids"]:
            self.assertNotIn(identity, original_log)
            self.assertIn(identity, positive_log)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.case = design.make_plan(config(cases=6))["cases"][0]
        self.assertEqual(self.case["target_alignment"], "conflict")
        self.node = node("hypothesis_generated")

    def score(self, choice, citations, condition="hypothesis_generated"):
        return design.parse_and_score(self.case, node(condition), json.dumps({
            "predicted_fault": choice,
            "supporting_observed_event_ids": citations,
            "reason": "A short test rationale.",
        }))

    def test_action_errors_are_not_automatically_source_errors_and_vice_versa(self):
        observed = self.case["observed_event_ids"]
        target_events = self.case["target_event_ids"]
        correct = self.case["base_winner"]
        wrong = self.case["target_fault"]
        action_only = self.score(wrong, observed)
        self.assertTrue(action_only["choice_error"])
        self.assertFalse(action_only["source_error"])
        self.assertFalse(action_only["joint_error"])
        source_only = self.score(correct, target_events)
        self.assertFalse(source_only["choice_error"])
        self.assertTrue(source_only["target_source_error"])
        self.assertFalse(source_only["joint_error"])
        both = self.score(wrong, target_events)
        self.assertTrue(both["choice_error"])
        self.assertTrue(both["joint_error"])
        self.assertEqual(both["target_unobserved_ids"], sorted(target_events))

    def test_unrelated_source_error_does_not_count_as_target_source_error(self):
        non_target = next(event["id"] for event in self.case["events"]
                          if event["id"] not in self.case["observed_event_ids"] + self.case["target_event_ids"])
        result = self.score(self.case["target_fault"], [non_target])
        self.assertTrue(result["source_error"])
        self.assertTrue(result["any_source_joint_error"])
        self.assertFalse(result["target_source_error"])
        self.assertFalse(result["joint_error"])

    def test_real_new_observations_are_not_scored_as_contamination(self):
        result = self.score(self.case["target_fault"], self.case["target_event_ids"], "observed_control")
        self.assertFalse(result["source_error"])
        self.assertFalse(result["choice_error"])
        self.assertTrue(result["target_choice"])

    def test_empty_citation_list_is_valid_but_records_zero_coverage(self):
        result = self.score(self.case["base_winner"], [])
        self.assertFalse(result["source_error"])
        self.assertEqual(result["observed_recall"], 0)

    def test_unknown_or_duplicate_citations_are_invalid_not_mechanistic_evidence(self):
        for citations in (["EVT-does-not-exist"],
                          [self.case["target_event_ids"][0]] * 2,
                          "not a list", [None]):
            with self.subTest(citations=citations), self.assertRaises(ValueError):
                self.score(self.case["base_winner"], citations)

    def test_notes_are_only_structurally_validated_and_remain_pending_review(self):
        # This assertion is intentionally false about source status. Structural parsing
        # must never claim that it passed the later manual semantic audit.
        text = json.dumps({"note": "The current record observed " + self.case["target_event_ids"][0]})
        result = design.parse_and_score(self.case, node("hypothesis_generated", "note"), text)
        self.assertEqual(result["semantic_review"], "pending")
        self.assertEqual(design.note_text(result), json.loads(text)["note"])

    def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for malformed in ('{"note":"first","note":"second"}', '{"note":NaN}',
                          '{"note":""}', '```json\n{"note":"x"}\n```'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                design.parse_and_score(self.case, node("hypothesis_generated", "note"), malformed)

    def test_scripted_contamination_fixture_separates_aligned_and_conflicting_actions(self):
        for case in design.make_plan(config(cases=6))["cases"]:
            response = design.scripted_response(case, self.node, policy="contaminated")
            result = design.parse_and_score(case, self.node, response)
            self.assertTrue(result["target_source_error"])
            self.assertEqual(result["choice_error"], case["target_alignment"] == "conflict")
            self.assertEqual(result["joint_error"], case["target_alignment"] == "conflict")


if __name__ == "__main__":
    unittest.main()
