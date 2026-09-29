# Manual run capture template

## Gate before any formal run

MP-ENV-SMOKE must pass first. For every planned run:

- Open a brand-new Temporary Chat.
- Before sending any message, select Unpersonalized / non-personalized mode. Do not use Personalized Temporary Chat.
- Confirm Memory context OFF and Custom Instructions OFF.
- Confirm environment_preflight_pass for this exact chat before sending prompts.
- Record exact visible ChatGPT model, reasoning setting, and tool availability.
- If any required setting cannot be confirmed, do not send the rule or testcase; leave the run NOT_RUN.

## Run procedure

Use the randomized order in manual-run-plan.json. Each run gets its own Temporary Chat.

1. Open the prompt file identified by the run ID.
2. Send Message 1 body as the first user message.
3. Attach only exact fixture bytes listed for that testcase.
4. Send Message 2 unchanged as the second user message.
5. Save the complete final answer and visible UI evidence.
6. Save the raw record under manual-runs/<run_id>.json.
7. Exit the chat. Do not save or continue it as the next run.

Do not use one chat for both variants. The injection level is USER_MESSAGE; never label it system-level.

## Raw record

Use manual-capture-schema.json. Capture chat_mode=TEMPORARY, personalization=OFF, memory_context=OFF, custom_instructions=OFF, rule_injection_level=USER_MESSAGE, exact model_ui and reasoning_ui, tool_availability, and environment_preflight_pass=true.

For a run that fails preflight, do not create a formal raw run record; record the environment failure in the MP-ENV-SMOKE capture and keep completed runs at 0 / 40.

## Evidence boundary

Set execution_trace to NOT_AVAILABLE_IN_PRODUCT_UI. Record only UI-visible search, file, tool, and action evidence, actual uploaded fixtures, final output, and screenshots. A screenshot cannot prove calls the UI does not show. If evidence is insufficient, score the action UNVERIFIABLE.
