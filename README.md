# map7e-rule-benchmark

This project is for an auditable OLD vs NEW22 benchmark of procedural compliance and false completion.

- Formal target: 200 independent runs across 20 test cases, two rule versions, and five repeats.
- Formal runs completed: **0 / 200**.
- The real experiment has **not started**. No pilot or model request is included in this initialization commit.

Rule sources and benchmark harness files will be added only from verified source material. Missing original rule text will remain explicitly marked `MISSING_INPUT`.

## Harness status

The harness uses one fresh OpenAI Responses API input context per run, writes raw records before scoring, and has a Git commit/push/readback path for durable completion. `--dry-run` never calls a model. Live model and Git transport behavior have not been tested in this environment.

```bash
python3 scripts/run-benchmark.py --testcase T01 --rule OLD --repeat 1 --dry-run
python3 scripts/score-results.py
```

Real requests are blocked while either rule file is `MISSING_INPUT`. When source text is restored, model configuration is read only from environment variables; credentials must not be stored in the repository.
