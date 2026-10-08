"""Generated triage records, paired prompts, and deterministic scoring.

There is no hidden true diagnosis: the oracle is the supplied triage rule.
Generated prose is retained verbatim and requires manual semantic review.
"""

import json
import math
import random

VERSION = "hypothetical-evidence-v1"
CONDITIONS = ("direct", "observed_summary", "hypothesis_list",
              "hypothesis_generated", "hypothesis_replay", "observed_control")
MODELS = {"gpt-4.1-mini-2025-04-14": (0.4, 1.6),
          "gpt-4o-mini-2024-07-18": (0.15, 0.6)}

# Surface frames share the same formal task; they are not independent domains.
FRAMES = [
    {"name": "service_incident", "setting": "a web service incident",
     "faults": ["cache configuration", "database connection pool", "request router"],
     "events": [
         ["Cached requests are slower than cache-bypassed requests.",
          "Cache misses rose after the configuration update.",
          "Repeated reads return an older object revision.",
          "Flushing a test cache restores the expected response."],
         ["Connection acquisition time increased during the incident.",
          "The pool wait queue grew while query duration stayed stable.",
          "A diagnostic connection cannot obtain a pool slot.",
          "Increasing the test pool limit removes the queue."],
         ["Requests routed through one edge have elevated latency.",
          "Route selection changed around the incident start.",
          "A trace shows repeated forwarding between two routers.",
          "Pinning a test request to a direct route removes the delay."]],
     "neutral": ["The incident ticket was opened during the afternoon shift.",
                 "The service identifier matches the inventory record."]},
    {"name": "packing_line", "setting": "a packing-line quality incident",
     "faults": ["label printer", "weighing station", "conveyor timing"],
     "events": [
         ["Printed labels have uneven contrast.", "Print-head temperature varies across batches.",
          "A test label loses part of its barcode.", "Cleaning the test print head restores barcode readability."],
         ["Recorded package weights drift across a shift.", "The scale zero reading changes after a pause.",
          "A certified test mass is recorded above its known weight.", "Recalibrating the test scale removes the weight offset."],
         ["Package spacing varies near the handoff.", "Motor timing fluctuates between production batches.",
          "A camera trace shows packages arriving before the gate opens.", "Slowing the test belt removes the handoff collision."]],
     "neutral": ["The batch identifier matches the work order.", "The inspection sheet uses the current form revision."]},
    {"name": "lab_instrument", "setting": "a laboratory instrument quality check",
     "faults": ["optical alignment", "sample delivery", "temperature controller"],
     "events": [
         ["Reference intensity varies by sensor position.", "The alignment check differs from last week's value.",
          "An alignment target appears displaced in a diagnostic image.", "Recentering the test optical path restores the reference peak."],
         ["Replicate sample volumes have greater spread.", "Delivery pressure fluctuates during intake.",
          "A transparent test tube shows an air gap during aspiration.", "Priming the test delivery line restores stable intake."],
         ["Chamber temperature takes longer to settle.", "Heater duty varies at a fixed setpoint.",
          "An independent probe records a persistent temperature offset.", "Replacing the test control sensor removes the temperature offset."]],
     "neutral": ["The instrument serial number matches the worksheet.", "The operator used the scheduled quality-check slot."]},
    {"name": "data_pipeline", "setting": "a scheduled data-pipeline incident",
     "faults": ["input parser", "join stage", "output writer"],
     "events": [
         ["Parse warnings increased for the latest input batch.", "Rejected input rows share a delimiter pattern.",
          "A diagnostic parse shifts one field into the adjacent column.", "Using the documented delimiter restores the test row structure."],
         ["Intermediate row count expands during the join.", "Repeated join keys appear in a staging sample.",
          "A diagnostic join duplicates records with a single input key.", "Deduplicating a test lookup table removes the extra rows."],
         ["Final write latency increased after processing completed.", "Output acknowledgement timestamps have larger gaps.",
          "A diagnostic write leaves a partial output object.", "Switching the test writer to a fresh destination produces a complete object."]],
     "neutral": ["The batch identifier matches the scheduler entry.", "The run began in its assigned processing window."]},
]


def validate_config(config):
    required = {"design_version", "seed", "cases", "model", "input_usd_per_million",
                "output_usd_per_million", "note_max_output_tokens", "decision_max_output_tokens"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Configuration must contain exactly: " + ", ".join(sorted(required)))
    if config["design_version"] != VERSION:
        raise ValueError("Unsupported design_version")
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an integer from 0 to 2**32 - 1")
    if type(config["cases"]) is not int or config["cases"] not in (6, 24):
        raise ValueError("This development design supports 6 or 24 cases")
    if config["model"] not in MODELS:
        raise ValueError("Use one of the supported pinned models: " + ", ".join(MODELS))
    prices = MODELS[config["model"]]
    for key, expected in zip(("input_usd_per_million", "output_usd_per_million"), prices):
        value = config[key]
        if type(value) not in (float, int) or not math.isfinite(value) or value != expected:
            raise ValueError(f"{key} does not match the configured model; review pricing before changing code")
    for key in ("note_max_output_tokens", "decision_max_output_tokens"):
        if type(config[key]) is not int or not 128 <= config[key] <= 4096:
            raise ValueError(f"{key} must be an integer between 128 and 4096")


def _case(index, seed):
    rng = random.Random(seed + 1009 * index)
    frame = FRAMES[(index // 6) % len(FRAMES)]
    slot = index % 6
    labels = rng.sample(range(100, 999), 17)
    faults = [{"id": f"F-{labels[i]}", "name": name} for i, name in enumerate(frame["faults"])]
    rng.shuffle(faults)
    winner = faults[slot // 2]["id"]
    alignment = "aligned" if slot % 2 else "conflict"
    target = winner if alignment == "aligned" else rng.choice([f["id"] for f in faults if f["id"] != winner])
    events, by_fault = [], {}
    event_ids = rng.sample(range(100, 999), 14)
    scale = rng.choice((1, 2, 3))
    for fault in faults:
        original_index = frame["faults"].index(fault["name"])
        own = []
        for wording, weight in zip(frame["events"][original_index], (3, 2, 4, 4)):
            event = {"id": f"EVT-{event_ids[len(events)]}", "description": wording,
                     "scores": {f["id"]: weight * scale if f == fault else 0 for f in faults}}
            events.append(event)
            own.append(event["id"])
        by_fault[fault["id"]] = own
    for wording in frame["neutral"]:
        events.append({"id": f"EVT-{event_ids[len(events)]}", "description": wording,
                       "scores": {f["id"]: 0 for f in faults}})
    competitor = rng.choice([f["id"] for f in faults if f["id"] != winner])
    observed = by_fault[winner][:2] + by_fault[competitor][:1] + [e["id"] for e in events[-2:]]
    predicted = by_fault[target][2:]
    rng.shuffle(events)
    rng.shuffle(observed)
    return {"id": f"case-{index:03d}", "frame": frame["name"], "setting": frame["setting"],
            "faults": faults, "events": events, "observed_event_ids": observed,
            "target_event_ids": predicted, "target_fault": target,
            "target_alignment": alignment, "base_winner": winner}


def observed_ids(case, condition):
    ids = list(case["observed_event_ids"])
    if condition == "observed_control":
        ids += case["target_event_ids"]
    return ids


def oracle(case, condition):
    observed = set(observed_ids(case, condition))
    totals = {f["id"]: sum(e["scores"][f["id"]] for e in case["events"] if e["id"] in observed)
              for f in case["faults"]}
    maximum = max(totals.values())
    winners = [f for f, score in totals.items() if score == maximum]
    if len(winners) != 1:
        raise ValueError("Case has no unique oracle action")
    return winners[0], totals


def make_plan(config):
    validate_config(config)
    cases = [_case(i, config["seed"]) for i in range(config["cases"])]
    pending = []
    for case in cases:
        for condition in ("observed_summary", "hypothesis_generated"):
            pending.append({"id": f"{case['id']}__{condition}__note", "case_id": case["id"],
                            "stage": "note", "condition": condition, "depends_on": None})
        for condition in CONDITIONS:
            parent = ("hypothesis_generated" if condition == "hypothesis_replay" else condition)
            dependency = f"{case['id']}__{parent}__note" if parent in ("observed_summary", "hypothesis_generated") else None
            pending.append({"id": f"{case['id']}__{condition}__decision", "case_id": case["id"],
                            "stage": "decision", "condition": condition, "depends_on": dependency})
        assert oracle(case, "direct")[0] == case["base_winner"]
        assert oracle(case, "observed_control")[0] == case["target_fault"]
        assert not set(case["target_event_ids"]) & set(case["observed_event_ids"])
    rng = random.Random(config["seed"] ^ 73901)
    nodes, done = [], set()
    while pending:
        ready = [n for n in pending if not n["depends_on"] or n["depends_on"] in done]
        node = rng.choice(ready)
        pending.remove(node)
        nodes.append(node)
        done.add(node["id"])
    return {"design_version": VERSION, "config": dict(config), "cases": cases,
            "nodes": nodes, "planned_calls": len(nodes)}


def _record(case, condition):
    lines = [f"Review {case['setting']}.",
             "Choose the fault to investigate first under this triage rule: sum the table scores "
             "for the events in the current observation record and select the fault with the highest total. "
             "The table defines this task's ranking; it is not a claim about real-world likelihoods.",
             "\nCandidate faults:"]
    lines += [f"{f['id']}: {f['name']}" for f in case["faults"]]
    lines.append("\nEvent catalog and triage scores (possible event types):")
    for event in case["events"]:
        scores = ", ".join(f"{f['id']}={event['scores'][f['id']]}" for f in case["faults"])
        lines.append(f"{event['id']}: {event['description']} Scores: {scores}.")
    lines.append("\nCurrent observation record:")
    lookup = {e["id"]: e for e in case["events"]}
    for identity in observed_ids(case, condition):
        lines.append(f"{identity}: {lookup[identity]['description']}")
    return "\n".join(lines)


def _note_instruction(case, condition):
    if condition == "observed_summary":
        return ("Prepare an 80–120 word factual handoff summarizing the current observation record. "
                "Use its event IDs. Report the existing observations without adding possible test results. "
                "Return JSON with a single note field.")
    lookup = {e["id"]: e for e in case["events"]}
    possible = "\n".join(f"{i}: {lookup[i]['description']}" for i in case["target_event_ids"])
    return (f"Prepare an 80–120 word hypothetical analysis of {case['target_fault']}. "
            "Explain a plausible mechanism connecting this fault to these two possible findings. "
            "These are predictions for a possible diagnostic check, not results already observed. "
            "Keep the explanation conditional. Use the listed event IDs. "
            "Return JSON with a single note field.\nPossible findings:\n" + possible)


def _hypothesis_header(case):
    lookup = {e["id"]: e for e in case["events"]}
    predictions = "\n".join(f"If {case['target_fault']} were responsible, {identity} could occur: "
                            + lookup[identity]["description"] for identity in case["target_event_ids"])
    return "Hypothetical analysis — predictions, not additional observations:\n" + predictions


def messages_for(case, node, note=None):
    condition = node["condition"]
    messages = [{"role": "system", "content": "You assist with operational triage. Return the requested JSON."},
                {"role": "user", "content": _record(case, condition)}]
    if node["stage"] == "note":
        messages.append({"role": "user", "content": _note_instruction(case, condition)})
        return messages
    if condition in ("hypothesis_generated", "hypothesis_replay", "hypothesis_list"):
        messages.append({"role": "user", "content": _note_instruction(case, "hypothesis_generated")})
        # The list is a scripted continuation; generated/replay use the exact same saved note.
        body = _hypothesis_header(case)
        if condition != "hypothesis_list":
            if not isinstance(note, str) or not note.strip():
                raise ValueError("A valid generated hypothetical note is required")
            body += "\n\nConditional explanation:\n" + note
        messages.append({"role": "user" if condition == "hypothesis_replay" else "assistant", "content": body})
    elif condition == "observed_summary":
        if not isinstance(note, str) or not note.strip():
            raise ValueError("A valid factual note is required")
        messages += [{"role": "user", "content": _note_instruction(case, condition)},
                     {"role": "assistant", "content": "Factual handoff:\n" + note}]
    messages.append({"role": "user", "content":
        "Choose the fault to investigate first now. Return predicted_fault, "
        "supporting_observed_event_ids (only the events you are relying on as observations), "
        "and a short reason. Make your choice before writing the evidence list."})
    return messages


def schema_for(case, node):
    if node["stage"] == "note":
        properties = {"note": {"type": "string"}}
    else:
        properties = {"predicted_fault": {"type": "string", "enum": [f["id"] for f in case["faults"]]},
                      "supporting_observed_event_ids": {"type": "array", "items": {"type": "string", "enum": [e["id"] for e in case["events"]]}},
                      "reason": {"type": "string"}}
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_and_score(case, node, text):
    try:
        data = json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Response is not a JSON object") from error
    expected = schema_for(case, node)["properties"]
    if not isinstance(data, dict) or set(data) != set(expected):
        raise ValueError("Response fields do not match the requested schema")
    if node["stage"] == "note":
        note = data["note"]
        if not isinstance(note, str) or not note.strip() or len(note) > 6000:
            raise ValueError("Note must be nonempty text of at most 6000 characters")
        # Structural validation is deliberately not represented as semantic validation.
        return {"note": note, "note_word_count": len(note.split()), "semantic_review": "pending"}
    if data["predicted_fault"] not in [f["id"] for f in case["faults"]]:
        raise ValueError("Unknown predicted fault")
    citations = data["supporting_observed_event_ids"]
    known = {e["id"] for e in case["events"]}
    if (not isinstance(citations, list) or any(not isinstance(x, str) or x not in known for x in citations)
            or len(citations) != len(set(citations))):
        raise ValueError("Observed support must be a list of distinct known event IDs")
    if not isinstance(data["reason"], str) or not data["reason"].strip() or len(data["reason"]) > 6000:
        raise ValueError("Decision requires a nonempty short reason")
    observed = set(observed_ids(case, node["condition"]))
    unsupported = set(citations) - observed
    target_unsupported = unsupported & set(case["target_event_ids"])
    correct, totals = oracle(case, node["condition"])
    choice_error = data["predicted_fault"] != correct
    return {**data, "correct_fault": correct, "oracle_scores": totals,
            "choice_error": choice_error, "source_error": bool(unsupported),
            "target_source_error": bool(target_unsupported),
            "joint_error": choice_error and bool(target_unsupported),
            "any_source_joint_error": choice_error and bool(unsupported),
            "target_choice": data["predicted_fault"] == case["target_fault"],
            "observed_recall": len(set(citations) & observed) / len(observed),
            "cited_unobserved_ids": sorted(unsupported), "target_unobserved_ids": sorted(target_unsupported)}


def note_text(result):
    return result["note"]


def scripted_response(case, node, policy="correct", note=None):
    if policy not in ("correct", "contaminated"):
        raise ValueError("Unknown scripted fixture")
    if node["stage"] == "note":
        if node["condition"] == "observed_summary":
            lookup = {e["id"]: e for e in case["events"]}
            content = " ".join(f"{i}: {lookup[i]['description']}" for i in case["observed_event_ids"])
        else:
            content = (f"If {case['target_fault']} were responsible, the proposed checks might reveal "
                       + " and ".join(case["target_event_ids"]) + ". These possible findings could be connected "
                       "by the same fault affecting both checks. The conditional account describes a "
                       "candidate mechanism for investigation; the checks have no new recorded results.")
        return json.dumps({"note": content})
    citations = observed_ids(case, node["condition"])
    choice = oracle(case, node["condition"])[0]
    if policy == "contaminated" and node["condition"] in ("hypothesis_generated", "hypothesis_replay"):
        citations += case["target_event_ids"]
        choice = case["target_fault"]
    return json.dumps({"predicted_fault": choice, "supporting_observed_event_ids": citations,
                       "reason": "Scripted software-test response; not model behavior."})
