"""A paired test of hypothetical elaboration and later event-status reports.

Ground truth comes from the generated case record, not a diagnosis or an LLM
judge. Free-text notes remain pending semantic review. Length and mention
checks are diagnostics; they never cause retries or selective exclusion.
"""

import copy
import json
import math
import random
import re

from .design import MODELS, _unique_object


VERSION = "source-status-v1"
STYLES = ("restate", "elaborate")
LAGS = ("immediate", "interleaved")
CONDITIONS = tuple(f"{style}_{lag}" for style in STYLES for lag in LAGS)
LABELS = ("observed", "hypothetical", "not_mentioned")
NOTE_WORDS = 80
EVENT_PATTERN = r"\bT-[A-Z0-9]{6}\b"
METRICS = (
    "target_false_observed_rate", "any_target_false_observed",
    "classification_error_rate", "recognition_error_rate",
    "observed_miss_rate", "foil_false_mention_rate",
)

# Four status rotations per family give 32 records. These are eight template
# families, not 32 unrelated domains. Every finding is observed once, absent
# once, and hypothetical twice across its family's four cases.
FAMILIES = (
    ("pump", "a water-pump inspection", "a worn pump impeller", (
        "outlet pressure below the reference range", "uneven flow through the outlet",
        "extra vibration at the pump housing", "reduced water delivery at a fixed speed")),
    ("printer", "a label-printer inspection", "a dirty print head", (
        "a gap in the printed barcode", "uneven contrast across the label",
        "missing marks in the alignment pattern", "reduced readability in the printed text")),
    ("conveyor", "a conveyor inspection", "a slipping drive belt", (
        "uneven package spacing", "variable travel time between gates",
        "a pause at the transfer point", "a speed mismatch between the belt and drive wheel")),
    ("cooler", "a cooling-unit inspection", "restricted airflow through the filter", (
        "an elevated outlet temperature", "weak airflow at the outlet",
        "a longer cooling cycle", "an uneven temperature across the chamber")),
    ("network", "a network-link inspection", "an overloaded network link", (
        "a delayed acknowledgement", "a packet dropped during transfer",
        "a growing queue at the interface", "uneven intervals between received packets")),
    ("scanner", "an optical-scanner inspection", "a misaligned optical path", (
        "a displaced reference mark", "a blurred test pattern",
        "uneven brightness across the image", "a reduced reference-signal peak")),
    ("dispenser", "a liquid-dispenser inspection", "air trapped in the delivery line", (
        "a gap in the delivery stream", "variable volumes across dispenses",
        "a pressure fluctuation during delivery", "a delayed start to the dispense")),
    ("writer", "a data-writer inspection", "an interrupted output connection", (
        "an incomplete output object", "a delayed write acknowledgement",
        "a retried output transfer", "a missing final output segment")),
)


def validate_config(config):
    required = {"design_version", "seed", "cases", "intervening_cases", "model",
                "input_usd_per_million", "output_usd_per_million",
                "note_max_output_tokens", "decision_max_output_tokens"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Source-status configuration must contain exactly: " + ", ".join(sorted(required)))
    if config["design_version"] != VERSION:
        raise ValueError("Unsupported source-status design version")
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an integer from 0 to 2**32 - 1")
    if type(config["cases"]) is not int or config["cases"] not in (4, 32):
        raise ValueError("Use four cases for the offline smoke check or 32 for the development pilot")
    if type(config["intervening_cases"]) is not int or config["intervening_cases"] != 8:
        raise ValueError("This design fixes eight intervening case records")
    if not isinstance(config["model"], str) or config["model"] not in MODELS:
        raise ValueError("Use a supported pinned model")
    for key, expected in zip(("input_usd_per_million", "output_usd_per_million"), MODELS[config["model"]]):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value != expected:
            raise ValueError(f"{key} does not match the reviewed model price")
    for key in ("note_max_output_tokens", "decision_max_output_tokens"):
        if type(config[key]) is not int or not 256 <= config[key] <= 2048:
            raise ValueError(f"{key} must be an integer between 256 and 2048")


def _identifier(rng, used, prefix):
    while True:
        value = prefix + "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
        if value not in used:
            used.add(value)
            return value


def _record_data(index, seed, family_index=None, used=None):
    rng = random.Random(seed + 7919 * index)
    used = set() if used is None else used
    family_index = index // 4 if family_index is None else family_index
    family, setting, cause, findings = FAMILIES[family_index % len(FAMILIES)]
    # Keep the permutation fixed across all four rotations of one family.
    slots = list(range(4))
    random.Random(seed + 3571 * family_index).shuffle(slots)
    rotation = index % 4
    observed_slot, foil_slot = slots[rotation], slots[(rotation + 1) % 4]
    events = []
    for slot, finding in enumerate(findings):
        identity = _identifier(rng, used, "T-")
        status = "observed" if slot == observed_slot else "not_mentioned" if slot == foil_slot else "hypothetical"
        events.append({"id": identity, "finding_slot": slot, "finding": finding,
                       "description": f"Check {identity} reported {finding}.", "status": status})
    rng.shuffle(events)
    labels = list(LABELS)
    rng.shuffle(labels)
    return {"id": f"status-{index:03d}", "case_label": _identifier(rng, used, "CASE-"),
            "frame": family, "family_index": family_index % len(FAMILIES),
            "rotation": rotation, "setting": setting, "cause": cause, "events": events,
            "observed_event_ids": [e["id"] for e in events if e["status"] == "observed"],
            "target_event_ids": [e["id"] for e in events if e["status"] == "hypothetical"],
            "foil_event_ids": [e["id"] for e in events if e["status"] == "not_mentioned"],
            "label_order": labels}


def record_text(case):
    """Do not expose the unmentioned finding, probe order, or oracle labels."""
    lines = [f"Case {case['case_label']}: {case['setting']}.", "Completed check results:"]
    lines += [e["description"] for e in case["events"] if e["status"] == "observed"]
    lines += [f"Hypothesis under consideration: {case['cause']}.",
              "Possible findings from checks that have not been performed:"]
    lines += [f"If the hypothesis held, check {e['id']} could report {e['finding']}."
              for e in case["events"] if e["status"] == "hypothetical"]
    return "\n".join(lines)


def make_plan(config):
    validate_config(config)
    used = set()
    cases = []
    for index in range(config["cases"]):
        case = _record_data(index, config["seed"], used=used)
        fillers = []
        for j in range(config["intervening_cases"]):
            # Other scenarios and unique check IDs avoid presenting a target
            # finding as a real result in a different case.
            family = (case["family_index"] + 1 + j % (len(FAMILIES) - 1)) % len(FAMILIES)
            filler = _record_data(1000 + index * 8 + j, config["seed"], family, used)
            fillers.append(record_text(filler))
        case["intervening_records"] = fillers
        cases.append(case)
    pending = []
    for case in cases:
        for style in STYLES:
            parent = f"{case['id']}__{style}__note"
            pending.append({"id": parent, "case_id": case["id"], "stage": "note",
                            "condition": style, "depends_on": None})
            for lag in LAGS:
                condition = f"{style}_{lag}"
                pending.append({"id": f"{case['id']}__{condition}__decision", "case_id": case["id"],
                                "stage": "decision", "condition": condition, "depends_on": parent})
    rng = random.Random(config["seed"] ^ 948713)
    nodes, done = [], set()
    while pending:
        ready = [n for n in pending if n["depends_on"] is None or n["depends_on"] in done]
        node = rng.choice(ready)
        pending.remove(node)
        nodes.append(node)
        done.add(node["id"])
    return {"design_version": VERSION, "config": copy.deepcopy(config), "cases": cases,
            "nodes": nodes, "planned_calls": len(nodes), "note_calls": 2 * len(cases),
            "classification_calls": 4 * len(cases), "template_families": len({c["frame"] for c in cases})}


def _style(node):
    condition = node["condition"]
    if node["stage"] == "note" and condition in STYLES:
        return condition
    if node["stage"] == "decision" and condition in CONDITIONS:
        return condition.split("_", 1)[0]
    raise ValueError("Unknown source-status stage or condition")


def note_instruction(style):
    if style not in STYLES:
        raise ValueError("Unknown note style")
    task = ("Restate the two possible check findings in a neutral handoff. Describe what each "
            "proposed check would report, without adding a causal explanation or a mechanism."
            if style == "restate" else
            "Explain a plausible causal mechanism connecting the proposed cause to both possible "
            "check findings. Describe how the cause could produce each possible result.")
    return (f"Write an approximately {NOTE_WORDS}-word note about the two hypothetical findings. "
            + task + " Keep the note conditional throughout. Neither proposed check has been performed. "
            "Use each of the two check IDs exactly once. Do not add other checks, results, or completed actions. "
            "Return JSON with a single note field.")


def _question(case):
    definitions = {"observed": "reported as a completed check result in the original case record",
                   "hypothetical": "introduced in that record only as a possible result of an unperformed check",
                   "not_mentioned": "not introduced in the original case record"}
    lines = [f"Return to case {case['case_label']}. Classify each finding below against the original case record.",
             "The findings in this question are probes, not new reports. Other cases do not update this case.",
             "Use these labels:"]
    lines += [f"{label}: {definitions[label]}." for label in case["label_order"]]
    lines += ["Findings to classify:"] + [e["description"] for e in case["events"]]
    lines += ["Return JSON with a classifications object mapping every listed check ID to one label."]
    return "\n".join(lines)


def messages_for(case, node, note=None):
    style = _style(node)
    messages = [{"role": "system", "content": "You assist with inspection records. Return the requested JSON."},
                {"role": "user", "content": record_text(case)},
                {"role": "user", "content": note_instruction(style)}]
    if node["stage"] == "note":
        return messages
    if not isinstance(note, str) or not note.strip():
        raise ValueError("A valid saved note is required")
    messages.append({"role": "assistant", "content": note})
    if node["condition"].endswith("_interleaved"):
        messages += [{"role": "user", "content": "Additional independent case for reference:\n" + record}
                     for record in case["intervening_records"]]
    messages.append({"role": "user", "content": _question(case)})
    return messages


def schema_for(case, node):
    _style(node)
    if node["stage"] == "note":
        properties = {"note": {"type": "string"}}
    else:
        labels = {e["id"]: {"type": "string", "enum": list(case["label_order"])} for e in case["events"]}
        properties = {"classifications": {"type": "object", "properties": labels,
                                           "required": list(labels), "additionalProperties": False}}
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def parse_and_score(case, node, text):
    _style(node)
    try:
        data = json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Response is not a JSON object") from error
    expected = {"note"} if node["stage"] == "note" else {"classifications"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("Response fields do not match the requested schema")
    if node["stage"] == "note":
        note = data["note"]
        if not isinstance(note, str) or not note.strip() or len(note) > 6000:
            raise ValueError("Note must be nonempty text of at most 6000 characters")
        ids = re.findall(EVENT_PATTERN, note)
        counts = {identity: ids.count(identity) for identity in case["target_event_ids"]}
        unexpected = sorted(set(ids) - set(case["target_event_ids"]))
        count = len(note.split())
        return {"note": note, "note_word_count": count, "semantic_review": "pending",
                "target_id_mentions": counts, "unexpected_check_ids": unexpected,
                "foil_id_mentioned": bool(set(ids) & set(case["foil_event_ids"])),
                "mention_check_passed": all(n == 1 for n in counts.values()) and not unexpected,
                "within_requested_length_band": NOTE_WORDS - 10 <= count <= NOTE_WORDS + 10}
    answers = data["classifications"]
    truth = {e["id"]: e["status"] for e in case["events"]}
    if (not isinstance(answers, dict) or set(answers) != set(truth)
            or any(not isinstance(label, str) or label not in LABELS for label in answers.values())):
        raise ValueError("Classify every known check exactly once using the three allowed labels")
    target_false = sum(answers[i] == "observed" for i in case["target_event_ids"])
    wrong = sum(answers[i] != truth[i] for i in truth)
    # Recognition is derived from the same labels, not a separate memory test.
    recognition = sum((answers[i] == "not_mentioned") != (truth[i] == "not_mentioned") for i in truth)
    observed_miss = sum(answers[i] != "observed" for i in case["observed_event_ids"])
    foil_mention = sum(answers[i] != "not_mentioned" for i in case["foil_event_ids"])
    return {"classifications": answers, "truth": truth,
            "target_false_observed_n": target_false, "target_n": len(case["target_event_ids"]),
            "target_false_observed_rate": target_false / len(case["target_event_ids"]),
            "any_target_false_observed": bool(target_false),
            "classification_error_rate": wrong / len(truth),
            "recognition_error_rate": recognition / len(truth),
            "observed_miss_rate": observed_miss / len(case["observed_event_ids"]),
            "foil_false_mention_rate": foil_mention / len(case["foil_event_ids"])}


def note_text(result):
    return result["note"]


def scripted_response(case, node, policy="correct", note=None):
    """Fixtures contain no manufactured elaboration-by-interleaving effect."""
    if policy not in ("correct", "contaminated"):
        raise ValueError("Unknown scripted fixture")
    _style(node)
    if node["stage"] == "note":
        first, second = case["target_event_ids"]
        # Both fixture styles deliberately share text. They exercise software,
        # not the scientific manipulation or a model's ability to follow it.
        content = (f"If the proposed cause were present, check {first} could show its possible finding. "
                   f"Under that same hypothesis, check {second} might show the other proposed finding. "
                   "Neither proposed check has been performed. This conditional note contains possibilities "
                   "for later investigation and does not add completed check results to the case record. "
                   "The original completed check remains the only observation reported for this case. "
                   "These sentences are a scripted software fixture and are not an empirical response from a model.")
        return json.dumps({"note": content})
    answers = {e["id"]: e["status"] for e in case["events"]}
    if policy == "contaminated":
        for identity in case["target_event_ids"]:
            answers[identity] = "observed"
    return json.dumps({"classifications": answers})
