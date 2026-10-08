# Running the source-status pilot

Run these commands from the repository root on `main`. Python 3.10+ on macOS or Linux is sufficient. No GPU or additional package is needed. The completed pilot's [results](../results/source-status.md) are included in the repository.

## Offline checks

```bash
python3 -m unittest discover -s tests -v
python3 -m evidence_pilot plan \
  --config configs/source-status.json --out runs/status-plan-001
python3 -m evidence_pilot run \
  --config configs/source-status-smoke.json --out runs/status-offline-001
```

The plan contains 192 requests for 32 cases. Dependent prompt exports contain a note placeholder; the eventual `requests/` files contain the exact note sent. The offline run contains four cases and 24 scripted responses. Neither command requires an API key or makes model calls.

The deliberately incorrect software fixture is also available:

```bash
python3 -m evidence_pilot run \
  --config configs/source-status-smoke.json --scripted-policy contaminated \
  --out runs/status-fixture-001
```

This fixture labels both hypothetical findings as observed in every condition. Its elaboration and interleaving effects are zero by construction. It verifies scoring; it does not simulate an empirical finding.

## Live development run

Set `OPENAI_API_KEY` locally if needed:

```bash
read -s OPENAI_API_KEY
```

Paste the key, press Enter, then run:

```bash
export OPENAI_API_KEY
```

Start the fixed 32-case schedule:

```bash
python3 -m evidence_pilot run \
  --config configs/source-status.json --backend openai \
  --out runs/status-001 --budget-usd 2 --max-calls 192
```

There are 64 note calls and 128 classification calls in total. The eight intervening records are supplied text and require no additional calls. Failed notes block their two dependent classifications, so an incomplete run can make fewer requests.

On a Mac, prefix the live command with `caffeinate -i` to keep the computer awake while the process runs.

The model, price allowlist, transport, ledger, and API failure handling are shared with the original runner. The client reserves a conservative request cost before each HTTP call, settles returned usage, and retains uncertain reservations. The cap applies to one run directory and is not a provider-enforced billing limit. No retries, replacement notes, or fallback models are automatic. See [the original accounting documentation](running.md#spending-limits) for details.

## Review outputs

```bash
cat runs/status-001/summary.md
cat runs/status-001/notes.md
```

The output directory contains the fixed plan, manifest and code fingerprint, exact requests, raw API responses, parsed results, note diagnostics, summary tables, and ledger. `summary.json` also contains per-case/per-family results, confusion matrices, and missingness bounds. A valid JSON response is not automatically a semantically valid note.

To recompute the summary without API calls:

```bash
python3 -m evidence_pilot analyze runs/status-001
```

To share the outputs for review:

```bash
zip -r status-001.zip runs/status-001
```

The runner does not save the API key. Run directories and ZIP files are excluded from Git.

## Interruptions and existing runs

To continue untouched requests, repeat the live command with `--resume`:

```bash
python3 -m evidence_pilot run \
  --config configs/source-status.json --backend openai \
  --out runs/status-001 --budget-usd 2 --max-calls 192 --resume
```

Use the same configuration, code, limits, and output directory. Completed, failed, interrupted, and budget-blocked requests are not replayed. If an interrupted request was a note, both classifications depending on it remain unavailable. Missing results stay out of complete-pair contrasts and are represented in the missingness bounds.

Do not delete checkpoints or edit the ledger to fill gaps. Do not merge selected outputs from repeated runs. Use a new, explicitly named directory if a separate run is justified.

The original fault-ranking commands still select the original design, and existing completed results can still be analyzed. For resuming an unfinished original run, use the exact code revision that created it: the shared runner's code fingerprint changes on this branch. Keep original directories such as `runs/smoke-001` separate from `runs/status-001`.
