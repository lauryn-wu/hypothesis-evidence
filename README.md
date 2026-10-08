# Hypothesis and evidence

This project tests whether a language model confuses hypothetical findings with observations after explaining how they could occur.

The completed pilot found no such errors in GPT-4.1-mini. The code, experimental design, and results are included here.

## Experiment

Each inspection record contains one observed finding and two hypothetical findings. The model writes either a neutral restatement or a causal explanation of the hypothetical findings, then classifies them as observed, hypothetical, or absent from the original record. An additional finding, absent from the record, serves as a control.

Classification happens immediately or after eight other case records. The same generated note is used for both conditions, and the original record remains visible throughout.

## Results

Run `status-001` used `gpt-4.1-mini-2025-04-14`, with 32 paired cases drawn from eight template families. All 64 note-generation calls and 128 classification calls completed successfully.

| Note | Classification timing | Hypothetical findings labelled observed | All classification errors |
|---|---|---:|---:|
| Restatement | Immediate | 0/64 | 0/128 |
| Restatement | After eight other cases | 0/64 | 0/128 |
| Causal explanation | Immediate | 0/64 | 0/128 |
| Causal explanation | After eight other cases | 0/64 | 0/128 |

The predicted elaboration effect did not appear. This is a narrow result: the model could consult explicit source labels, and even the longer prompts contained only about 1,400 input tokens. Note lengths also differed between writing conditions. Repeated conditions and findings within cases are not independent samples.

See the [results summary](results/source-status.md) for controls, note checks, and limitations, or the [JSON summary](results/source-status.json) for the numerical results. The current design is complete; no further runs are planned.

## Run locally

Python 3.10 or newer on macOS or Linux. Only the standard library is required.

```bash
git clone https://github.com/lauryn-wu/hypothesis-evidence.git
cd hypothesis-evidence
python3 -m unittest discover -s tests -v
```

Run a four-case check with scripted responses:

```bash
python3 -m evidence_pilot run \
  --config configs/source-status-smoke.json --out runs/status-offline-001
```

This checks the software without API calls. [Running instructions](docs/source-status-running.md) cover prompt export, live runs, saved outputs, and recovery after interruptions.

## Design and earlier work

- [Source-status experiment and related work](docs/source-status-design.md)
- [Original fault-ranking experiment](docs/design.md), retained with its own configurations and analysis
- [Original experiment commands](docs/running.md)
