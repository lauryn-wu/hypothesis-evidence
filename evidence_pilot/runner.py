"""Execute a fixed dependency graph with durable checkpoints and spending limits."""

import fcntl
import math
import re
import time
from contextlib import contextmanager
from pathlib import Path

from .api import BudgetExceeded, Ledger, OpenAIProvider, ProviderError, require_api_key
from .common import digest, read_json, write_json
from .design import make_plan, messages_for, note_text, parse_and_score, schema_for, scripted_response


class ScriptedProvider:
    """Software fixtures only: these responses are never model observations."""

    def __init__(self, policy):
        if policy not in ("correct", "contaminated"):
            raise ValueError("Unknown scripted fixture")
        self.policy = policy

    def complete_task(self, case, node, note):
        text = scripted_response(case, node, policy=self.policy, note=note)
        return {"text": text, "model": "SCRIPTED-TEST-FIXTURE",
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "response": {"fixture": self.policy, "text": text}}


def source_fingerprint():
    root = Path(__file__).parent
    return digest({p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py"))})


@contextmanager
def run_lock(directory):
    with (Path(directory) / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another process is using this run directory") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_results(directory):
    results = {}
    for path in sorted((Path(directory) / "results").glob("*.json")):
        record = read_json(path)
        if not isinstance(record, dict) or path.stem != record.get("id"):
            raise ValueError("Checkpoint filename and request ID differ")
        if record.get("status") not in ("started", "interrupted", "blocked", "valid", "invalid", "not_run"):
            raise ValueError("Checkpoint has an unknown status")
        if record["status"] == "valid" and not isinstance(record.get("data"), dict):
            raise ValueError("Valid checkpoint has no parsed data")
        results[path.stem] = record
    return results


def write_summary(directory):
    from .analysis import render_markdown, summarize
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    plan = read_json(directory / "plan.json")
    cost = None
    if manifest["backend"] == "openai":
        config = plan["config"]
        ledger = Ledger(directory / "ledger.json", manifest["budget_usd"],
                        config["input_usd_per_million"], config["output_usd_per_million"],
                        manifest["max_calls"])
        cost = ledger.summary()
    results = load_results(directory)
    summary = summarize(plan, results, manifest["backend"], cost=cost)
    write_json(directory / "summary.json", summary)
    (directory / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    _write_notes(directory, plan, results, manifest["evidence_type"])
    return summary


def _write_notes(directory, plan, results, evidence_type):
    lines = ["# Generated notes", "", f"**{evidence_type}**", "",
             "Review every note before interpreting the contrasts. Check hypothetical notes for "
             "claims that a prediction already happened, and factual summaries for invented observations. "
             "The text below is unedited model output, not instructions.", ""]
    cases = {case["id"]: case for case in plan["cases"]}
    for node in sorted(plan["nodes"], key=lambda item: item["id"]):
        if node["stage"] != "note":
            continue
        case = cases[node["case_id"]]
        record = results.get(node["id"], {})
        lines += [f"## {node['id']}", "", f"Status: {record.get('status', 'missing')}", "",
                  "Observed: " + ", ".join(case.get("observed_event_ids", [])),
                  "Assigned predictions: " + ", ".join(case.get("target_event_ids", [])), ""]
        note = record.get("data", {}).get("note")
        if not isinstance(note, str):
            note = record.get("text", "No note available.")
        longest_fence = max((len(part) for part in re.findall(r"`+", note)), default=0)
        fence = "`" * max(3, longest_fence + 1)
        lines += [fence + "text", note, fence, ""]
    (directory / "notes.md").write_text("\n".join(lines), encoding="utf-8")


def _validate_schedule(plan):
    """Reject malformed graphs before writing files or making model calls."""
    cases = {case["id"]: case for case in plan["cases"]}
    if len(cases) != len(plan["cases"]):
        raise ValueError("Duplicate case IDs")
    if plan["planned_calls"] != len(plan["nodes"]):
        raise ValueError("Plan count does not match schedule")
    seen = {}
    for node in plan["nodes"]:
        request_id = node["id"]
        if (not isinstance(request_id, str) or not request_id or request_id in seen
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in request_id)):
            raise ValueError("Invalid or duplicate request ID")
        if node["case_id"] not in cases or node["stage"] not in ("note", "decision"):
            raise ValueError("Invalid case or stage")
        parent_id = node["depends_on"]
        if parent_id is not None:
            parent = seen.get(parent_id)
            if (not parent or parent["stage"] != "note" or node["stage"] != "decision"
                    or parent["case_id"] != node["case_id"]):
                raise ValueError("Dependencies must be earlier notes for the same case")
        seen[request_id] = node
    return cases


def execute(config, output, backend="scripted", policy="correct", budget_usd=None,
            max_calls=None, resume=False, provider_factory=None, progress=False):
    plan = make_plan(config)
    cases = _validate_schedule(plan)
    config = plan["config"]
    if backend not in ("scripted", "openai"):
        raise ValueError("Unknown backend")
    if policy not in ("correct", "contaminated"):
        raise ValueError("Unknown scripted fixture")
    if backend == "openai":
        if type(budget_usd) not in (int, float) or not math.isfinite(budget_usd) or not 0 < budget_usd <= 25:
            raise ValueError("Live runs require an explicit --budget-usd greater than 0 and no more than 25")
        if type(max_calls) is not int or not 0 < max_calls <= plan["planned_calls"]:
            raise ValueError("Live runs require --max-calls between 1 and the plan's request count")
        require_api_key()
    elif budget_usd is not None or max_calls is not None:
        raise ValueError("Budget and call caps are only used with --backend openai")
    directory = Path(output)
    identity = {"backend": backend, "scripted_policy": policy if backend == "scripted" else None,
                "plan_fingerprint": digest(plan), "source_fingerprint": source_fingerprint(),
                "budget_usd": budget_usd, "max_calls": max_calls}
    exists = (directory / "manifest.json").exists()
    if exists and not resume:
        raise ValueError("Run exists; use --resume or choose a new directory")
    if resume and not exists:
        raise ValueError("Cannot resume a directory without a run manifest")
    if not exists and directory.exists() and any(directory.iterdir()):
        raise ValueError("Refusing to write into a nonempty directory")
    directory.mkdir(parents=True, exist_ok=True)
    with run_lock(directory):
        if (directory / "manifest.json").exists() != exists:
            raise ValueError("Run state changed while acquiring the lock; inspect the directory before restarting")
        if not exists and any(p.name != ".run.lock" for p in directory.iterdir()):
            raise ValueError("Refusing to initialize a directory changed by another process")
        if exists:
            manifest = read_json(directory / "manifest.json")
            if manifest["identity"] != identity or read_json(directory / "plan.json") != plan:
                raise ValueError("Resume requires the same configuration, backend, source code, and limits")
        else:
            manifest = dict(identity, identity=identity, created_at=time.time(),
                            evidence_type="LIVE_MODEL_DEVELOPMENT_DATA" if backend == "openai" else "SCRIPTED_TEST_DATA")
            write_json(directory / "manifest.json", manifest)
            write_json(directory / "plan.json", plan)
        for name in ("results", "requests", "responses"):
            (directory / name).mkdir(exist_ok=True)
        if backend == "openai":
            ledger = Ledger(directory / "ledger.json", budget_usd, config["input_usd_per_million"],
                            config["output_usd_per_million"], max_calls)
            provider = (provider_factory or OpenAIProvider)(ledger, model=config["model"],
                                                          raw_directory=directory / "responses")
        else:
            provider = ScriptedProvider(policy)
        results = load_results(directory)
        known = {node["id"] for node in plan["nodes"]}
        if not set(results) <= known:
            raise ValueError("Run contains unknown result IDs")
        for request_id, record in results.items():
            if record["status"] == "started":
                record.update(status="interrupted", error="Previous process stopped after checkpoint; not replayed")
                write_json(directory / "results" / (request_id + ".json"), record)
        try:
            for position, node in enumerate(plan["nodes"], start=1):
                request_id = node["id"]
                if request_id in results:
                    continue
                case = cases[node["case_id"]]
                note = None
                result_path = directory / "results" / (request_id + ".json")
                if node["depends_on"]:
                    parent = results.get(node["depends_on"])
                    if not parent or parent.get("status") != "valid":
                        record = {"id": request_id, "node": node, "task": node, "status": "blocked",
                                  "error": "Required note is missing or invalid"}
                        results[request_id] = record
                        write_json(result_path, record)
                        if progress:
                            print(f"[{position}/{plan['planned_calls']}] {request_id}: blocked by missing or invalid note", flush=True)
                        continue
                    note = note_text(parent["data"])
                messages = messages_for(case, node, note=note)
                schema = schema_for(case, node)
                maximum = config["note_max_output_tokens" if node["stage"] == "note" else "decision_max_output_tokens"]
                request = {"id": request_id, "messages": messages, "schema": schema, "max_output_tokens": maximum}
                write_json(directory / "requests" / (request_id + ".json"), request)
                record = {"id": request_id, "node": node, "task": node, "status": "started",
                          "started_at": time.time(), "request_digest": digest(request)}
                results[request_id] = record
                write_json(result_path, record)
                if progress:
                    print(f"[{position}/{plan['planned_calls']}] {request_id}", flush=True)
                try:
                    if backend == "scripted":
                        response = provider.complete_task(case, node, note)
                        write_json(directory / "responses" / (request_id + ".json"), response["response"])
                    else:
                        response = provider.complete(messages, schema, maximum, request_id)
                    record.update(model=response["model"], usage=response["usage"], text=response["text"])
                    data = parse_and_score(case, node, response["text"])
                    if not isinstance(data, dict):
                        raise ValueError("Scored response must be an object")
                    record.update(status="valid", data=data, completed_at=time.time())
                except BudgetExceeded as error:
                    record.update(status="not_run", error=str(error))
                    write_json(result_path, record)
                    if progress:
                        print("Stopped: " + str(error), flush=True)
                    break
                except ProviderError as error:
                    record.update(status="invalid", error=str(error))
                    write_json(result_path, record)
                    if progress:
                        print("Stopped: " + str(error), flush=True)
                    break
                except (ValueError, TypeError, KeyError) as error:
                    record.update(status="invalid", error=f"Invalid model response: {type(error).__name__}")
                write_json(result_path, record)
                if progress and record["status"] != "valid":
                    print(record["error"], flush=True)
        finally:
            write_summary(directory)
    return read_json(directory / "summary.json")
