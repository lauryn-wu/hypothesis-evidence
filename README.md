# Hypothesis and evidence

Does explaining a hypothetical event make a model more likely to treat it as an observation later?

This pilot uses incident records with known observations and a fixed triage rule. A model writes a conditional explanation of possible findings, then selects a fault to investigate and identifies the observations supporting its choice. The original record and hypothetical labels stay visible.

The comparisons include a brief list of the same possible findings, a factual summary, a direct decision, and the same explanation replayed as a supplied note. A separate control adds the predicted findings to the actual record, where they should change the answer.

## Run locally

Python 3.10 or newer on macOS or Linux. No packages or GPU are required.

```bash
git clone https://github.com/lauryn-wu/hypothesis-evidence.git
cd hypothesis-evidence
python3 -m unittest discover -s tests -v
```

Inspect the cases and prompts without making API calls:

```bash
python3 -m evidence_pilot plan --config configs/smoke.json --out runs/plan-smoke
```

Run the software check with scripted responses:

```bash
python3 -m evidence_pilot run --config configs/smoke.json --out runs/offline-001
```

## Run the pilot

Set `OPENAI_API_KEY` in your terminal. If you need to enter it, run `read -s OPENAI_API_KEY`, paste the key and press Enter, then run `export OPENAI_API_KEY`. The key is not displayed or saved in shell history.

Start with the six-case smoke test:

```bash
python3 -m evidence_pilot run \
  --config configs/smoke.json --backend openai \
  --out runs/smoke-001 --budget-usd 1 --max-calls 48
cat runs/smoke-001/summary.md
```

This makes at most **48 API calls** using `gpt-4.1-mini-2025-04-14`. The budget is a client-side limit based on configured token prices. Read the generated notes in `runs/smoke-001/notes.md` before interpreting the results.

The larger configuration contains **24 cases and at most 192 calls**:

```bash
python3 -m evidence_pilot run \
  --config configs/pilot.json --backend openai \
  --out runs/pilot-001 --budget-usd 3 --max-calls 192
```

Run directories are excluded from Git. They contain exact prompts, responses, the fixed schedule, accounting, and summaries. Add `--resume` to the original command after an interruption; uncertain requests are not automatically repeated. See [running and recovery](docs/running.md).

## What the results mean

The summary separates citing an unobserved event, choosing the wrong action, and doing both. A wrong action alone is not evidence that the model confused a hypothesis with an observation.

Generated prose needs manual review: a note that asserts a prediction already happened introduces a different explanation for any later error. These are development experiments in a constrained task, with no live results included. Scripted responses only test the software.

See [design and interpretation](docs/design.md) for the controls, measurements, limitations, and related work.
