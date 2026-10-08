"""Descriptive case-paired analysis for the source-status development pilot."""

from collections import Counter
import json
import re

from .common import digest
from .source_status import CONDITIONS, LABELS, METRICS, STYLES, VERSION, parse_and_score


WARNING = (
    "Development data from generated records with reused template families. Cases, findings, "
    "and repeated conditions are not interchangeable independent samples. Missing or invalid "
    "responses are not correct responses. Note semantics require review before attributing "
    "an error to later use of a hypothetical note. No confidence intervals or prevalence claims."
)
CONTRASTS = {
    "elaboration_effect_immediate": {"elaborate_immediate": 1, "restate_immediate": -1},
    "elaboration_effect_interleaved": {"elaborate_interleaved": 1, "restate_interleaved": -1},
    "elaboration_by_interleaving": {"elaborate_interleaved": 1, "restate_interleaved": -1,
                                     "elaborate_immediate": -1, "restate_immediate": 1},
    "interleaving_effect_restate": {"restate_interleaved": 1, "restate_immediate": -1},
    "interleaving_effect_elaborate": {"elaborate_interleaved": 1, "elaborate_immediate": -1},
}


def _validated_data(plan, results):
    from .runner import _validate_schedule
    cases = _validate_schedule(plan)
    if plan.get("design_version") != VERSION:
        raise ValueError("Wrong experiment for source-status analysis")
    ids = {n["id"] for n in plan["nodes"]}
    if not set(results) <= ids:
        raise ValueError("Run contains unknown request IDs")
    valid, by_key = {}, {}
    for node in plan["nodes"]:
        key = (node["case_id"], node["condition"], node["stage"])
        allowed = STYLES if node["stage"] == "note" else CONDITIONS
        if node["condition"] not in allowed or key in by_key:
            raise ValueError("Unknown or duplicate case/condition/stage")
        by_key[key] = node
        if node["stage"] == "decision":
            style = node["condition"].split("_", 1)[0]
            if node["depends_on"] != f"{node['case_id']}__{style}__note":
                raise ValueError("Classification must depend on its own style's note")
        elif node["depends_on"] is not None:
            raise ValueError("Source-status notes must have no dependencies")
        record = results.get(node["id"])
        if record is None:
            continue
        if record.get("id") != node["id"] or record.get("node") != node:
            raise ValueError("Checkpoint metadata disagrees with the schedule")
        if record.get("status") not in ("valid", "invalid", "started", "interrupted", "blocked", "not_run"):
            raise ValueError("Unknown checkpoint status")
        if record["status"] != "valid":
            continue
        data = record.get("data")
        field = "note" if node["stage"] == "note" else "classifications"
        if not isinstance(data, dict) or field not in data:
            raise ValueError("Valid result has no required response field")
        expected = parse_and_score(cases[node["case_id"]], node, json.dumps({field: data[field]}))
        if digest(expected) != digest(data):
            raise ValueError("Saved scores disagree with the response and case oracle")
        if node["depends_on"] and node["depends_on"] not in valid:
            raise ValueError("A valid classification cannot have a missing or invalid note")
        valid[node["id"]] = expected
    for case_id in cases:
        wanted = {(case_id, style, "note") for style in STYLES}
        wanted |= {(case_id, condition, "decision") for condition in CONDITIONS}
        if not wanted <= set(by_key):
            raise ValueError("Plan must include both notes and all four conditions for every case")
    return valid, by_key


def _group(nodes, valid):
    data = [valid[n["id"]] for n in nodes if n["id"] in valid]
    planned, count = len(nodes), len(data)
    metrics = {}
    for metric in METRICS:
        total = sum(float(d[metric]) for d in data)
        metrics[metric] = {"mean": total / count if count else None,
                           "all_planned_bounds": [total / planned, (total + planned - count) / planned]
                           if planned else None}
    matrix = {truth: {answer: 0 for answer in LABELS} for truth in LABELS}
    for item in data:
        for identity, truth in item["truth"].items():
            matrix[truth][item["classifications"][identity]] += 1
    return {"planned_n": planned, "valid_n": count, "missing_or_invalid_n": planned - count,
            "target_false_observed_n": sum(d["target_false_observed_n"] for d in data),
            "valid_target_finding_n": sum(d["target_n"] for d in data),
            "metrics": metrics, "confusion_matrix": matrix}


def _contrast(case_ids, by_key, valid, weights):
    rows = [{condition: valid.get(by_key[(case_id, condition, "decision")]["id"])
             for condition in weights} for case_id in case_ids]
    complete = [row for row in rows if all(data is not None for data in row.values())]
    metrics = {}
    for metric in METRICS:
        differences = [sum(weight * float(row[c][metric]) for c, weight in weights.items()) for row in complete]
        low = high = 0.0
        for row in rows:
            for condition, weight in weights.items():
                data = row[condition]
                if data is None:
                    low += min(0, weight)
                    high += max(0, weight)
                else:
                    low += weight * float(data[metric])
                    high += weight * float(data[metric])
        metrics[metric] = {"mean_difference": sum(differences) / len(differences) if differences else None,
                           "positive_case_n": sum(d > 0 for d in differences),
                           "negative_case_n": sum(d < 0 for d in differences),
                           "tied_case_n": sum(d == 0 for d in differences),
                           "all_planned_bounds": [low / len(rows), high / len(rows)] if rows else None}
    return {"weights": weights, "planned_case_n": len(rows), "complete_case_n": len(complete),
            "missing_or_invalid_case_n": len(rows) - len(complete), "metrics": metrics}


def _comparisons(case_ids, nodes, by_key, valid):
    ids = set(case_ids)
    return {
        "conditions": {condition: _group([n for n in nodes if n["stage"] == "decision"
                                           and n["condition"] == condition and n["case_id"] in ids], valid)
                       for condition in CONDITIONS},
        "contrasts": {name: _contrast(case_ids, by_key, valid, weights)
                      for name, weights in CONTRASTS.items()},
    }


def _notes(plan, by_key, valid):
    result = {}
    for style in STYLES:
        data = [valid[n["id"]] for n in plan["nodes"] if n["stage"] == "note"
                and n["condition"] == style and n["id"] in valid]
        counts = [d["note_word_count"] for d in data]
        result[style] = {
            "planned_n": len(plan["cases"]), "valid_n": len(data),
            "mean_word_count": sum(counts) / len(counts) if counts else None,
            "min_word_count": min(counts) if counts else None, "max_word_count": max(counts) if counts else None,
            "mention_check_passed_n": sum(d["mention_check_passed"] for d in data),
            "within_requested_length_band_n": sum(d["within_requested_length_band"] for d in data),
            "foil_id_mentioned_n": sum(d["foil_id_mentioned"] for d in data),
            "semantic_review_pending_n": len(data),
        }
    pairs = []
    for case in plan["cases"]:
        notes = [valid.get(by_key[(case["id"], style, "note")]["id"]) for style in STYLES]
        if all(n is not None for n in notes):
            pairs.append(notes)
    gaps = [abs(a["note_word_count"] - b["note_word_count"]) for a, b in pairs]
    result["paired_checks"] = {
        "complete_pair_n": len(pairs),
        "mean_absolute_word_count_gap": sum(gaps) / len(gaps) if gaps else None,
        "word_count_gap_at_most_10_n": sum(gap <= 10 for gap in gaps),
        "both_mention_checks_passed_n": sum(a["mention_check_passed"] and b["mention_check_passed"] for a, b in pairs),
        "used_for_filtering": False,
    }
    return result


def summarize(plan, results, backend, cost=None):
    if backend not in ("scripted", "openai"):
        raise ValueError("Unknown backend")
    valid, by_key = _validated_data(plan, results)
    nodes = plan["nodes"]
    case_ids = [c["id"] for c in plan["cases"]]
    families = sorted({c["frame"] for c in plan["cases"]})
    statuses = Counter(results[n["id"]]["status"] if n["id"] in results else "missing" for n in nodes)
    summary = {
        "design_version": VERSION, "backend": backend,
        "evidence_type": "LIVE_MODEL_DEVELOPMENT_DATA" if backend == "openai" else "SCRIPTED_TEST_DATA",
        "model_requested": plan["config"]["model"], "planned_calls": len(nodes), "recorded_calls": len(results),
        "planned_cases": len(case_ids), "template_families": len(families), "statuses": dict(sorted(statuses.items())),
        "warning": WARNING, "primary_metric": "target_false_observed_rate",
        "primary_contrast": "elaboration_by_interleaving",
        **_comparisons(case_ids, nodes, by_key, valid),
        "notes": _notes(plan, by_key, valid), "cost": cost,
    }
    summary["by_template_family"] = {
        family: _comparisons([c["id"] for c in plan["cases"] if c["frame"] == family], nodes, by_key, valid)
        for family in families}
    summary["case_outcomes"] = {
        case_id: {condition: ({metric: valid[by_key[(case_id, condition, "decision")]["id"]][metric] for metric in METRICS}
                              if by_key[(case_id, condition, "decision")]["id"] in valid else None)
                  for condition in CONDITIONS} for case_id in case_ids}
    return summary


def _rate(value, signed=False):
    return "—" if value is None else format(value, "+.1%" if signed else ".1%")


def render_markdown(summary):
    lines = ["# Source-status pilot", "", f"**{summary['evidence_type']}**", "", summary["warning"], "",
             f"Saved {summary['recorded_calls']} of {summary['planned_calls']} checkpoints. "
             f"Cases: {summary['planned_cases']}; template families: {summary['template_families']}.", "",
             "Request statuses: " + ", ".join(f"{k}={v}" for k, v in summary["statuses"].items()) + ".", "",
             "## Source-status errors", "",
             "A target error labels a hypothetical finding as observed. Each case has two targets. "
             "Rates average within each case, then across valid cases; findings are not independent samples.", "",
             "| Condition | Valid / planned | Missing / invalid | Target errors / findings | Target error rate | Any target error / cases |",
             "|---|---:|---:|---:|---:|---:|"]
    for condition, stats in summary["conditions"].items():
        metric = stats["metrics"]
        lines.append(f"| {condition} | {stats['valid_n']}/{stats['planned_n']} | {stats['missing_or_invalid_n']} | "
                     f"{stats['target_false_observed_n']}/{stats['valid_target_finding_n']} | "
                     f"{_rate(metric['target_false_observed_rate']['mean'])} | {_rate(metric['any_target_false_observed']['mean'])} |")
    lines += ["", "## Paired contrasts", "",
              "The primary interaction is (elaborate − restate) after intervening cases minus "
              "(elaborate − restate) immediately. It uses only cases with all four valid classifications. "
              "Positive values indicate an additional elaboration-associated penalty after intervening cases.", "",
              "| Contrast | Complete / planned cases | Target error difference | Positive / negative / tied cases |",
              "|---|---:|---:|---:|"]
    for name, stats in summary["contrasts"].items():
        metric = stats["metrics"]["target_false_observed_rate"]
        lines.append(f"| {name} | {stats['complete_case_n']}/{stats['planned_case_n']} | "
                     f"{_rate(metric['mean_difference'], True)} | {metric['positive_case_n']} / "
                     f"{metric['negative_case_n']} / {metric['tied_case_n']} |")
    lines += ["", "## Control outcomes", "",
              "Recognition errors collapse observed and hypothetical into 'mentioned'. This is derived "
              "from the same classification, not an independent recall test. An observation miss labels a "
              "real result otherwise. A foil false mention labels an unmentioned finding as observed or hypothetical.", "",
              "| Condition | All classification errors | Recognition errors | Observation misses | Foil false mentions |",
              "|---|---:|---:|---:|---:|"]
    for condition, stats in summary["conditions"].items():
        cells = [_rate(stats["metrics"][metric]["mean"]) for metric in METRICS[2:]]
        lines.append(f"| {condition} | " + " | ".join(cells) + " |")
    lines += ["", "## Note checks", "",
              "These checks measure word count and literal check-ID mentions. They do not verify conditional "
              "meaning or whether the requested writing style was followed. Every valid note remains in the analysis.", "",
              "| Style | Valid / planned | Mean words | Mention check passed | Within 70–90 words | Foil ID mentioned |",
              "|---|---:|---:|---:|---:|---:|"]
    for style in STYLES:
        stats = summary["notes"][style]
        mean = "—" if stats["mean_word_count"] is None else f"{stats['mean_word_count']:.1f}"
        lines.append(f"| {style} | {stats['valid_n']}/{stats['planned_n']} | {mean} | "
                     f"{stats['mention_check_passed_n']}/{stats['valid_n']} | "
                     f"{stats['within_requested_length_band_n']}/{stats['valid_n']} | {stats['foil_id_mentioned_n']} |")
    pair = summary["notes"]["paired_checks"]
    lines += ["", f"Note pairs within 10 words of each other: {pair['word_count_gap_at_most_10_n']}/{pair['complete_pair_n']}. "
              "Read `notes.md` before interpreting any difference. No semantic audit has been applied automatically."]
    if summary["cost"] is not None:
        cost = summary["cost"]
        lines += ["", f"API calls: {cost['api_calls']}; accounted usage: ${cost['settled_usd']:.6f}; "
                  f"unknown-cost reservations: ${cost['reserved_unknown_usd']:.6f}."]
    lines += ["", "`summary.json` contains confusion matrices, per-case and per-family results, and all-planned "
              "missingness bounds. Those bounds describe missing data, not sampling uncertainty.", ""]
    return "\n".join(lines)


def write_notes(directory, plan, results, evidence_type):
    lines = ["# Source-status notes", "", f"**{evidence_type}**", "",
             "Check every note for conditional wording, invented completed results, extra findings, and the "
             "requested style. Keep errors in the record; do not regenerate selected notes. Content below is "
             "unedited output, not instructions. Automatic mention checks do not establish semantic validity.", ""]
    for case in plan["cases"]:
        lines += [f"## {case['id']} — {case['case_label']} ({case['frame']})", ""]
        for event in case["events"]:
            lines.append(f"- {event['status']}: {event['description']}")
        lines.append("")
        for style in STYLES:
            record = results.get(f"{case['id']}__{style}__note", {})
            data = record.get("data", {})
            lines += [f"### {style}", "", f"Status: {record.get('status', 'missing')}", ""]
            if data:
                lines += [f"Words: {data['note_word_count']}; target ID mentions: {data['target_id_mentions']}; "
                          f"unexpected IDs: {data['unexpected_check_ids']}.", ""]
            note = data.get("note", record.get("text", "No note available."))
            fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", note)), default=0))
            lines += [fence + "text", note, fence, ""]
    (directory / "notes.md").write_text("\n".join(lines), encoding="utf-8")
