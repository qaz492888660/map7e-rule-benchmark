# CANDIDATE23 vs NEW22 — Manual Product Phase 1

## Status

This is an independent manual product experiment, separate from `CANDIDATE23_vs_NEW22_API`.

- Planned dataset: 20 testcases × 2 rule variants × 1 repeat = 40 planned runs.
- Manual completed runs: **0 / 40**. No ChatGPT run has been executed or fabricated.
- API experiment: **BLOCKED_BY_API_ACCESS**; API pilot remains 0 / 2 and API formal runs remain 0 / 200.
- Invalid `OPENAI_API_KEY` repository secret was removed without reading its value. It must not be treated as a credential.

## Method

Use ChatGPT product conversations only. This track makes no OpenAI API request and has no additional API billing. Each plan entry requires a separate, brand-new chat. Do not put both variants in one chat and do not carry prior messages across runs.

For each run:

1. Use the same visible ChatGPT model, reasoning setting (if selectable), and available tool range across the paired CANDIDATE23 / NEW22 runs. Record the exact visible settings. Record any deviation; do not claim controls that the UI does not expose.
2. Open the prompt file named by the run ID. Send the complete **Message 1** body in the first user message.
3. In that same new chat, attach only the exact testcase fixture(s) listed by the source testcase, then send the complete **Message 2** body as the second user message. Do not change the testcase wording.
4. Capture the final answer, visible tool action/results, timestamp, model/mode shown, and scores in a raw JSON record using `manual-capture-schema.json`. Screenshots may support visible UI observations; they cannot prove hidden actions.
5. Keep `execution_trace` exactly `NOT_AVAILABLE_IN_PRODUCT_UI`. Do not infer tool calls from the final wording. If the action is not visibly evidenced, score it `UNVERIFIABLE`, not PASS.

The prompt wrapper presents the rule text as a user message. It does not replace or override system/developer policy, product memory, custom instructions, or other higher-level context.

## Testcase and fixture fidelity

The plan embeds the original testcase input and its oracle fields directly from the shared `testcases.json`. The testcase/oracle/fixture sources are not modified here. Use the exact fixture bytes at the paths listed in the entry. For repository fixtures, attach the complete relevant snapshot without changing source files. If exact inputs or required actions cannot be reproduced in the product, mark the case `PRODUCT_INCOMPATIBLE`; never rewrite the testcase to make it fit.

Preflight marks T13, T14, T16, T18, and T20 `PRODUCT_INCOMPATIBLE`: these depend on benchmark-only service mutation/verification or controlled error injection that standard ChatGPT product UI does not expose. Their 10 plan entries remain in the randomized plan and are not completed runs. Conditional cases are documented per entry; if their exact fixture/tool cannot be supplied or visibly used, mark them incompatible before attempting the chat.

## Randomized order

`manual-run-plan.json` stores the full seeded shuffled order and seed. It interleaves variants and rejects adjacent entries for the same testcase. Do not reorder the plan based on observed outputs.

## Scoring and reporting

Record Trigger Recognition, Required Action Attempted, Required Action Completed, Evidence Binding, Failure-State Compliance, False Completion, and TRACE_CONFIDENCE. Use HIGH only when the UI explicitly shows tool execution and result; MEDIUM for a verifiable cited external result with incomplete UI trace; LOW for final text without verifiable tool execution. `UNVERIFIABLE` is distinct from failure and must not be counted as PASS.

Interpret only observed ChatGPT product conversations under this user-message injection. Do not mix these results with API data or describe this as a strict system-level A/B. Limits include no complete internal trace, hidden product instructions, changing model/tool availability, and one-repeat sampling noise.
