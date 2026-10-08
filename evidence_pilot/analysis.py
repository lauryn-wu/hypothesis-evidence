"""Descriptive, paired analysis of the fixed pilot schedule."""

from collections import Counter
import math


DECISION_CONDITIONS = (
    "direct", "observed_summary", "hypothesis_list", "hypothesis_generated",
    "hypothesis_replay", "observed_control",
)
NOTE_CONDITIONS = ("observed_summary", "hypothesis_generated")
METRICS = ("source_error", "target_source_error", "choice_error", "joint_error")
CONTRASTS = (
    ("generated_minus_list", "hypothesis_generated", "hypothesis_list"),
    ("generated_minus_summary", "hypothesis_generated", "observed_summary"),
    ("generated_minus_direct", "hypothesis_generated", "direct"),
    ("generated_minus_replay", "hypothesis_generated", "hypothesis_replay"),
)
WARNING = (
    "Development data from generated diagnostic tasks. Surface frames are not independent "
    "application domains, and repeated conditions are not independent samples. Contrasts are "
    "descriptive; no inferential intervals or deployment-prevalence claims are supplied. "
    "Missing or invalid responses are not correct responses. Free note prose needs manual "
    "review for assertions that hypothetical events actually occurred before interpreting "
    "a source error as contamination. A decision error alone does not establish contamination."
)


def _valid_data(node, results):
    record = results.get(node["id"])
    if record is None or record["status"] != "valid":
        return None
    data = record.get("data")
    if not isinstance(data, dict):
        raise ValueError("A valid result must contain parsed data")
    if node["stage"] == "note":
        count = data.get("note_word_count")
        if type(count) is not int or count < 0:
            raise ValueError("A valid note must contain a nonnegative word count")
    else:
        if any(type(data.get(metric)) is not bool for metric in METRICS):
            raise ValueError("A valid decision must contain every Boolean outcome")
        recall = data.get("observed_recall")
        if type(recall) not in (int, float) or not math.isfinite(recall) or not 0 <= recall <= 1:
            raise ValueError("A valid decision must contain observed recall in [0, 1]")
        if data["target_source_error"] and not data["source_error"]:
            raise ValueError("A target source error must also be a source error")
        if data["joint_error"] != (data["choice_error"] and data["target_source_error"]):
            raise ValueError("Joint outcome disagrees with its component outcomes")
    return data


def _statuses(nodes, results):
    return dict(sorted(Counter(results[n["id"]]["status"] if n["id"] in results
                               else "missing" for n in nodes).items()))


def _decision_summary(nodes, results):
    valid = [_valid_data(node, results) for node in nodes]
    valid = [data for data in valid if data is not None]
    planned_n, valid_n = len(nodes), len(valid)
    missing_n = planned_n - valid_n
    outcomes = {}
    for metric in METRICS:
        error_n = sum(data[metric] for data in valid)
        outcomes[metric] = {
            "error_n": error_n,
            "observed_rate": error_n / valid_n if valid_n else None,
            "all_planned_rate_bounds": [error_n / planned_n, (error_n + missing_n) / planned_n]
            if planned_n else None,
        }
    return {
        "planned_n": planned_n, "valid_n": valid_n, "missing_or_invalid_n": missing_n,
        "statuses": _statuses(nodes, results), "outcomes": outcomes,
        "mean_observed_recall": sum(data["observed_recall"] for data in valid) / valid_n
        if valid_n else None,
    }


def _paired_summary(nodes_by_condition, results, first, second):
    first_nodes = {node["case_id"]: node for node in nodes_by_condition[first]}
    second_nodes = {node["case_id"]: node for node in nodes_by_condition[second]}
    paired_cases = sorted(first_nodes.keys() & second_nodes.keys())
    pairs = [(_valid_data(first_nodes[case], results), _valid_data(second_nodes[case], results))
             for case in paired_cases]
    complete = [(a, b) for a, b in pairs if a is not None and b is not None]
    planned_n, complete_n = len(pairs), len(complete)
    outcomes = {}
    for metric in METRICS:
        first_only = sum(a[metric] and not b[metric] for a, b in complete)
        second_only = sum(b[metric] and not a[metric] for a, b in complete)
        lower = upper = 0
        for a, b in pairs:
            a_low = int(a[metric]) if a is not None else 0
            a_high = int(a[metric]) if a is not None else 1
            b_low = int(b[metric]) if b is not None else 0
            b_high = int(b[metric]) if b is not None else 1
            lower += a_low - b_high
            upper += a_high - b_low
        outcomes[metric] = {
            "complete_pair_risk_difference": (first_only - second_only) / complete_n
            if complete_n else None,
            "first_only_error_n": first_only, "second_only_error_n": second_only,
            "all_planned_risk_difference_bounds": [lower / planned_n, upper / planned_n]
            if planned_n else None,
        }
    return {"first_condition": first, "second_condition": second,
            "planned_pair_n": planned_n, "complete_pair_n": complete_n,
            "missing_or_invalid_pair_n": planned_n - complete_n, "outcomes": outcomes}


def _decision_and_contrast_summary(nodes, results):
    by_condition = {condition: [node for node in nodes
                               if node["stage"] == "decision" and node["condition"] == condition]
                    for condition in DECISION_CONDITIONS}
    return {
        "decisions": {condition: _decision_summary(group, results)
                      for condition, group in by_condition.items()},
        "contrasts": {name: _paired_summary(by_condition, results, first, second)
                      for name, first, second in CONTRASTS},
    }


def summarize(plan, results, backend, cost=None):
    """Use scheduled cases as denominators and compare only complete paired decisions."""
    if backend not in ("openai", "scripted"):
        raise ValueError("Unknown backend")
    nodes = plan["nodes"]
    node_ids = {node["id"] for node in nodes}
    cases = {case["id"]: case for case in plan["cases"]}
    if len(node_ids) != len(nodes) or plan["planned_calls"] != len(nodes):
        raise ValueError("Plan has duplicate IDs or inconsistent counts")
    if len(cases) != len(plan["cases"]) or not set(results) <= node_ids:
        raise ValueError("Plan has duplicate cases or results contain unknown IDs")
    seen = set()
    for node in nodes:
        if node["case_id"] not in cases:
            raise ValueError("Node refers to an unknown case")
        allowed = NOTE_CONDITIONS if node["stage"] == "note" else DECISION_CONDITIONS
        if node["stage"] not in ("note", "decision") or node["condition"] not in allowed:
            raise ValueError("Unknown stage or condition")
        key = (node["case_id"], node["stage"], node["condition"])
        if key in seen:
            raise ValueError("Multiple scheduled responses for the same case, stage, and condition")
        seen.add(key)
        record = results.get(node["id"])
        if record is not None:
            if record.get("id") != node["id"] or record.get("node") != node:
                raise ValueError("Result metadata disagrees with the schedule")
            if record.get("status") not in ("valid", "invalid", "blocked", "started", "interrupted", "not_run"):
                raise ValueError("Unknown result status")
            _valid_data(node, results)
    for case in cases.values():
        if case["target_alignment"] not in ("conflict", "aligned"):
            raise ValueError("Unknown target alignment")
    summary = {
        "backend": backend,
        "evidence_type": "LIVE_MODEL_DEVELOPMENT_DATA" if backend == "openai" else "SCRIPTED_TEST_DATA",
        "model_requested": plan.get("config", {}).get("model"),
        "planned_calls": len(nodes), "recorded_calls": len(results),
        "statuses": _statuses(nodes, results),
        "planned_cases": len(cases),
        "surface_frames": len({case["frame"] for case in cases.values()}),
        "warning": WARNING,
        **_decision_and_contrast_summary(nodes, results),
    }
    summary["by_target_alignment"] = {
        alignment: _decision_and_contrast_summary(
            [node for node in nodes if cases[node["case_id"]]["target_alignment"] == alignment], results)
        for alignment in ("conflict", "aligned")
    }
    notes = {}
    for condition in NOTE_CONDITIONS:
        group = [node for node in nodes if node["stage"] == "note" and node["condition"] == condition]
        valid = [_valid_data(node, results) for node in group]
        counts = [data["note_word_count"] for data in valid if data is not None]
        notes[condition] = {
            "planned_n": len(group), "valid_n": len(counts),
            "missing_or_invalid_n": len(group) - len(counts), "statuses": _statuses(group, results),
            "mean_word_count": sum(counts) / len(counts) if counts else None,
            "min_word_count": min(counts) if counts else None,
            "max_word_count": max(counts) if counts else None,
        }
    summary["notes"] = notes
    summary["cost"] = cost
    return summary


def _rate(value, signed=False):
    return "—" if value is None else format(value, "+.1%" if signed else ".1%")


def _decision_table(decisions):
    lines = ["| Condition | Valid / planned | Missing / invalid | Any source errors | Target source errors | Choice errors | Joint errors |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for condition, stats in decisions.items():
        cells = [f"{stats['outcomes'][metric]['error_n']} ({_rate(stats['outcomes'][metric]['observed_rate'])})"
                 for metric in METRICS]
        lines.append(f"| {condition} | {stats['valid_n']}/{stats['planned_n']} | {stats['missing_or_invalid_n']} | "
                     + " | ".join(cells) + " |")
    return lines


def _contrast_table(contrasts):
    lines = ["| Contrast | Complete / planned pairs | Any source errors | Target source errors | Choice errors | Joint errors |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, stats in contrasts.items():
        cells = [_rate(stats["outcomes"][metric]["complete_pair_risk_difference"], signed=True)
                 for metric in METRICS]
        lines.append(f"| {name} | {stats['complete_pair_n']}/{stats['planned_pair_n']} | "
                     + " | ".join(cells) + " |")
    return lines


def render_markdown(summary):
    """Render the principal descriptive results without hiding missingness."""
    lines = ["# Pilot run summary", "", f"**{summary['evidence_type']}**", "", summary["warning"], "",
             f"Saved {summary['recorded_calls']} of {summary['planned_calls']} scheduled request checkpoints. "
             f"Task instances: {summary['planned_cases']}; surface frames: {summary['surface_frames']}.", "",
             "Request status counts: " + ", ".join(f"{key}={value}" for key, value in summary["statuses"].items()) + ".", "",
             "## Decisions", "",
             "A source error cites an event that was not observed. Target source errors cite an assigned "
             "hypothetical event as observed. Joint errors combine a wrong choice with a target source error "
             "in the same response. Rates use valid responses only.", "",
             *_decision_table(summary["decisions"]), "",
             "The observed control makes the target findings real observations and scores against its "
             "updated answer. It is a responsiveness check, not a primary comparison condition.", "",
             "## Paired contrasts", "",
             "Differences are generated elaboration minus the named comparison, in percentage points. "
             "Positive values mean more errors after generated elaboration. The primary descriptive "
             "contrast is generated minus list for source errors; the list contains the same assigned "
             "hypothetical findings. Only complete within-case pairs contribute.", "",
             *_contrast_table(summary["contrasts"])]
    for alignment, label in (("conflict", "Hypothetical target conflicts with the observed answer"),
                             ("aligned", "Hypothetical target agrees with the observed answer")):
        stratum = summary["by_target_alignment"][alignment]
        lines.extend(["", f"## {label}", "", *_decision_table(stratum["decisions"]), "",
                      *_contrast_table(stratum["contrasts"])])
    lines.extend(["", "## Notes", "",
                  "| Condition | Valid / planned | Mean word count |", "|---|---:|---:|"])
    for condition, stats in summary["notes"].items():
        mean = "—" if stats["mean_word_count"] is None else f"{stats['mean_word_count']:.1f}"
        lines.append(f"| {condition} | {stats['valid_n']}/{stats['planned_n']} | {mean} |")
    if summary["cost"] is not None:
        cost = summary["cost"]
        lines.extend(["", "API accounting: "
                      f"{cost.get('api_calls', cost.get('reserved_calls', 'unknown'))} reserved calls; "
                      f"settled cost ${cost.get('settled_usd', 0):.6f}; "
                      f"unknown-cost reservations ${cost.get('reserved_unknown_usd', 0):.6f}."])
    lines.extend(["", "All-planned worst-case missingness bounds, observed-event recall, note counts, "
                  "and request-status counts are in `summary.json`. Bounds describe missing data, "
                  "not sampling uncertainty. No confidence intervals are estimated.", ""])
    return "\n".join(lines)
