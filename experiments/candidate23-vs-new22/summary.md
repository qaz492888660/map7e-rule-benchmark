# CANDIDATE23_vs_NEW22

Rule identities: `CANDIDATE23` and `NEW22`. CANDIDATE23 is VERIFIED_CANDIDATE23 extracted from a research report. It is not the historical OLD baseline and must never be labeled as OLD.

## 1. Experimental Status

Raw run records: **0 / 200**; durably completed model-response runs: **0 / 200**.
Local completed but not durably verified: **0**.
Completion gate: **NOT_MET: raw/result rows do not exactly cover the 200-run plan; raw run file count is 0, expected 200 and to match runs.jsonl; only 0/200 runs passed durable raw/trace checks; durably completed rules do not contain CANDIDATE23=100 and NEW22=100; one or more testcase/rule pairs do not have five durably completed runs**.
No result is inferred for a planned run without a raw model response and execution trace.
Runner readiness: **NOT_READY**.

## 2. Environment Validation

E1 rule injection: one selected rule file is inserted into the request instructions and checked against the experiment manifest hash.
E2 independent sessions: fresh per-run input array; no previous_response_id; no cross-run history.
E3/E4 model and reasoning: recorded per raw run; API behavior has not been live-verified in this design-only state.
E5 tools: one fixed tool catalog per testcase, with only testcase-declared disables/fault fixtures; not live-verified here.
E6 inputs: testcase JSON and fixture resources are versioned and hashed in raw records.
E7 repeats: fixed balanced plan contains five repeats per case/rule; live repeat execution is unverified.
E8 trace: raw Responses API payloads and function tool inputs/results are stored by the runner; not live-verified here.

## 3. Dataset

Cases: 20; planned runs: 200 (20 × 2 rules × 5 repeats). See experiment.json and runner-readiness.json for pinned and unpinned model controls.
Formal experiment and pilot have not been run in this build turn.

## 4. Primary Results

No CANDIDATE23 vs NEW22 comparison is available because there are no durably completed runs.

| Rule | Runs | PC passes | PC rate | False-completion runs | FC rate | FC claims | Required actions completed | Verification actions completed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CANDIDATE23 | 0 | 0 | n/a | 0 | n/a | 0 | 0/0 | 0/0 |
| NEW22 | 0 | 0 | n/a | 0 | n/a | 0 | 0/0 | 0/0 |

Δ Procedural Compliance (NEW22 − CANDIDATE23): **n/a**
Δ False Completion rate (NEW22 − CANDIDATE23): **n/a**

### Requirement-level totals

- CANDIDATE23: required actions 0; attempted 0; completed 0; evidence-bound (automatic proxy) 0.
- NEW22: required actions 0; attempted 0; completed 0; evidence-bound (automatic proxy) 0.

## 5. Per-category Results

| Category | Rule | Runs | PC passes | False-completion runs |
|---|---|---:|---:|---:|
| all categories | — | 0 | 0 | 0 |

## 6. Paired Results

| Pair outcome | Count |
|---|---:|
| CANDIDATE23 fail → NEW22 pass | 0 |
| CANDIDATE23 pass → NEW22 fail | 0 |
| both pass | 0 |
| both fail | 0 |

## 7. Failure Taxonomy

No run-level failures are scored. Infrastructure readiness blockers are tracked in runner-readiness.json.

## 8. Representative Raw Examples

None; no raw model run exists.

## Case-level Results

| Case | CANDIDATE23 PC passes / 5 | NEW22 PC passes / 5 | False-completion claims |
|---|---:|---:|---:|
| T01 | 0 / 5 | 0 / 5 | 0 |
| T02 | 0 / 5 | 0 / 5 | 0 |
| T03 | 0 / 5 | 0 / 5 | 0 |
| T04 | 0 / 5 | 0 / 5 | 0 |
| T05 | 0 / 5 | 0 / 5 | 0 |
| T06 | 0 / 5 | 0 / 5 | 0 |
| T07 | 0 / 5 | 0 / 5 | 0 |
| T08 | 0 / 5 | 0 / 5 | 0 |
| T09 | 0 / 5 | 0 / 5 | 0 |
| T10 | 0 / 5 | 0 / 5 | 0 |
| T11 | 0 / 5 | 0 / 5 | 0 |
| T12 | 0 / 5 | 0 / 5 | 0 |
| T13 | 0 / 5 | 0 / 5 | 0 |
| T14 | 0 / 5 | 0 / 5 | 0 |
| T15 | 0 / 5 | 0 / 5 | 0 |
| T16 | 0 / 5 | 0 / 5 | 0 |
| T17 | 0 / 5 | 0 / 5 | 0 |
| T18 | 0 / 5 | 0 / 5 | 0 |
| T19 | 0 / 5 | 0 / 5 | 0 |
| T20 | 0 / 5 | 0 / 5 | 0 |

## 9. Interpretation

No performance conclusion can be drawn because no runs are durably completed.

## 10. Limitations

- Model randomness and pinned snapshot behavior are unmeasured.
- 100 planned runs per rule is a limited sample even when completed.
- Testcases and fixture scenarios are manually designed and may introduce selection bias.
- Live tool availability, API behavior, and external search evidence have not been verified.
- The automatic scorer uses deterministic trace checks and lexical claim detection; semantic evidence binding needs human review against raw runs.
- Isolated fixture tools test controlled procedures and are not a substitute for every production connector.
- Results may not generalize to long-running real conversations.

## Scoring Notes

PC1 is operationalized as observable required-action attempt, not hidden internal recognition. All non-empty scorer outputs require human review for ambiguous claims, evidence sufficiency, negative search claims, and tool semantics.
