# Manual Product Phase 1 — Summary

## Status
PREPARED_NOT_STARTED. Actual product runs: **0 / 40**. No model requests or ChatGPT conversations were run in this setup turn.

## Planned dataset
20 original testcases, two rule variants, one repeat per testcase/rule (40 entries). The order is randomized and saved in `manual-run-plan.json`. All entries are NOT_RUN.

## Rules
CANDIDATE23 SHA-256: `11a649a605a041bd2dfaf1dec844661ad39879cd87ce6022aadb457a90b4a8b5`
NEW22 SHA-256: `eddeb28b83ad1c7df91b855a169273dae35fbb4ae059be9624e0512ce9ba6d46`
The rule text is delivered in a user message. This is not a system-level A/B test.

## Compatibility
Preflight identifies T13, T14, T16, T18, and T20 as PRODUCT_INCOMPATIBLE with the standard ChatGPT product UI because their oracle depends on benchmark-only service mutation/verification or controlled error injection. The remaining testcases have per-entry fixture/tool preflight requirements. No observed product result exists yet.

## Results
Not estimable before real captures. No pass rate, false-completion rate, or comparative conclusion is reported.

## Interpretation limits
This is a separate ChatGPT product dataset with user-message rule injection, weaker controls than the API benchmark, no complete internal execution trace, possible hidden system/developer instructions and memory, potentially changing model/tool availability, and a single repeat per case/rule. Do not combine it with API results or claim strict system-prompt A/B evidence.
