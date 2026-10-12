# Hypothesis and evidence

This pilot tests whether explaining a hypothetical finding makes a language
model more likely to treat it as an observation later. GPT-4.1-mini made no such
errors in the completed run. The original source stayed visible, so the
experiment tests whether the model keeps track of source labels while adding
its own explanations.

## Experiment

Each inspection record contains one observed finding and two hypothetical findings. The model writes either a neutral restatement or a causal explanation of the hypothetical findings, then classifies them as observed, hypothetical, or absent from the original record. An additional finding, absent from the record, serves as a control.

Classification happens immediately or after eight other case records. The same generated note is used for both conditions, and the original record remains visible throughout.

Finding status rotates across cases within each template, so a particular
finding is not always observed or always hypothetical. The observed and absent
findings also check for errors beyond confusing hypotheses with observations.

## Results

Run `status-001` used `gpt-4.1-mini-2025-04-14`, with 32 paired cases drawn from eight template families. All 64 note-generation calls and 128 classification calls completed successfully.

| Note | Classification timing | Hypothetical findings labelled observed | All classification errors |
|---|---|---:|---:|
| Restatement | Immediate | 0/64 | 0/128 |
| Restatement | After eight other cases | 0/64 | 0/128 |
| Causal explanation | Immediate | 0/64 | 0/128 |
| Causal explanation | After eight other cases | 0/64 | 0/128 |

Explaining the hypothetical findings and adding eight intervening records did
not induce the proposed error. The model could still consult explicit source
labels, and the longer prompts contained only about 1,400 input tokens. This
leaves memory after source removal untested. Note lengths also differed between
conditions, and repeated classifications within cases are not independent
samples. No further runs are planned for this design.

The [results summary](results/source-status.md) includes the controls, note
checks, and limitations; the [JSON summary](results/source-status.json) contains
the numerical results. These are aggregate exports. The original raw requests
and responses are not included in the public repository.

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
