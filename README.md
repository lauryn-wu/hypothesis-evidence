# Hypothesis and evidence

Does explaining a hypothetical finding make a language model more likely to later report it as an observation?

The current pilot asks a model to distinguish completed check results, hypothetical findings, and findings absent from an inspection record. It compares a neutral restatement with a causal explanation, testing each immediately and after eight other case records. The original record stays available throughout.

There are 32 cases drawn from eight template families. Each case needs two note-generation calls and four classification calls: **192 API calls for the full pilot**. The same saved note is used for both timing conditions. The intervening records are generated locally and require no extra API calls.

The original fault-ranking experiment remains available with its original configurations. Its design and analysis are separate from this pilot.

## Setup

Python 3.10 or newer on macOS or Linux. No packages or GPU are required.

From an existing clone:

```bash
git fetch origin
git switch source-status-pilot
git pull --ff-only
python3 -m unittest discover -s tests -v
```

For a fresh clone:

```bash
git clone --branch source-status-pilot https://github.com/lauryn-wu/hypothesis-evidence.git
cd hypothesis-evidence
python3 -m unittest discover -s tests -v
```

## Check the experiment offline

Export the full schedule and prompts:

```bash
python3 -m evidence_pilot plan \
  --config configs/source-status.json --out runs/status-plan-001
```

Run the four-case software check with scripted responses:

```bash
python3 -m evidence_pilot run \
  --config configs/source-status-smoke.json --out runs/status-offline-001
cat runs/status-offline-001/summary.md
```

Scripted responses check the implementation. They are not model results.

## Run the pilot

Set `OPENAI_API_KEY` in your terminal if it is not already available. Run `read -s OPENAI_API_KEY`, paste the key and press Enter, then run `export OPENAI_API_KEY`.

```bash
python3 -m evidence_pilot run \
  --config configs/source-status.json --backend openai \
  --out runs/status-001 --budget-usd 2 --max-calls 192
cat runs/status-001/summary.md
```

This uses `gpt-4.1-mini-2025-04-14`. The dollar limit is a client-side safeguard based on configured token prices, not a provider billing limit. The default backend is offline; paid requests require `--backend openai` and both limits.

Read `runs/status-001/notes.md` alongside the summary. The two writing conditions request the same length and number of check-ID mentions. Actual lengths and mentions are reported, and every valid note is retained. The software does not decide whether a note stayed hypothetical or followed the requested writing style.

Run directories contain the exact prompts, raw responses, parsed outcomes, schedule, and accounting. They are excluded from Git. After an interruption, add `--resume` to the original command. Completed and uncertain requests are not repeated. See [source-status running instructions](docs/source-status-running.md).

## Interpretation

The main comparison asks whether elaboration adds more hypothetical-to-observed errors after intervening cases than it does immediately. Separate outputs show observed-result errors, false mentions of absent findings, and confusion matrices. Missing responses remain missing.

This is a development screen. A difference needs note review and follow-up on new cases before supporting a broader claim. No live results from the new experiment are included here.

- [Source-status design, controls, and related work](docs/source-status-design.md)
- [Original fault-ranking design](docs/design.md)
- [Original experiment commands and recovery](docs/running.md)
