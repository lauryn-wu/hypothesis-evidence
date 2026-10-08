# Design

## Question

Can elaborating an explicitly hypothetical explanation increase later reports that its predicted findings were observed?

The measured behavior is an overt source-status error: naming an event absent from the observation record in a field asking for observed support. It is not a measurement of internal belief or hidden reasoning. A choice can be wrong without this error, and this error can occur alongside a correct choice.

## Tasks

Each case has three faults and a catalog of fourteen possible event types, with neutral randomized IDs. Five events appear in the current observation record. An explicit scoring table specifies how to rank faults for investigation: add the contributions of the observed events and choose the highest total. The initial winner is unique.

Two currently unobserved events support the assigned hypothetical fault. Half the cases assign the current winner (aligned); half assign another fault (conflict). Promoting the two predictions into genuine observations makes the assigned fault the unique winner. The aligned controls let us detect source errors even when they do not produce an action error.

This formal rule gives an exact action oracle without treating a plausible alternative diagnosis as objectively false. It also limits ecological validity: these are constrained triage tasks, not clinical diagnoses, real incident investigations, or estimates of deployment error rates.

The 24-case schedule contains four surface frames: service incidents, packing lines, instrument checks, and data pipelines. They share the same formal structure. Within each six-case frame, winner position and target alignment are fully crossed. Fault identities, event IDs, event order, observation order, and a score multiplier are randomized. Frames and repeated conditions are not independent domains or independent samples.

## Conditions

Every case has six decision calls and two note-generation calls.

| Condition | What precedes the decision | Calls per case |
|---|---|---:|
| `direct` | Original record | 1 |
| `observed_summary` | Model-written factual summary, with original record retained | 2 |
| `hypothesis_list` | Scripted list of the two conditional predictions | 1 |
| `hypothesis_generated` | The same list plus a model-written conditional explanation | 2 |
| `hypothesis_replay` | Exact same hypothetical text, changed from assistant to user role | 1 |
| `observed_control` | Predicted events actually added to the observation record | 1 |

The generated hypothesis and factual summary each request 80–120 words. Length is measured, not forced by rejecting shorter or longer notes. They are not exactly token-matched. The list and generated conditions share the preceding hypothetical-writing request and prediction list. The list is a scripted conversation continuation; it is not represented as an actual note-generation sample. Generated and replay conditions share the exact note and other message contents; only its role changes. This is a role-attribution intervention, not a claim that the model remembers authoring text or identifies an external person.

All decision calls retain the original case. Hypothetical headings remain visible. No hidden chain of thought is requested. The decision response asks for the action before an evidence list and short reason; the evidence request may itself make source checking more salient. A null therefore applies to this prompted task.

## Schedule

| Configuration | Cases | Frames | Notes | Decisions | Maximum calls |
|---|---:|---:|---:|---:|---:|
| `smoke.json` | 6 | 1 | 12 | 36 | 48 |
| `pilot.json` | 24 | 4 | 48 | 144 | 192 |
| `pilot-4o-mini.json` | 24 | 4 | 48 | 144 | 192 |

The smoke test checks mechanics and uses a different seed from the larger pilot. They still share templates and must not be presented as independent confirmation. The two larger configurations use identical cases but different pinned models. Both are OpenAI models; this is not cross-provider replication. Calls use temperature zero, which does not guarantee identical provider outputs. Order is seeded and randomized subject to note dependencies. There are no automatic retries or repeated draws until an error appears.

## Outcomes and contrasts

The primary exploratory contrast is `hypothesis_generated − hypothesis_list` in the rate of citing any unobserved event as observed support. Positive values indicate more overt source errors after elaboration. Also report the narrower rate involving the two assigned predictions, action errors, and the joint event of action error plus citing an assigned prediction as observed. `any_source_joint_error` in raw scored records includes other unobserved events too.

Generated-minus-summary, generated-minus-direct, and generated-minus-replay are secondary contrasts. All are paired within complete cases. Aligned and conflict strata remain separate in the JSON output. The positive control has its own updated oracle and does not enter the main contrast.

This design does not isolate elaboration from its added length, repetition, or continuation framing. The factual-summary and role-replay controls help interpret a pattern; they do not remove every confound. A positive pilot would justify a follow-up with tighter length/repetition matching and held-out tasks, not a broad claim that imagination changes model belief.

Empty and partial support lists are valid: the model need not cite every neutral or irrelevant observation. Citation coverage (`observed_recall`) is descriptive, not an accuracy score. Unsupported use in free prose can escape the structured citation metric; inspect reasons as well. Unknown or duplicate event IDs are invalid outputs, not evidence of this mechanism.

Missing, refused, truncated, malformed, blocked, and interrupted outcomes never become correct responses. Summaries show planned and valid denominators, complete pairs, and worst-case bounds for missing binary outcomes. These bounds are not confidence intervals. The small development screen reports no significance tests or inferential intervals.

## Review before interpretation

Read every saved note in `notes.md`, including factual summaries. Do this before selecting apparent positive examples. Check whether each hypothetical explanation remains conditional, references the assigned possibilities, and refrains from asserting that an unobserved event already happened. Check summaries for invented observations. Preserve the raw text and record any issues; do not silently repair notes or regenerate them.

The software checks response structure, not the truth or grammatical force of free prose. A hypothetical heading cannot rescue a sentence that explicitly claims the current log contains an invented event. Such cases confound hypothetical elaboration with asserted misinformation. Automatic contrasts remain provisional until that review; the code does not automatically exclude semantically bad notes. Report all cases and any explicitly documented sensitivity analysis rather than filtering until the effect grows.

## Decision after the pilot

- If choices or source attribution fail even on direct records, inspect basic task comprehension and prompts before studying the proposed mechanism.
- If genuine-evidence controls do not follow the updated record, inspect whether the task or scoring is too difficult.
- If the contrast is driven by missing outputs or notes that assert false observations, it does not establish the proposed phenomenon.
- If all source outcomes remain clean in the bounded pilot, stop this version. Do not remove labels or demand a particular explanation to manufacture a result.
- If clean source errors appear, inspect paired examples and reasons, then freeze a follow-up with new templates, explicit repetition/length controls, and another model family.

## Related work

This pilot is motivated by adjacent work, not a claim to have established a new effect.

- [Reality Monitoring in Large Language Models](https://arxiv.org/abs/2607.23927) studies source attribution for supplied and generated material. The distinction here is labelled hypothetical findings used in a subsequent operational decision.
- [Thinking to Recall](https://research.google/blog/thinking-to-recall-how-reasoning-unlocks-parametric-knowledge-in-llms/) studies reasoning and factual retrieval, including problems with incorrect intermediate facts. A hypothetical prediction is not itself a false factual assertion.
- [Hypothesis generation and updating in large language models](https://arxiv.org/abs/2605.05851) studies hypothesis behavior in number tasks. A mere change in preference after considering a hypothesis is not enough to support the claim tested here.
