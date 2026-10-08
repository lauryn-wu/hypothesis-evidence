import copy
import unittest

from evidence_pilot.analysis import render_markdown, summarize


CONDITIONS = ("direct", "observed_summary", "hypothesis_list", "hypothesis_generated",
              "hypothesis_replay", "observed_control")


def fixture_plan(case_count=4):
    cases = [{"id": f"case-{i}", "frame": f"frame-{i % 3}",
              "target_alignment": "conflict" if i % 2 == 0 else "aligned"}
             for i in range(case_count)]
    nodes = []
    for case in cases:
        for stage, conditions in (("note", ("observed_summary", "hypothesis_generated")),
                                  ("decision", CONDITIONS)):
            for condition in conditions:
                nodes.append({"id": f"{case['id']}__{condition}__{stage}", "case_id": case["id"],
                              "stage": stage, "condition": condition, "depends_on": None})
    return {"cases": cases, "nodes": nodes, "planned_calls": len(nodes), "config": {"model": "fixture"}}


def add_result(plan, results, index, condition, *, stage="decision", status="valid",
               source=False, target=False, choice=False, recall=1.0, words=80):
    node = next(n for n in plan["nodes"] if n["case_id"] == f"case-{index}"
                and n["stage"] == stage and n["condition"] == condition)
    record = {"id": node["id"], "node": node, "status": status}
    if status == "valid":
        record["data"] = ({"note_word_count": words} if stage == "note" else {
            "source_error": source, "target_source_error": target, "choice_error": choice,
            "joint_error": choice and target, "observed_recall": recall,
            "cited_unobserved_ids": ["event-1"] if source else [], "predicted_fault": "fault-a",
        })
    results[node["id"]] = record
    return record


class AnalysisTests(unittest.TestCase):
    def test_unrun_data_have_unknown_rates_and_full_missingness_bounds(self):
        summary = summarize(fixture_plan(), {}, "openai")
        self.assertEqual(summary["evidence_type"], "LIVE_MODEL_DEVELOPMENT_DATA")
        self.assertEqual(summary["recorded_calls"], 0)
        self.assertEqual(summary["statuses"], {"missing": 32})
        for condition in summary["decisions"].values():
            self.assertEqual(condition["valid_n"], 0)
            self.assertEqual(condition["missing_or_invalid_n"], 4)
            for outcome in condition["outcomes"].values():
                self.assertIsNone(outcome["observed_rate"])
                self.assertEqual(outcome["all_planned_rate_bounds"], [0, 1])
        for contrast in summary["contrasts"].values():
            self.assertEqual(contrast["complete_pair_n"], 0)
            for outcome in contrast["outcomes"].values():
                self.assertIsNone(outcome["complete_pair_risk_difference"])
                self.assertEqual(outcome["all_planned_risk_difference_bounds"], [-1, 1])

    def test_contrast_uses_within_case_pairs_and_keeps_unpaired_information_in_bounds(self):
        plan, results = fixture_plan(), {}
        add_result(plan, results, 0, "hypothesis_generated", source=True)
        add_result(plan, results, 0, "hypothesis_list")
        add_result(plan, results, 1, "hypothesis_generated", source=True)
        add_result(plan, results, 2, "hypothesis_generated")
        add_result(plan, results, 3, "hypothesis_list")
        summary = summarize(plan, results, "openai")
        contrast = summary["contrasts"]["generated_minus_list"]
        self.assertEqual(contrast["planned_pair_n"], 4)
        self.assertEqual(contrast["complete_pair_n"], 1)
        source = contrast["outcomes"]["source_error"]
        self.assertEqual(source["complete_pair_risk_difference"], 1)
        self.assertEqual(source["all_planned_risk_difference_bounds"], [0, .75])
        self.assertEqual(source["first_only_error_n"], 1)
        self.assertEqual(source["second_only_error_n"], 0)
        generated = summary["decisions"]["hypothesis_generated"]["outcomes"]["source_error"]
        self.assertAlmostEqual(generated["observed_rate"], 2 / 3)
        self.assertEqual(generated["all_planned_rate_bounds"], [.5, .75])

    def test_direction_and_alignment_strata_are_not_pooled_into_independent_samples(self):
        plan, results = fixture_plan(), {}
        for i in range(4):
            add_result(plan, results, i, "hypothesis_generated", source=i % 2 == 0)
            add_result(plan, results, i, "hypothesis_list", source=i == 1)
        summary = summarize(plan, results, "scripted")
        self.assertEqual(summary["evidence_type"], "SCRIPTED_TEST_DATA")
        overall = summary["contrasts"]["generated_minus_list"]
        self.assertEqual(overall["complete_pair_n"], 4)
        self.assertEqual(overall["outcomes"]["source_error"]["complete_pair_risk_difference"], .25)
        conflict = summary["by_target_alignment"]["conflict"]["contrasts"]["generated_minus_list"]
        aligned = summary["by_target_alignment"]["aligned"]["contrasts"]["generated_minus_list"]
        self.assertEqual(conflict["complete_pair_n"], 2)
        self.assertEqual(conflict["outcomes"]["source_error"]["complete_pair_risk_difference"], 1)
        self.assertEqual(aligned["outcomes"]["source_error"]["complete_pair_risk_difference"], -.5)

    def test_source_choice_and_joint_outcomes_remain_distinct(self):
        plan, results = fixture_plan(), {}
        add_result(plan, results, 0, "direct", choice=True)
        add_result(plan, results, 1, "direct", source=True, choice=True)
        add_result(plan, results, 2, "direct", source=True, target=True)
        add_result(plan, results, 3, "direct", source=True, target=True, choice=True)
        outcomes = summarize(plan, results, "scripted")["decisions"]["direct"]["outcomes"]
        self.assertEqual(outcomes["source_error"]["error_n"], 3)
        self.assertEqual(outcomes["target_source_error"]["error_n"], 2)
        self.assertEqual(outcomes["choice_error"]["error_n"], 3)
        self.assertEqual(outcomes["joint_error"]["error_n"], 1)

    def test_nonvalid_statuses_cannot_become_correct_outcomes(self):
        plan, results = fixture_plan(6), {}
        for i, status in enumerate(("invalid", "blocked", "started", "interrupted", "not_run")):
            add_result(plan, results, i, "hypothesis_generated", status=status)
            add_result(plan, results, i, "hypothesis_list")
        summary = summarize(plan, results, "openai")
        stats = summary["decisions"]["hypothesis_generated"]
        self.assertEqual(stats["valid_n"], 0)
        self.assertEqual(stats["missing_or_invalid_n"], 6)
        self.assertEqual(summary["recorded_calls"], 10)
        self.assertEqual(summary["statuses"]["missing"], 38)
        self.assertIsNone(stats["outcomes"]["choice_error"]["observed_rate"])
        contrast = summary["contrasts"]["generated_minus_list"]
        self.assertEqual(contrast["complete_pair_n"], 0)
        self.assertEqual(contrast["outcomes"]["source_error"]["all_planned_risk_difference_bounds"],
                         [-1 / 6, 1])

    def test_note_counts_and_recall_only_include_valid_responses(self):
        plan, results = fixture_plan(), {}
        add_result(plan, results, 0, "observed_summary", stage="note", words=60)
        add_result(plan, results, 1, "observed_summary", stage="note", words=100)
        add_result(plan, results, 2, "observed_summary", stage="note", status="invalid")
        add_result(plan, results, 0, "direct", recall=.25)
        add_result(plan, results, 1, "direct", recall=.75)
        add_result(plan, results, 2, "direct", status="invalid")
        summary = summarize(plan, results, "scripted")
        note = summary["notes"]["observed_summary"]
        self.assertEqual((note["valid_n"], note["missing_or_invalid_n"]), (2, 2))
        self.assertEqual(note["mean_word_count"], 80)
        self.assertEqual(summary["decisions"]["direct"]["mean_observed_recall"], .5)
        self.assertNotIn("observed_control", " ".join(summary["contrasts"]))

    def test_corrupt_valid_data_and_mismatched_checkpoints_are_rejected(self):
        plan, results = fixture_plan(), {}
        record = add_result(plan, results, 0, "direct")
        original = copy.deepcopy(record)
        corruptions = [
            lambda r: r["data"].pop("source_error"),
            lambda r: r["data"].update(choice_error=0),
            lambda r: r["data"].update(observed_recall=float("nan")),
            lambda r: r["data"].update(target_source_error=True),
            lambda r: r["data"].update(joint_error=True),
            lambda r: r.update(node={}),
        ]
        for corrupt in corruptions:
            with self.subTest(corrupt=corrupt):
                changed = copy.deepcopy(original)
                corrupt(changed)
                with self.assertRaises(ValueError):
                    summarize(plan, {changed["id"]: changed}, "openai")

    def test_markdown_labels_missingness_scripted_data_and_prose_review(self):
        summary = summarize(fixture_plan(), {}, "scripted")
        markdown = render_markdown(summary)
        self.assertIn("SCRIPTED_TEST_DATA", markdown)
        self.assertIn("0/4", markdown)
        self.assertIn("manual review", markdown)
        self.assertIn("not sampling uncertainty", markdown)
        self.assertNotIn("0 (0.0%)", markdown)


if __name__ == "__main__":
    unittest.main()
