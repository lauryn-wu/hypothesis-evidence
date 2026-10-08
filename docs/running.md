# Running and recovery

Use Python 3.10+ on macOS or Linux. The code uses only the standard library. Run commands from the repository root.

## Before a live run

Run the unit tests and offline smoke test in the README. Inspect `runs/plan-smoke/prompts.json` to see all prompt templates and schemas. Dependent prompts contain a placeholder until a note is generated; live `requests/` files contain the exact text sent.

`OPENAI_API_KEY` is read from the environment. There is no `.env` loader. On your Mac, enter it without echoing it:

```bash
read -s OPENAI_API_KEY
```

Paste the key and press Enter. Then run:

```bash
export OPENAI_API_KEY
```

No API key is needed for `plan`, the scripted backend, or unit tests. Keys are never included in saved request files or the run manifest. `runs/`, `.env`, and archives are excluded from Git.

## Outputs

- `plan.json`: cases, oracles' inputs, conditions, and schedule.
- `manifest.json`: backend, configuration/code fingerprints, and limits.
- `requests/`: exact messages and requested response schema.
- `responses/`: raw provider responses, or explicitly labelled scripted fixtures.
- `results/`: parsed results, scores, errors, and checkpoints.
- `summary.md` and `summary.json`: descriptive results, paired contrasts, and missingness.
- `notes.md`: all generated notes together for semantic review.
- `ledger.json`: call reservations and token accounting for live runs.

The default backend is scripted. Its clean and deliberately contaminated policies test scoring and workflow only. Neither is empirical evidence.

## Larger runs

After reviewing the smoke test, use the larger command in the README. An optional second model uses the same cases and a separate directory:

```bash
python3 -m evidence_pilot run \
  --config configs/pilot-4o-mini.json --backend openai \
  --out runs/pilot-4o-001 --budget-usd 2 --max-calls 192
```

The 192 calls are the whole run, not 192 per condition. Each case has two note calls and six decisions. A failed note blocks its dependent decisions, so a damaged run may make fewer calls.

Both supplied models are supported by the Responses API and structured outputs. Pinned snapshots and standard text prices were checked in the official model documentation on 2026-10-08: [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini). The [structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs) describes the request format. Account access and rate limits may differ. If the provider rejects a request, inspect the error; the runner does not fall back to another model.

## Spending limits

A live run requires both `--budget-usd` and `--max-calls`. Before each HTTP request, the runner reserves a conservative amount using the serialized request byte length plus 4,096 input tokens and the full output token allowance. Settled usage uses ordinary input prices, including for cached tokens, so it can overestimate the actual bill.

This is a client-side safeguard, not a provider-enforced billing limit. It depends on correct token prices and a conservative input bound. The code permits only the two documented model/price pairs. If prices change, review the allowlist and configuration before starting a new run. An unexpected reservation overrun records actual usage and halts later calls. The budget and maximum request count apply to one run directory, not every command combined.

An HTTP or network error stops the run without retrying. Uncertain requests keep their reservations. Invalid structured content is saved as invalid; an invalid note blocks its dependents while unrelated cases can continue. No invalid response is counted as correct.

## Interruptions

Add `--resume` to the original run command. It must use the same directory, configuration, source code, budget, and request limit. Completed, failed, and uncertain requests are not replayed. Remaining untouched requests can proceed within the original limits.

A request interrupted before a result checkpoint remains missing even if the server may have completed it. This deliberately favors avoiding duplicate billing over automatically filling the dataset. Do not delete checkpoints or adjust a ledger to force a retry. A later explicitly labelled run can use a new directory; retain the incomplete run rather than merging selected responses.

Rebuild the summary without making requests:

```bash
python3 -m evidence_pilot analyze runs/smoke-001
```

This uses saved parsed records; it is not a new model run. The code fingerprint protects resumes, so finish or preserve a run before pulling code changes.
