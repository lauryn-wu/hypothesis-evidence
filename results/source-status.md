# Source-status pilot results

Run `status-001` tested whether explaining hypothetical findings increases later reports that those findings were observed. GPT-4.1-mini classified every finding correctly in all four conditions. The pilot did not support the predicted effect, and the current design has been shelved.

## Setup

- Model: `gpt-4.1-mini-2025-04-14`, temperature 0.
- Data: 32 generated cases from eight template families, with four finding-status rotations per family.
- Conditions: neutral restatement or causal explanation, each tested immediately and after eight intervening case records.
- Each case: one observed finding, two hypothetical findings, and one finding absent from the original record.
- Schedule: 64 note-generation calls and 128 classification calls. All 192 responses were valid, with no missing outcomes.
- Configuration: [`configs/source-status.json`](../configs/source-status.json).
- Code revision: [`5d261f0`](https://github.com/lauryn-wu/hypothesis-evidence/commit/5d261f081228bbc63a3bc48aec456174137721e3).

Both timing conditions reused the exact same note. The original record stayed in context. Interleaving added eight supplied case records, without additional model-generated turns.

## Findings

| Condition | Valid cases | Hypothetical findings labelled observed | All classification errors |
|---|---:|---:|---:|
| Restatement, immediate | 32/32 | 0/64 | 0/128 |
| Restatement, interleaved | 32/32 | 0/64 | 0/128 |
| Causal explanation, immediate | 32/32 | 0/64 | 0/128 |
| Causal explanation, interleaved | 32/32 | 0/64 | 0/128 |

Actual observations and absent-finding controls were also classified correctly. Every paired contrast was zero, including the primary interaction: the elaboration effect after intervening cases minus the elaboration effect immediately.

The 32 paired cases reuse eight template families. Individual findings and repeated conditions are not independent samples. No confidence intervals or population error-rate estimates are reported.

## Generated notes

| Check | Restatement | Causal explanation |
|---|---:|---:|
| Valid notes | 32/32 | 32/32 |
| Mean word count | 65.1 | 76.1 |
| Within the requested 70–90 word band | 9/32 | 31/32 |
| Passed literal check-ID constraints | 30/32 | 30/32 |

Eighteen of the 32 note pairs were within ten words of each other. Two restatements repeated both target IDs; two explanations also mentioned an existing observed check. No note mentioned the absent finding's check ID. All valid notes were retained.

A qualitative inspection of all 64 notes found that explanations added causal mechanisms while target findings remained conditional. This was an informal review, without independent annotators. The automatic JSON diagnostics retain their original `semantic_review_pending_n` values; literal ID checks do not establish semantic validity.

## Interpretation

This model handled the explicitly labelled source-status task correctly. That result gives no empirical basis for extending the current pilot into a larger study of the proposed failure.

Several features limit the conclusion. Original source labels and note-writing instructions remained visible. The longer prompts contained 1,372–1,435 input tokens, so the intervening records imposed a modest context load. The final question explicitly asked for source status. Note length was requested rather than enforced, and explanations were longer on average. The experiment covers one model and one formal task across reused templates; it does not establish source-tracking reliability in other settings.

## Data and verification

The [JSON summary](source-status.json) contains the configuration, condition counts, confusion matrices, paired contrasts, note diagnostics, and fingerprints from the completed run. It is an aggregate export; raw requests and responses are not included in this repository.

All 192 saved responses were checked against the request ledger. Request payload hashes, generated prompts, schemas, parsed labels, and saved scores matched. Recomputing the full run summary reproduced the saved summary exactly. Directly comparing raw classification labels with the case records gave the same zero-error counts.

The [design document](../docs/source-status-design.md) describes the comparisons and their limitations.
