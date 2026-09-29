# Manual run capture template

Use a new ChatGPT conversation for each run. The rule text is injected as a **user message**; do not describe it as system-level. Follow the randomized order in `manual-run-plan.json`.

## Before each run

- Confirm the run is not marked `PRODUCT_INCOMPATIBLE`.
- Open a genuinely new chat with no prior testcase messages.
- Record the visible ChatGPT model, reasoning setting, and tools available.
- Attach the exact listed fixture bytes where required.
- Send Message 1 from the run's prompt file, then send Message 2 unchanged as the second user message.
- Do not add hints or corrective follow-ups before capturing the final answer.

## Raw record

Copy `manual-capture-schema.json` to a per-run JSON file under `manual-runs/`, then fill every field from the actual UI.

```json
{
  "run_id": "",
  "testcase_id": "",
  "rule_version": "",
  "rule_hash": "",
  "rule_injection_level": "USER_MESSAGE",
  "chatgpt_model_shown_in_ui": "",
  "reasoning_setting": "NOT_SHOWN",
  "timestamp_started": "",
  "timestamp_finished": "",
  "testcase_input": "",
  "attachments_used": [],
  "assistant_final_output": "",
  "visible_tool_evidence": [],
  "execution_trace": "NOT_AVAILABLE_IN_PRODUCT_UI",
  "scores": {
    "trigger_recognition": "UNVERIFIABLE",
    "required_action_attempted": "UNVERIFIABLE",
    "required_action_completed": "UNVERIFIABLE",
    "evidence_binding": "UNVERIFIABLE",
    "failure_state_compliance": "UNVERIFIABLE",
    "false_completion": "UNVERIFIABLE",
    "false_completion_count": null
  },
  "trace_confidence": "LOW",
  "control_deviations": [],
  "notes": ""
}
```

## Evidence rules

- A tool action counts as attempted only if visible UI evidence supports it. Otherwise use `UNVERIFIABLE`.
- A visible tool call alone does not prove sufficient evidence or final-state verification.
- Never infer hidden tool activity from assistant text.
- Keep screenshots in a private evidence location and refer to them by path. Do not commit account details or unrelated personal information.
- Mark an unexecutable unchanged testcase `PRODUCT_INCOMPATIBLE` with reason; do not fabricate a response or create a raw record for an unrun case.
- The CSV and summary are planning templates until real runs are captured. Do not fill scores from expectations.
