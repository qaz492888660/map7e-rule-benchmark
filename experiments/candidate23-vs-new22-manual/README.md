# CANDIDATE23 vs NEW22 — Manual Product Phase 1

## Status

This track is independent from CANDIDATE23_vs_NEW22_API. It uses real ChatGPT product conversations, not the OpenAI API, and creates no additional API billing.

- Planned dataset: 20 testcases × 2 rule variants × 1 repeat = 40.
- Manual completed runs: **0 / 40**.
- MP-ENV-SMOKE: **defined, not executed**.
- API experiment remains BLOCKED_BY_API_ACCESS; API pilot 0 / 2; API formal 0 / 200.
- Rule injection level is USER_MESSAGE. This is not a strict system-prompt A/B test.

## Required environment for every formal run

1. Create a new Temporary Chat for that run.
2. Before sending the first message, explicitly choose Unpersonalized / non-personalized. Do not use Personalized Temporary Chat.
3. Confirm Memory context is OFF and Custom Instructions are OFF. Do not assume Temporary Chat by itself disables them.
4. Use the same visible ChatGPT model and reasoning setting throughout. Record exact UI labels. If either cannot be selected or held fixed, the preflight gate fails; do not start formal runs.
5. Keep the same available product tools and exact testcase fixture bytes across CANDIDATE23 and NEW22.
6. Use one chat for one run only. Do not save or continue that chat for another run. Save its evidence, then exit.

These are protocol requirements, not claims that the controls have already been verified. MP-ENV-SMOKE must pass first. No formal run is executable before then.

## MP-ENV-SMOKE

The non-experimental preflight is defined in manual-environment-smoke.json. It is not C23 or NEW22, is excluded from the 0 / 40 count, and must not compare rule effects. It checks Temporary + Unpersonalized mode, memory/custom-instruction state, fixed model/reasoning, actual UI tool availability, and visible tool execution evidence. It has not been run.

## Run procedure

Use the randomized order in manual-run-plan.json. For each run, open its manual-prompts/<testcase>-<variant>.txt file:

- Send Message 1 as the first user message, including exactly one rule body.
- Attach the exact fixture bytes listed by the testcase.
- Send Message 2 unchanged as the second user message.
- Capture the final answer and only tool actions/results actually visible in the UI.

Do not execute both variants in one chat. The wrapper supplies rules in a user message and does not override system/developer instructions.

## Compatibility assessment

No testcase is currently classified PRODUCT_COMPATIBLE or PRODUCT_INCOMPATIBLE. Because the Unpersonalized Temporary Chat environment has not been preflighted, **T01–T20 are all REQUIRES_PREFLIGHT**. This replaces the earlier compatibility labels. After MP-ENV-SMOKE, classify each testcase only from observed tool and fixture availability. If a required action cannot be performed unchanged, mark it PRODUCT_INCOMPATIBLE.

## Evidence and scoring

Set execution_trace to exactly NOT_AVAILABLE_IN_PRODUCT_UI. Score only what is actually visible: search UI, file-read UI, tool operations/results, uploaded fixtures, final answer, and screenshots. Screenshots are supporting evidence only. Never infer hidden tool calls from final wording. If action evidence is not visible, use UNVERIFIABLE, not PASS.

## Interpretation limits

This track has weaker controls than the API benchmark, has no complete internal execution trace, and may still be affected by product-level hidden instructions. ChatGPT model versions and tool availability can change. It uses user-message rule injection, not system-level injection, and its results must not be mixed with API results. One repeat per testcase/rule has substantial sampling uncertainty.
