#!/usr/bin/env python3
"""Static checks and manually dispatched runtime modes for GitHub Actions.

The only model-calling modes are api-smoke (one non-experimental request) and
pilot (the two explicitly frozen T01 pilot entries). This module is safe to run
in static-check mode without credentials or external requests.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT = Path(__file__).resolve().parents[1]
EXP_DIR = PROJECT / "experiments" / "candidate23-vs-new22"
EXPERIMENT_CONFIG = EXP_DIR / "experiment.json"
RUNTIME_CONFIG = EXP_DIR / "runtime-config.json"
READINESS = EXP_DIR / "runner-readiness.json"
WORKFLOW = PROJECT / ".github" / "workflows" / "benchmark-pilot.yml"
RUNNER_PATH = PROJECT / "scripts" / "run-benchmark.py"
SCORER_PATH = PROJECT / "scripts" / "score-results.py"
DEPENDENCIES = PROJECT / "requirements-benchmark.txt"
API_REQUEST_STARTED = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> bytes:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return payload


def import_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args: list[str], timeout: int = 120, env: dict[str, str] | None = None, include_api_key: bool = False) -> subprocess.CompletedProcess[str]:
    clean_env = os.environ.copy() if env is None else env.copy()
    if not include_api_key:
        clean_env.pop("OPENAI_API_KEY", None)
    clean_env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(args, cwd=PROJECT, capture_output=True, text=True, timeout=timeout, env=clean_env)


def assert_actions_host() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise RuntimeError("This runtime mode must execute on the configured GitHub Actions Linux runner")
    if os.environ.get("GITHUB_REF_NAME") != "main":
        raise RuntimeError("Dispatch this workflow from main; only main is checked out and updated")


def assert_main_checkout(require_token: bool = True) -> None:
    assert_actions_host()
    if require_token and not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("GitHub Actions default GITHUB_TOKEN is unavailable")
    for args, expected in ((["git", "branch", "--show-current"], "main"), (["git", "remote", "get-url", "origin"], None)):
        result = command(args)
        if result.returncode != 0:
            raise RuntimeError("The Actions checkout is missing its expected Git metadata")
        actual = result.stdout.strip()
        if expected is not None and actual != expected:
            raise RuntimeError("The Actions checkout is not on main")
        if expected is None and not actual.startswith("https://github.com/"):
            raise RuntimeError("origin is not an HTTPS GitHub repository")


def sync_and_verify(paths: list[str], message: str) -> str:
    runner = import_module(RUNNER_PATH, "benchmark_runner_for_actions")
    commit = runner.commit_push(paths, message)
    for relative in paths:
        expected = (PROJECT / relative).read_bytes()
        runner.verify_remote_file(relative, expected)
    return commit


def check_runtime_config(strict_actions_host: bool = False) -> dict[str, Any]:
    runtime = load_json(RUNTIME_CONFIG)
    readiness = load_json(READINESS)
    experiment = load_json(EXPERIMENT_CONFIG)
    model = runtime["api"]["exact_model_id"]
    if model != "gpt-5.6-sol":
        raise ValueError("Configured model ID must be gpt-5.6-sol")
    if runtime["api"]["model_id_evidence"]["status"] not in {"CONFIGURED_NOT_YET_CALLABLE_VERIFIED", "VERIFIED_CALLABLE"}:
        raise ValueError("Model evidence must be either configured-but-untested or backed by a real API response")
    if runtime["api"]["callability"]["status"] not in {"NOT_VERIFIED", "CONFIGURED_NOT_YET_CALLABLE_VERIFIED", "VERIFIED_CALLABLE"}:
        raise ValueError("Unexpected model-callability status")
    if runtime["reasoning"]["effort"] != "medium":
        raise ValueError("reasoning effort must be medium")
    temp = runtime["generation"]["temperature"]
    if temp.get("value") is not None or not str(temp.get("status", "")).startswith("OMITTED"):
        raise ValueError("temperature must be omitted, not assigned a guessed value")
    if runtime["generation"].get("max_output_tokens") != 2000:
        raise ValueError("max_output_tokens must be 2000")
    if runtime["request"].get("response_storage") is not False:
        raise ValueError("Responses storage must be disabled")
    state = runtime["state_policy"]
    if state.get("previous_response_id") != "OMITTED" or state.get("conversation_id") != "OMITTED" or state.get("response_store") is not False:
        raise ValueError("Runtime must be stateless and use store=false")
    old_model = runtime.get("superseded_model_candidate", {})
    if old_model.get("model_id") == "gpt-5.5-2026-04-23" and old_model.get("status") != "CANCELLED_NOT_VERIFIED":
        raise ValueError("The prior untested model candidate must not remain VERIFIED")
    if runtime["persistence"].get("credential_env") != "GITHUB_TOKEN":
        raise ValueError("Persistence must use the Actions-provided GITHUB_TOKEN")
    if runtime["persistence"].get("durable_persistence") not in {"BLOCKED", "NOT_VERIFIED", "VERIFIED"}:
        raise ValueError("Unexpected durable persistence status")
    if strict_actions_host:
        assert_actions_host()
        py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if py != runtime["execution_environment"].get("python_version"):
            raise RuntimeError("Actions Python version differs from runtime-config.json")
        if os.environ.get("RUNNER_OS") != "Linux":
            raise RuntimeError("Actions runner OS differs from runtime-config.json")
    if experiment.get("rule_names") != ["CANDIDATE23", "NEW22"]:
        raise ValueError("This workflow is scoped only to CANDIDATE23_vs_NEW22")
    if readiness.get("formal_runs_completed") != 0 or readiness.get("pilot_runs_completed") != 0:
        raise ValueError("Readiness file must preserve zero run counts before the pilot")
    if readiness.get("status") not in {"READY_FOR_RUNTIME_SMOKE", "READY_FOR_PILOT", "NOT_READY"}:
        raise ValueError("Unexpected readiness state")
    return runtime


def parse_workflow() -> dict[str, Any]:
    # BaseLoader avoids YAML 1.1 treating the GitHub key "on" as a boolean.
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(document, dict):
        raise ValueError("Workflow YAML is not a mapping")
    trigger = document.get("on")
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        raise ValueError("Workflow must have workflow_dispatch as its only trigger")
    permissions = document.get("permissions", {})
    if permissions.get("contents") != "write" or len(permissions) != 1:
        raise ValueError("Workflow permissions must be limited to contents: write")
    concurrency = document.get("concurrency", {})
    if concurrency.get("group") != "map7e-rule-benchmark" or concurrency.get("cancel-in-progress") != "false":
        raise ValueError("Workflow concurrency must serialize benchmark writers without cancellation")
    inputs = trigger["workflow_dispatch"].get("inputs", {})
    choices = inputs.get("mode", {}).get("options", [])
    expected_modes = ["static_validation", "persistence_smoke", "api_smoke", "pilot"]
    if choices != expected_modes:
        raise ValueError("workflow_dispatch mode choices are missing, reordered, or unexpected")
    jobs = document.get("jobs", {})
    if len(jobs) != 1:
        raise ValueError("One serialized job is required to keep all modes on one controlled runner")
    job = next(iter(jobs.values()))
    if job.get("runs-on") != "ubuntu-24.04":
        raise ValueError("Workflow runner label differs from the frozen runtime configuration")
    steps = job.get("steps", [])
    checkout_steps = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    if len(checkout_steps) != 1:
        raise ValueError("Workflow must checkout the repository")
    checkout = checkout_steps[0]
    if (
        checkout.get("with", {}).get("ref") != "main"
        or checkout.get("with", {}).get("token") != "${{ github.token }}"
        or checkout.get("with", {}).get("persist-credentials") != "true"
        or checkout.get("with", {}).get("fetch-depth") != "0"
    ):
        raise ValueError("Workflow must checkout main using the default github.token")
    python_steps = [step for step in steps if step.get("uses", "").startswith("actions/setup-python@")]
    if len(python_steps) != 1:
        raise ValueError("Workflow must set up the pinned Python runtime")
    runtime = load_json(RUNTIME_CONFIG)
    if python_steps[0].get("with", {}).get("python-version") != runtime["execution_environment"]["python_version"]:
        raise ValueError("actions/setup-python does not match the frozen Python patch version")
    install_steps = [step for step in steps if "pip install" in step.get("run", "")]
    if len(install_steps) != 1 or "--requirement requirements-benchmark.txt" not in install_steps[0]["run"]:
        raise ValueError("Workflow must install the pinned benchmark dependency file")
    mode_steps = {step.get("id"): step for step in steps if step.get("id")}
    expected_mode_routes = {
        "persistence-smoke": ("inputs.mode == 'persistence_smoke'", "persistence-smoke"),
        "api-smoke": ("inputs.mode == 'api_smoke'", "api-smoke"),
        "pilot": ("inputs.mode == 'pilot'", "pilot"),
    }
    for step_id, (condition, command_name) in expected_mode_routes.items():
        step = mode_steps.get(step_id)
        if step is None or step.get("if") != condition or f"benchmark-actions.py {command_name}" not in step.get("run", ""):
            raise ValueError(f"Workflow mode route is missing or mismatched: {step_id}")
    guard = next((step for step in steps if step.get("name") == "Require API credential for API modes"), None)
    if guard is None or guard.get("if") != "inputs.mode == 'api_smoke' || inputs.mode == 'pilot'" or "OPENAI_API_KEY_MISSING" not in guard.get("run", ""):
        raise ValueError("API mode secret guard must fail with OPENAI_API_KEY_MISSING before runtime dispatch")
    static_step = next((step for step in steps if step.get("name") == "Compile and validate benchmark runtime without model calls"), None)
    if static_step is None or "benchmark-actions.py static-check --github-runtime" not in static_step.get("run", ""):
        raise ValueError("Workflow must run the no-model static validation before any mode")
    key_env_steps = [step for step in steps if step.get("env", {}).get("OPENAI_API_KEY") == "${{ secrets.OPENAI_API_KEY }}"]
    key_env_names = {step.get("name") for step in key_env_steps}
    if len(key_env_steps) != 3 or key_env_names != {
        "Require API credential for API modes",
        "One non-experimental API smoke request",
        "Two-run T01 pilot",
    }:
        raise ValueError("OPENAI_API_KEY must be injected only into its guard and the api_smoke/pilot steps")
    if any("secrets.GITHUB_TOKEN" in str(step.get("env", {})) for step in steps):
        raise ValueError("Do not require a separate GITHUB_TOKEN secret")
    if any("${{ github.token }}" not in str(step.get("env", {})) for step in steps if step.get("id") in {"persistence-smoke", "api-smoke", "pilot"}):
        raise ValueError("Persistence and result-writing modes must use the default github.token")
    return document


def secret_scan(paths: list[Path]) -> int:
    prefixes = ("sk-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
    found = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for prefix in prefixes:
            start = 0
            while True:
                index = text.find(prefix, start)
                if index < 0:
                    break
                end = index + len(prefix)
                while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
                    end += 1
                if end - index >= len(prefix) + 20:
                    found.append(path.relative_to(PROJECT).as_posix())
                start = index + len(prefix)
    if found:
        raise RuntimeError("Secret-like credential material found in source files: " + ", ".join(sorted(set(found))))
    return 0


def project_text_files() -> list[Path]:
    result = command(["git", "ls-files", "-co", "--exclude-standard"])
    if result.returncode != 0:
        raise RuntimeError("Could not enumerate project files for secret scan")
    return [PROJECT / item for item in result.stdout.splitlines() if item]


def static_checks() -> dict[str, Any]:
    runtime = check_runtime_config(strict_actions_host=False)
    workflow = parse_workflow()
    if not DEPENDENCIES.is_file() or DEPENDENCIES.read_text(encoding="utf-8").strip() != "PyYAML==6.0.3":
        raise ValueError("requirements-benchmark.txt must lock the sole non-stdlib dependency")
    candidate_manifest = load_json(EXP_DIR / "rules-manifest.json")
    for name, relative in (("CANDIDATE23", "../../rules/CANDIDATE23.md"), ("NEW22", "../../rules/NEW22.md")):
        meta = candidate_manifest["rules"][name]
        data = (EXP_DIR / relative).resolve().read_bytes()
        if digest(data) != meta.get("sha256") or meta.get("usable_for_ab") is not True:
            raise ValueError(f"Rule input integrity failed for {name}")
    if candidate_manifest["rules"]["CANDIDATE23"]["sha256"] == candidate_manifest["rules"]["NEW22"]["sha256"]:
        raise ValueError("Rule hashes must differ")

    runner = import_module(RUNNER_PATH, "benchmark_runner_static")
    runner.configure_experiment(str(EXPERIMENT_CONFIG))
    layout = runner.validate_layout()
    if not layout["valid_structure"]:
        raise ValueError("Runner structural validation failed: " + "; ".join(layout["errors"]))
    if layout.get("ready_for_model_request"):
        raise ValueError("Model requests must remain gated until both real smoke modes are verified")
    if layout.get("runner_hash_matches") is not True:
        raise ValueError("runtime-config runner SHA-256 does not match scripts/run-benchmark.py")
    response_loop_source = inspect.getsource(runner.response_loop)
    if '"previous_response_id":' in response_loop_source or '"conversation_id":' in response_loop_source:
        raise ValueError("Runner source must not send chained response/conversation identifiers")
    if '"store": False' not in response_loop_source:
        raise ValueError("Runner must explicitly set store=false")

    testcase_doc = load_json(PROJECT / "testcases.json")
    plan = load_json(EXP_DIR / "run-plan.json")
    cases = testcase_doc.get("cases", [])
    entries = plan.get("entries", [])
    case_ids = {case["id"] for case in cases}
    ids = [entry["run_id"] for entry in entries]
    expected_ids = {f"{case_id}-{code}-R{repeat}" for case_id in case_ids for code in ("C23", "N22") for repeat in range(1, 6)}
    if len(cases) != 20 or len(entries) != 200 or len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise ValueError("Candidate23 plan must still be exactly 20 cases x 2 variants x 5 repeats with unique run IDs")
    if any(e.get("run_id") in {"T01-OLD-R1", "T01-NEW22-R1"} for e in entries):
        raise ValueError("New experiment run IDs must remain distinct from the blocked OLD experiment")
    frozen_pilot = pilot_plan()
    if frozen_pilot != [("CANDIDATE23", "T01-C23-R1"), ("NEW22", "T01-N22-R1")]:
        raise ValueError("Pilot request order must be T01-C23-R1 followed by T01-N22-R1")
    for rule, run_id in frozen_pilot:
        if sum(1 for item in entries if item.get("run_id") == run_id and item.get("rule") == rule and item.get("testcase") == "T01" and item.get("repeat") == 1) != 1:
            raise ValueError(f"Frozen pilot run is absent or ambiguous: {run_id}")

    # Import-only checks do not write the experiment's results or call a model.
    scorer = import_module(SCORER_PATH, "benchmark_scorer_empty_check")
    with tempfile.TemporaryDirectory(prefix="benchmark-empty-scorer-") as temp_dir:
        scorer.ROOT = Path(temp_dir)
        if scorer.raw_runs() != []:
            raise ValueError("Scorer empty-runs handling did not return an empty dataset")

    # Reuse the existing runner's explicit dry-run path for both rule variants.
    dry_results = []
    dry_payloads = []
    for rule in ("CANDIDATE23", "NEW22"):
        result = command([
            sys.executable, str(RUNNER_PATH), "--experiment-config", str(EXPERIMENT_CONFIG),
            "--testcase", "T01", "--rule", rule, "--repeat", "1", "--dry-run",
        ])
        if result.returncode != 0:
            raise RuntimeError("Runner dry-run failed for " + rule + ": " + result.stderr[-2000:])
        parsed = json.loads(result.stdout)
        if parsed.get("model_request_made") is not False or parsed.get("selection_count") != 1:
            raise RuntimeError("Runner dry-run returned an unexpected selection or model-call status")
        dry_results.append(rule)
        dry_payloads.append(parsed["selected"][0])
    if dry_payloads[0].get("input") != dry_payloads[1].get("input") or dry_payloads[0].get("tools") != dry_payloads[1].get("tools"):
        raise RuntimeError("Pilot variants do not receive identical testcase input and tool permissions")

    # Empty resume and duplicate-run protection are exercised without external calls.
    resume = command([
        sys.executable, str(RUNNER_PATH), "--experiment-config", str(EXPERIMENT_CONFIG),
        "--testcase", "T01", "--rule", "CANDIDATE23", "--repeat", "1", "--resume",
    ])
    if resume.returncode != 0 or json.loads(resume.stdout).get("resume_results") != []:
        raise RuntimeError("Empty resume path failed or produced unexpected run data")
    case = next(case for case in cases if case["id"] == "T01")
    entry = next(item for item in entries if item["run_id"] == "T01-C23-R1")
    original_runs = runner.RUNS
    with tempfile.TemporaryDirectory(prefix="benchmark-dedup-check-") as temp_dir:
        runner.RUNS = Path(temp_dir)
        target = runner.raw_path_for(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
        try:
            runner.execute_entry(entry, case, dry_run=False)
        except FileExistsError:
            pass
        else:
            raise RuntimeError("Duplicate run_id guard did not reject an existing raw record")
        finally:
            runner.RUNS = original_runs

    persistence_flow = inspect.getsource(run_persistence_smoke)
    for marker in ("runner.commit_push", "fetch", "verify_remote_file", "path.unlink", "cat-file", '"durable_persistence"] = "VERIFIED"'):
        if marker not in persistence_flow:
            raise RuntimeError("Persistence smoke flow is missing a required step: " + marker)
    api_flow = inspect.getsource(run_api_smoke)
    for marker in ('"store": False', '"previous_response_id_sent": False', '"usage"', '"tool_calls"', '"started_at"', '"finished_at"', '"callability"] = {'):
        if marker not in api_flow:
            raise RuntimeError("API smoke flow is missing required safe trace metadata: " + marker)
    pilot_flow = inspect.getsource(run_pilot)
    for marker in ("pilot_plan()", "--testcase", "--rule", "--repeat", "DURABLY_COMPLETED"):
        if marker not in pilot_flow:
            raise RuntimeError("Pilot flow is missing a required gate or run selector: " + marker)

    source_files = project_text_files()
    secret_scan(source_files)
    return {
        "status": "PASS_STATIC_ONLY",
        "workflow_modes": workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"],
        "model_id": runtime["api"]["exact_model_id"],
        "reasoning_effort": runtime["reasoning"]["effort"],
        "temperature": "OMITTED",
        "max_output_tokens": runtime["generation"]["max_output_tokens"],
        "testcases": len(cases),
        "planned_runs": len(entries),
        "unique_run_ids": len(set(ids)),
        "dry_run_variants": dry_results,
        "scorer_empty_runs": "PASS",
        "resume_empty": "PASS",
        "duplicate_run_guard": "PASS",
        "secret_scan": "PASS",
        "real_model_calls": 0,
        "persistence_smoke_executed": False,
        "api_smoke_executed": False,
        "pilot_executed": False,
    }


def run_persistence_smoke() -> dict[str, Any]:
    assert_main_checkout()
    smoke_id = uuid.uuid4().hex
    relative = f".benchmark-smoke/{smoke_id}.txt"
    path = PROJECT / relative
    content = f"map7e persistence smoke {smoke_id}\n".encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("Unexpected smoke-test path collision")
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    runner = import_module(RUNNER_PATH, "benchmark_runner_persistence_smoke")
    first_sha: str | None = None
    delete_sha: str | None = None
    create_verified = False
    try:
        first_sha = runner.commit_push([relative], f"benchmark persistence smoke create {smoke_id}")
        fetch_create = runner.git(["fetch", "origin", "main"], timeout=90)
        if fetch_create.returncode != 0:
            raise RuntimeError("Could not fetch origin/main after the smoke-file push")
        runner.verify_remote_file(relative, content)
        create_verified = True
    finally:
        # The unique file is removed locally and a delete push is attempted even
        # if create readback fails. An incomplete sequence can never be VERIFIED.
        if path.exists():
            path.unlink()
        delete_sha = runner.commit_push([relative], f"benchmark persistence smoke delete {smoke_id}")
        fetch_delete = runner.git(["fetch", "origin", "main"], timeout=90)
        if fetch_delete.returncode != 0:
            raise RuntimeError("Could not fetch origin/main after deleting the smoke file")
        absent = runner.git(["cat-file", "-e", f"origin/main:{relative}"], timeout=30)
        if absent.returncode == 0:
            raise RuntimeError("Remote still contains the deleted smoke-test file")
        if absent.returncode not in (1, 128):
            raise RuntimeError("Remote absence check returned an unexpected Git error")

    if first_sha is None or delete_sha is None or not create_verified:
        raise RuntimeError("Persistence smoke did not complete both remote commits")

    runtime = load_json(RUNTIME_CONFIG)
    readiness = load_json(READINESS)
    evidence = {
        "status": "VERIFIED",
        "smoke_id": smoke_id,
        "create_commit": first_sha,
        "delete_commit": delete_sha,
        "remote_create_readback": "CONTENT_MATCHED",
        "remote_delete_readback": "ABSENT",
        "verified_at": now(),
    }
    runtime["persistence"]["durable_persistence"] = "VERIFIED"
    runtime["persistence"]["persistence_smoke_test"] = "PASS"
    runtime["persistence"]["evidence"] = evidence
    readiness["persistence"] = {"status": "VERIFIED", "method": "GitHub Actions default GITHUB_TOKEN via git HTTPS", "evidence": evidence}
    readiness["validation_results"]["durable_persistence_smoke"] = "PASS"
    readiness["status"] = "READY_FOR_RUNTIME_SMOKE"
    config_rel = RUNTIME_CONFIG.relative_to(PROJECT).as_posix()
    ready_rel = READINESS.relative_to(PROJECT).as_posix()
    write_json(RUNTIME_CONFIG, runtime)
    write_json(READINESS, readiness)
    status_commit = sync_and_verify([config_rel, ready_rel], f"record verified benchmark persistence smoke {smoke_id}")
    return {"status": "VERIFIED", "smoke_id": smoke_id, "create_commit": first_sha, "delete_commit": delete_sha, "status_commit": status_commit, "remote_create_content_matched": True, "remote_deleted_file_absent": True}


def redact_text(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b", "[REDACTED]", text)
    return text[:10000]


def extract_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    parts = []
    for item in response.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
    return "\n".join(parts)


def api_post(payload: dict[str, Any], key: str, timeout: int) -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
    import urllib.error
    import urllib.request

    runtime = load_json(RUNTIME_CONFIG)
    endpoint = runtime["api"]["endpoint"]
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        error = body.get("error", {}) if isinstance(body, dict) else {}
        metadata = {
            "http_status": exc.code,
            "type": error.get("type"),
            "code": error.get("code"),
            "param": error.get("param"),
            "message": redact_text(error.get("message", "API request failed")),
        }
        return exc.code, None, metadata
    except Exception as exc:
        return None, None, {"type": type(exc).__name__, "message": redact_text(exc)}


def run_api_smoke() -> dict[str, Any]:
    global API_REQUEST_STARTED
    assert_main_checkout()
    # Fail closed before any request if durable persistence has not already passed.
    runtime = load_json(RUNTIME_CONFIG)
    if runtime.get("persistence", {}).get("durable_persistence") != "VERIFIED":
        raise RuntimeError("api_smoke is blocked until persistence_smoke is verified")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY_MISSING")
    model = runtime["api"]["exact_model_id"]
    payload = {
        "model": model,
        "reasoning": {"effort": runtime["reasoning"]["effort"]},
        "input": "For a runtime trace check, call the function benchmark_runtime_trace exactly once with value set to the string smoke-ok.",
        "tools": [{
            "type": "function",
            "name": "benchmark_runtime_trace",
            "description": "Diagnostic tool used only by one non-experimental API smoke request.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "strict": True,
        }],
        "tool_choice": {"type": "function", "name": "benchmark_runtime_trace"},
        "max_output_tokens": runtime["generation"]["max_output_tokens"],
        "store": False,
    }
    started = now()
    API_REQUEST_STARTED = True
    http_status, response, error = api_post(payload, key, int(runtime["request"]["timeout_seconds"]))
    finished = now()
    calls = [item for item in (response or {}).get("output", []) if item.get("type") == "function_call"]
    record = {
        "smoke_type": "NON_EXPERIMENTAL_API_SMOKE",
        "smoke_id": "API-SMOKE-" + uuid.uuid4().hex,
        "request_count": 1,
        "requested_model_id": model,
        "actual_model_id": (response or {}).get("model"),
        "reasoning_setting": runtime["reasoning"]["effort"],
        "temperature": "OMITTED",
        "max_output_tokens": runtime["generation"]["max_output_tokens"],
        "store": False,
        "previous_response_id_sent": False,
        "input": payload["input"],
        "http_status": http_status,
        "response_id": (response or {}).get("id"),
        "response_status": (response or {}).get("status"),
        "usage": (response or {}).get("usage"),
        "assistant_output": extract_text(response or {}),
        "tool_calls": calls,
        "tool_results": [],
        "tool_call_metadata": [{"id": c.get("id"), "call_id": c.get("call_id"), "name": c.get("name"), "arguments": c.get("arguments")} for c in calls],
        "response_metadata": {k: (response or {}).get(k) for k in ("object", "created_at", "completed_at", "incomplete_details", "error", "metadata", "service_tier") if k in (response or {})},
        "execution_trace": (response or {}).get("output", []),
        "errors": [error] if error else [],
        "started_at": started,
        "finished_at": finished,
        "credential_saved": False,
    }
    result_status = (
        http_status == 200
        and response is not None
        and response.get("status") == "completed"
        and response.get("model") == model
        and bool(response.get("id"))
        and len(calls) == 1
        and calls[0].get("name") == "benchmark_runtime_trace"
    )
    record["smoke_status"] = "PASS" if result_status else "FAIL"
    record["model_not_found_or_access_denied"] = bool(error and error.get("code") in {"model_not_found", "access_denied", "permission_denied"})
    smoke_dir = EXP_DIR / "runtime-smoke"
    relative_record = (smoke_dir / (record["smoke_id"] + ".json")).relative_to(PROJECT).as_posix()
    write_json(PROJECT / relative_record, record)
    commit_record = sync_and_verify([relative_record], f"record non-experimental API smoke {record['smoke_id']}")
    record["record_commit"] = commit_record
    if not result_status:
        runtime = load_json(RUNTIME_CONFIG)
        readiness = load_json(READINESS)
        runtime["api"]["callability"] = {"status": "NOT_VERIFIED", "evidence": "api_smoke did not receive a completed response from the configured exact model ID; no fallback is attempted."}
        runtime["api"]["model_id_evidence"]["status"] = "CONFIGURED_NOT_YET_CALLABLE_VERIFIED"
        runtime["api"]["model_id_evidence"]["account_callability"] = "NOT_VERIFIED"
        runtime["execution_trace_capture"] = {"status": "NOT_VERIFIED", "reason": "api_smoke did not capture the required forced function-call response."}
        readiness["status"] = "READY_FOR_RUNTIME_SMOKE"
        readiness["api_smoke"] = {"status": "FAIL", "request_count": 1, "record": relative_record, "record_commit": commit_record, "model_fallback": "NONE"}
        readiness["execution_trace"]["status"] = "NOT_VERIFIED"
        readiness["runtime_configuration"]["model_id_evidence"]["status"] = "CONFIGURED_NOT_YET_CALLABLE_VERIFIED"
        config_rel = RUNTIME_CONFIG.relative_to(PROJECT).as_posix()
        ready_rel = READINESS.relative_to(PROJECT).as_posix()
        write_json(RUNTIME_CONFIG, runtime)
        write_json(READINESS, readiness)
        sync_and_verify([config_rel, ready_rel], f"record failed API smoke {record['smoke_id']}")
        raise RuntimeError("API smoke failed for configured model; no model fallback was attempted. See persisted error metadata.")

    runtime = load_json(RUNTIME_CONFIG)
    readiness = load_json(READINESS)
    runtime["api"]["callability"] = {
        "status": "VERIFIED_CALLABLE",
        "response_id": record["response_id"],
        "actual_model_id": record["actual_model_id"],
        "verified_at": finished,
        "evidence_record": relative_record,
    }
    runtime["api"]["model_id_evidence"]["status"] = "VERIFIED_CALLABLE"
    runtime["api"]["model_id_evidence"]["account_callability"] = "VERIFIED_BY_REAL_API_RESPONSE"
    runtime["execution_trace_capture"] = {
        "status": "VERIFIED",
        "evidence_record": relative_record,
        "evidence": "The real Responses API smoke response included the forced function_call item, id/call_id, name, arguments, response id, status, usage, and timestamps.",
    }
    readiness["api_smoke"] = {"status": "PASS", "request_count": 1, "record": relative_record, "record_commit": commit_record, "response_id": record["response_id"], "actual_model_id": record["actual_model_id"]}
    readiness["execution_trace"]["status"] = "VERIFIED_BY_API_SMOKE"
    readiness["execution_trace"]["evidence_record"] = relative_record
    readiness["api_credential"]["status"] = "AVAILABLE_FOR_SUCCESSFUL_API_SMOKE"
    readiness["validation_results"]["api_smoke"] = "PASS"
    ready_for_pilot = (
        runtime["persistence"].get("durable_persistence") == "VERIFIED"
        and runtime["execution_trace_capture"].get("status") == "VERIFIED"
        and runtime["api"]["callability"].get("status") == "VERIFIED_CALLABLE"
    )
    readiness["status"] = "READY_FOR_PILOT" if ready_for_pilot else "READY_FOR_RUNTIME_SMOKE"
    config_rel = RUNTIME_CONFIG.relative_to(PROJECT).as_posix()
    ready_rel = READINESS.relative_to(PROJECT).as_posix()
    write_json(RUNTIME_CONFIG, runtime)
    write_json(READINESS, readiness)
    status_commit = sync_and_verify([config_rel, ready_rel], f"verify runtime API smoke {record['smoke_id']}")
    return {"status": "PASS", "record": relative_record, "record_commit": commit_record, "status_commit": status_commit, "response_id": record["response_id"], "actual_model_id": record["actual_model_id"], "request_count": 1}


def pilot_plan() -> list[tuple[str, str]]:
    return [("CANDIDATE23", "T01-C23-R1"), ("NEW22", "T01-N22-R1")]


def run_pilot() -> list[dict[str, Any]]:
    assert_main_checkout()
    runtime = load_json(RUNTIME_CONFIG)
    readiness = load_json(READINESS)
    if readiness.get("status") != "READY_FOR_PILOT":
        raise RuntimeError("Pilot is blocked until persistence_smoke and api_smoke have both passed")
    if runtime.get("api", {}).get("callability", {}).get("status") != "VERIFIED_CALLABLE":
        raise RuntimeError("Pilot is blocked: exact configured model has not been callable-verified")
    if runtime.get("execution_trace_capture", {}).get("status") != "VERIFIED":
        raise RuntimeError("Pilot is blocked: live execution trace capture has not been verified")
    if runtime.get("persistence", {}).get("durable_persistence") != "VERIFIED":
        raise RuntimeError("Pilot is blocked: GitHub durable persistence is not verified")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY_MISSING")
    expected = pilot_plan()
    plan = load_json(EXP_DIR / "run-plan.json")
    outputs = []
    for rule, run_id in expected:
        matches = [e for e in plan["entries"] if e.get("run_id") == run_id and e.get("testcase") == "T01" and e.get("rule") == rule and e.get("repeat") == 1]
        if len(matches) != 1:
            raise RuntimeError(f"Frozen pilot plan does not contain exactly one {run_id}")
        proc = command([
            sys.executable, str(RUNNER_PATH), "--experiment-config", str(EXPERIMENT_CONFIG),
            "--testcase", "T01", "--rule", rule, "--repeat", "1",
        ], timeout=1800, include_api_key=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Pilot stopped at {run_id}; inspect its persisted raw record and runner error output: {redact_text(proc.stderr[-2000:])}")
        report = json.loads(proc.stdout)
        rows = report.get("results", [])
        if len(rows) != 1 or rows[0].get("run_id") != run_id or rows[0].get("durability_state") != "DURABLY_COMPLETED":
            raise RuntimeError(f"{run_id} did not pass its per-run GitHub push and remote readback gate")
        outputs.append({"run_id": run_id, "durability_state": rows[0]["durability_state"], "raw_path": rows[0].get("raw_path"), "raw_sha256": rows[0].get("raw_sha256")})
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "static-check", "persistence-smoke", "api-smoke", "pilot"))
    parser.add_argument("--github-runtime", action="store_true", help="Require the pinned GitHub Actions runtime environment")
    args = parser.parse_args()
    try:
        if args.mode in {"validate", "static-check"}:
            if args.github_runtime:
                check_runtime_config(strict_actions_host=True)
                assert_actions_host()
            result = static_checks()
        elif args.mode == "persistence-smoke":
            result = run_persistence_smoke()
        elif args.mode == "api-smoke":
            result = run_api_smoke()
        else:
            result = {"status": "PILOT_RUN_RESULTS", "results": run_pilot()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Error output is sanitized; the API credential is never part of a serialized object.
        message = redact_text(exc)
        failure = {"status": "BLOCKED", "error": message}
        if args.mode == "api-smoke":
            failure["api_request_attempted"] = API_REQUEST_STARTED
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
