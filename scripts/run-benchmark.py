#!/usr/bin/env python3
"""Run independent, auditable benchmark requests against the OpenAI Responses API.

No model call is made by --dry-run. A real request requires complete verbatim rule
files plus environment variables. Every selected run starts with a fresh input
array and never supplies a previous_response_id.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import json
import mimetypes
import os
import platform
import re
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT  # Per-experiment data root; overridden by --experiment-config.
RUNS = ROOT / "runs"
API_URL = "https://api.openai.com/v1/responses"
BASELINE = (
    "You are participating in a controlled benchmark. Follow the single supplied "
    "rule text and the testcase. Use available tools when required. Do not claim an "
    "action or verification unless the execution record supports it."
)
RULE_NAMES = ("OLD", "NEW22")
RUN_ID_CODES = {"OLD": "OLD", "NEW22": "NEW22"}
RULE_PATHS = {name: ROOT / "rules" / f"{name}.md" for name in RULE_NAMES}
TESTCASES_PATH = ROOT / "testcases.json"
RUN_PLAN_PATH = ROOT / "run-plan.json"
RULES_MANIFEST_PATH = ROOT / "rules-manifest.json"
FIXTURES_ROOT = ROOT / "fixtures"
EXPERIMENT_CONFIG: dict[str, Any] = {}
ACCEPTED_RULE_STATUSES = {"ORIGINAL_VERBATIM", "VERIFIED_INPUT", "VERIFIED_CANDIDATE_FROM_RESEARCH_REPORT"}
MAX_TOOL_ROUNDS = 8
MAX_TOOL_OUTPUT_CHARS = 30000
RUNTIME_CONFIG: dict[str, Any] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact_error(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"https?://[^/\s@]+@", "https://[REDACTED]@", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b", "[REDACTED]", text)
    return text[:12000]


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, data: bytes) -> None:
    """Atomically create an immutable run artifact and fsync its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
        os.unlink(tmp)
        fsync_dir(path.parent)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            raise


def append_fsync(path: Path, line: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_dir(path.parent)
    finally:
        pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def configure_experiment(config_path: str | None) -> None:
    """Select an isolated experiment data root while sharing versioned project fixtures."""
    global ROOT, RUNS, RULE_NAMES, RUN_ID_CODES, RULE_PATHS
    global TESTCASES_PATH, RUN_PLAN_PATH, RULES_MANIFEST_PATH, FIXTURES_ROOT, EXPERIMENT_CONFIG
    global RUNTIME_CONFIG, API_URL, MAX_TOOL_ROUNDS
    if not config_path:
        return
    path = Path(config_path).resolve()
    if PROJECT_ROOT not in path.parents:
        raise ValueError("experiment config must be inside the project repository")
    config = read_json(path)
    root = path.parent

    def resolve(value: str) -> Path:
        resolved = (root / value).resolve()
        if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
            raise ValueError(f"configured path escapes the repository: {value}")
        return resolved

    names = tuple(config.get("rule_names", []))
    if len(names) != 2 or len(set(names)) != 2:
        raise ValueError("experiment config must define exactly two distinct rule_names")
    code_map = config.get("run_id_codes", {})
    if set(code_map) != set(names) or any(not isinstance(value, str) or not value for value in code_map.values()):
        raise ValueError("experiment config run_id_codes must map each rule to a non-empty code")
    rule_files = config.get("rule_files", {})
    if set(rule_files) != set(names):
        raise ValueError("experiment config rule_files must map both rule_names")
    ROOT = root
    RUNS = ROOT / config.get("runs_dir", "runs")
    RULE_NAMES = names
    RUN_ID_CODES = dict(code_map)
    RULE_PATHS = {name: resolve(rule_files[name]) for name in names}
    TESTCASES_PATH = resolve(config["testcases_path"])
    RUN_PLAN_PATH = resolve(config.get("run_plan_path", "run-plan.json"))
    RULES_MANIFEST_PATH = resolve(config.get("rules_manifest_path", "rules-manifest.json"))
    FIXTURES_ROOT = resolve(config.get("fixtures_root", "../../fixtures"))
    EXPERIMENT_CONFIG = config
    runtime_config_path = config.get("runtime_config_path")
    if not isinstance(runtime_config_path, str) or not runtime_config_path:
        raise ValueError("experiment config must select a runtime_config_path")
    RUNTIME_CONFIG = read_json(resolve(runtime_config_path))
    endpoint = RUNTIME_CONFIG.get("api", {}).get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ValueError("runtime config must define an HTTPS API endpoint")
    API_URL = endpoint
    MAX_TOOL_ROUNDS = int(RUNTIME_CONFIG.get("request", {}).get("max_tool_rounds", 8))


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def load_case_data() -> tuple[dict[str, Any], dict[str, Any]]:
    return read_json(TESTCASES_PATH), read_json(RUN_PLAN_PATH)


def rule_info(rule: str) -> tuple[bytes, str, dict[str, Any]]:
    if rule not in RULE_NAMES:
        raise ValueError(f"Unknown rule version: {rule}")
    path = RULE_PATHS[rule]
    raw = path.read_bytes()
    manifest = read_json(RULES_MANIFEST_PATH)["rules"][rule]
    actual_hash = sha256(raw)
    if actual_hash != manifest.get("sha256"):
        raise ValueError(f"Rule hash does not match rules-manifest.json for {rule}")
    return raw, actual_hash, manifest


def validate_layout() -> dict[str, Any]:
    testcase_doc, plan = load_case_data()
    cases = testcase_doc.get("cases", [])
    entries = plan.get("entries", [])
    errors: list[str] = []
    case_ids = [c.get("id") for c in cases]
    run_ids = [e.get("run_id") for e in entries]
    if len(cases) != 20 or testcase_doc.get("case_count") != 20:
        errors.append("testcases.json must contain exactly 20 cases")
    if len(set(case_ids)) != len(case_ids):
        errors.append("testcase ids are not unique")
    if len(entries) != 200 or plan.get("target_runs") != 200:
        errors.append("run-plan.json must contain exactly 200 entries")
    if len(set(run_ids)) != len(run_ids):
        errors.append("run_id values are not unique")
    expected = {f"{cid}-{RUN_ID_CODES[rule]}-R{repeat}" for cid in case_ids for rule in RULE_NAMES for repeat in range(1, 6)}
    if set(run_ids) != expected:
        errors.append("run-plan does not match 20 cases x 2 rules x 5 repeats")
    counts: dict[tuple[str, str], int] = {}
    for entry in entries:
        key = (entry.get("testcase"), entry.get("rule"))
        counts[key] = counts.get(key, 0) + 1
    if any(counts.get((cid, rule), 0) != 5 for cid in case_ids for rule in RULE_NAMES):
        errors.append("each testcase/rule pair must have five plan entries")
    if any(not all(k in c for k in ("required_actions", "success_criteria", "forbidden_completion_claims_without_evidence", "failure_state_expected_when")) for c in cases):
        errors.append("one or more cases are missing required oracle fields")
    hashes: dict[str, str] = {}
    complete: dict[str, bool] = {}
    usable: dict[str, bool] = {}
    for rule in RULE_NAMES:
        try:
            raw, digest, manifest = rule_info(rule)
            hashes[rule] = digest
            complete[rule] = manifest.get("source_status") in ACCEPTED_RULE_STATUSES and not raw.decode("utf-8", errors="replace").startswith("MISSING_INPUT")
            usable[rule] = manifest.get("usable_for_ab") is True
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"rule file/manifest invalid for {rule}: {exc}")
            complete[rule] = False
            usable[rule] = False
    if len(hashes) == 2 and len(set(hashes.values())) != 2:
        errors.append("the two experiment rule hashes must differ")
    api_config = RUNTIME_CONFIG.get("api", {})
    reasoning_config = RUNTIME_CONFIG.get("reasoning", {})
    model_parameters_pinned = bool(api_config.get("exact_model_id") and reasoning_config.get("effort")) if EXPERIMENT_CONFIG else True
    tool_config = RUNTIME_CONFIG.get("tools", {})
    actual_catalog = {"native": tool_config.get("native_api_tools", []), "functions": local_tool_specs()}
    configured_catalog = {"native": tool_config.get("native_api_tools", []), "functions": tool_config.get("benchmark_function_tools", [])}
    actual_catalog_hash = sha256(json.dumps(actual_catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) if RUNTIME_CONFIG else ""
    tool_catalog_matches = (
        bool(tool_config.get("catalog_sha256"))
        and configured_catalog == actual_catalog
        and actual_catalog_hash == tool_config.get("catalog_sha256")
    )
    if RUNTIME_CONFIG and not tool_catalog_matches:
        errors.append("runtime tool catalog hash does not match the runner tool definitions")
    baseline_hash_matches = (
        not RUNTIME_CONFIG
        or RUNTIME_CONFIG.get("shared_instruction", {}).get("sha256") == sha256(BASELINE.encode("utf-8"))
    )
    if RUNTIME_CONFIG and not baseline_hash_matches:
        errors.append("runtime shared-instruction hash does not match the runner baseline")
    environment = RUNTIME_CONFIG.get("execution_environment", {})
    runner_hash_matches = (
        not RUNTIME_CONFIG
        or environment.get("runner_sha256") == sha256(Path(__file__).read_bytes())
    )
    if environment.get("mode") == "github_actions":
        runtime_environment_matches = (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}" == environment.get("python_version")
            and runner_hash_matches
        )
    else:
        runtime_environment_matches = (
            not RUNTIME_CONFIG
            or (
                environment.get("python_version") == sys.version
                and environment.get("platform") == platform.platform()
                and runner_hash_matches
            )
        )
    generation = RUNTIME_CONFIG.get("generation", {})
    temperature_status = str(generation.get("temperature", {}).get("status", ""))
    temperature_policy_valid = (
        not RUNTIME_CONFIG
        or (
            reasoning_config.get("effort") == "none"
            or temperature_status.startswith("OMITTED")
        )
    )
    if RUNTIME_CONFIG and not temperature_policy_valid:
        errors.append("temperature must be omitted when the frozen reasoning effort is not none")
    runtime_frozen = RUNTIME_CONFIG.get("config_status") == "FROZEN"
    api_credential_available = bool(os.environ.get("OPENAI_API_KEY"))
    model_callable = api_config.get("callability", {}).get("status") == "VERIFIED_CALLABLE"
    durable_persistence_verified = RUNTIME_CONFIG.get("persistence", {}).get("durable_persistence") == "VERIFIED"
    readiness_path = ROOT / EXPERIMENT_CONFIG.get("readiness_path", "runner-readiness.json") if EXPERIMENT_CONFIG else None
    readiness_status = read_json(readiness_path).get("status") if readiness_path and readiness_path.exists() else None
    return {
        "valid_structure": not errors,
        "errors": errors,
        "testcase_count": len(cases),
        "plan_count": len(entries),
        "unique_run_ids": len(set(run_ids)),
        "rule_hashes": hashes,
        "rules_complete": complete,
        "rules_usable_for_selected_experiment": usable,
        "model_parameters_pinned": model_parameters_pinned,
        "runtime_config_frozen": runtime_frozen,
        "tool_catalog_matches_runner": tool_catalog_matches,
        "shared_instruction_matches_runner": baseline_hash_matches,
        "execution_environment_matches": runtime_environment_matches,
        "runner_hash_matches": runner_hash_matches,
        "temperature_policy_valid": temperature_policy_valid,
        "model_callable_verified": model_callable,
        "durable_persistence_verified": durable_persistence_verified,
        "readiness_status": readiness_status,
        "api_credential_available": api_credential_available,
        "ready_for_model_request": not errors and all(complete.values()) and all(usable.values()) and model_parameters_pinned and runtime_frozen and tool_catalog_matches and baseline_hash_matches and runtime_environment_matches and model_callable and durable_persistence_verified and readiness_status == "READY_FOR_PILOT" and api_credential_available,
    }


def selected_entries(args: argparse.Namespace) -> list[dict[str, Any]]:
    _, plan = load_case_data()
    selected = list(plan["entries"])
    if args.testcase:
        selected = [e for e in selected if e["testcase"] == args.testcase]
    if args.rule:
        selected = [e for e in selected if e["rule"] == args.rule]
    if args.repeat is not None:
        selected = [e for e in selected if e["repeat"] == args.repeat]
    if not args.all and not (args.testcase and args.rule and args.repeat is not None):
        raise ValueError("Choose one exact run with --testcase/--rule/--repeat, or use --all")
    return selected


def local_tool_specs() -> list[dict[str, Any]]:
    def spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "function", "name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}, "strict": True}

    return [
        spec("benchmark_calculate", "Evaluate a numeric arithmetic expression for this testcase.", {"expression": {"type": "string"}}, ["expression"]),
        spec("benchmark_read_fixture", "Read a UTF-8 file from this run's isolated testcase workspace; paths are relative to that workspace.", {"path": {"type": "string"}}, ["path"]),
        spec("benchmark_repo_search", "Search source files in this run's isolated repo/ fixture and return matching paths and lines.", {"query": {"type": "string"}}, ["query"]),
        spec("benchmark_repo_read", "Read one file from this run's isolated repo/ fixture.", {"path": {"type": "string"}}, ["path"]),
        spec("benchmark_history_lookup", "Search only the history explicitly supplied to this independent run. This benchmark supplies no prior conversation history.", {"query": {"type": "string"}}, ["query"]),
        spec("benchmark_service_read", "Read the current isolated service fixture state before an operation.", {}, []),
        spec("benchmark_service_apply", "Apply a JSON patch to the isolated service fixture state; configured permission failures are returned as errors.", {"changes": {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean", "null"]}}}, ["changes"]),
        spec("benchmark_service_verify", "Freshly reread the isolated service fixture state after an attempted operation.", {}, []),
        spec("benchmark_fetch_url", "A controlled URL-fetch tool for the testcase. Testcase fault injection is recorded in the execution trace.", {"url": {"type": "string"}}, ["url"]),
        spec("benchmark_workspace_write", "Write UTF-8 content to an allowed file in the isolated run workspace.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        spec("benchmark_run_tests", "Run the fixed Python unittest suite from the isolated repo/ fixture.", {}, []),
    ]


def safe_child(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    if candidate != base.resolve() and base.resolve() not in candidate.parents:
        raise ValueError("Path escapes the run workspace")
    return candidate


def safe_arithmetic(expression: str) -> float | int:
    tree = ast.parse(expression, mode="eval")
    allowed_bin = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    allowed_unary = (ast.UAdd, ast.USub)

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, allowed_bin):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("Exponent too large")
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right, ast.Div: lambda: left / right, ast.FloorDiv: lambda: left // right, ast.Mod: lambda: left % right, ast.Pow: lambda: left ** right}[type(node.op)]()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, allowed_unary):
            val = visit(node.operand)
            return val if isinstance(node.op, ast.UAdd) else -val
        raise ValueError("Expression contains unsupported syntax")

    result = visit(tree)
    if not isinstance(result, (int, float)) or not (-1e100 < result < 1e100):
        raise ValueError("Result is outside the allowed range")
    return result


def tool_executor(name: str, args: dict[str, Any], case: dict[str, Any], workspace: Path) -> dict[str, Any]:
    policy = case.get("tool_policy", {})
    injected = policy.get("fault_injection", {}).get(name)
    if injected:
        return {"ok": False, "error": injected, "controlled_fault_injection": True}
    try:
        if name == "benchmark_calculate":
            return {"ok": True, "expression": args["expression"], "value": safe_arithmetic(args["expression"])}
        if name in ("benchmark_read_fixture", "benchmark_repo_read"):
            rel = args["path"] if name == "benchmark_read_fixture" else str(Path("repo") / args["path"])
            path = safe_child(workspace, rel)
            content = path.read_text(encoding="utf-8")
            return {"ok": True, "path": rel, "content": content[:MAX_TOOL_OUTPUT_CHARS], "truncated": len(content) > MAX_TOOL_OUTPUT_CHARS}
        if name == "benchmark_repo_search":
            repo = safe_child(workspace, "repo")
            needle = str(args["query"]).casefold()
            found = []
            if repo.exists():
                for path in sorted(repo.rglob("*")):
                    if not path.is_file() or path.is_symlink() or path.stat().st_size > 200_000:
                        continue
                    try:
                        text = path.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for line_no, line in enumerate(text.splitlines(), 1):
                        if needle in line.casefold():
                            found.append({"path": str(path.relative_to(repo)), "line": line_no, "text": line[:500]})
            return {"ok": True, "query": args["query"], "matches": found, "searched_root": "repo/"}
        if name == "benchmark_history_lookup":
            return {"ok": True, "query": args["query"], "entries": [], "scope": "No prior conversation history is supplied to any independent run."}
        if name in ("benchmark_service_read", "benchmark_service_verify"):
            path = safe_child(workspace, "service.json")
            state = json.loads(path.read_text(encoding="utf-8"))
            return {"ok": True, "fresh_read": name.endswith("verify"), "state": state}
        if name == "benchmark_service_apply":
            path = safe_child(workspace, "service.json")
            state = json.loads(path.read_text(encoding="utf-8"))
            changes = args["changes"]
            if not changes or any(not isinstance(k, str) for k in changes):
                raise ValueError("Changes must be a non-empty JSON object")
            state.update(changes)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_bytes(json_bytes(state))
            os.replace(tmp, path)
            return {"ok": True, "applied": changes}
        if name == "benchmark_fetch_url":
            # Only permit the controlled failure fixture; arbitrary network access is disabled.
            if "example.invalid" in args["url"]:
                return {"ok": False, "error": "DNS lookup failed for reserved .invalid test domain", "controlled_test_domain": True}
            return {"ok": False, "error": "Network access is disabled for fixture URL fetches"}
        if name == "benchmark_workspace_write":
            path = safe_child(workspace, args["path"])
            if path.suffix not in {".py", ".txt", ".json", ".md"} or "repo" not in path.parts:
                raise ValueError("Writes are restricted to text files under repo/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return {"ok": True, "path": str(path.relative_to(workspace)), "bytes_written": path.stat().st_size}
        if name == "benchmark_run_tests":
            repo = safe_child(workspace, "repo")
            if not repo.is_dir():
                raise FileNotFoundError("repo/ fixture does not exist")
            def limits_and_drop_privileges() -> None:
                try:
                    import resource
                    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
                    resource.setrlimit(resource.RLIMIT_FSIZE, (2_000_000, 2_000_000))
                    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
                    if hasattr(resource, "RLIMIT_AS"):
                        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
                except (ImportError, OSError, ValueError):
                    pass
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    os.setgroups([])
                    os.setgid(65534)
                    os.setuid(65534)
            isolated_env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "HOME": "/nonexistent", "TMPDIR": "/tmp"}
            bootstrap = "import runpy,sys; sys.path.insert(0, '.'); sys.argv=['unittest','-v']; runpy.run_module('unittest', run_name='__main__')"
            result = subprocess.run([sys.executable, "-I", "-c", bootstrap], cwd=repo, capture_output=True, text=True, timeout=10, env=isolated_env, preexec_fn=limits_and_drop_privileges if os.name == "posix" else None)
            return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:], "isolation": "minimal environment, timeout, process resource limits where available, and unprivileged uid when parent is root; network namespace isolation is not enforced"}
        return {"ok": False, "error": f"Tool not implemented: {name}"}
    except Exception as exc:  # Tool errors are returned to the model and preserved in raw trace.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def tool_names_for(case: dict[str, Any]) -> set[str]:
    disabled = set(case.get("tool_policy", {}).get("disable", []))
    names = {spec["name"] for spec in local_tool_specs()}
    if "web_search" not in disabled:
        names.add("web_search")
    return names


def tool_permissions_for(case: dict[str, Any]) -> list[str]:
    names = tool_names_for(case)
    if any(str(path).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) for path in case.get("attachments", [])):
        names.add("input_image")
    return sorted(names)


def copy_workspace(case_id: str, run_id: str) -> Path:
    src = FIXTURES_ROOT / "workspaces" / case_id
    dst = RUNS / ".work" / run_id
    if dst.exists():
        raise FileExistsError(f"Run workspace already exists; refusing reuse: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.mkdir(parents=True)
    return dst


def attachment_path(case_id: str, raw_path: str, workspace: Path) -> Path:
    marker = f"fixtures/workspaces/{case_id}/"
    if raw_path.startswith(marker):
        return safe_child(workspace, raw_path[len(marker):])
    if raw_path.startswith("fixtures/images/"):
        return FIXTURES_ROOT / raw_path[len("fixtures/"):]
    return safe_child(workspace, raw_path)


def make_request_input(case: dict[str, Any], case_id: str, workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    available = []
    for item in case.get("attachments", []):
        marker = f"fixtures/workspaces/{case_id}/"
        available.append(item[len(marker):] if item.startswith(marker) else item)
    text = case["input"]
    if available:
        text += "\n\nTest resources available to the provided tools: " + ", ".join(available)
    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    attachment_records = []
    for item in case.get("attachments", []):
        path = attachment_path(case_id, item, workspace)
        if path.is_dir():
            inventory = []
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.is_symlink():
                    child_bytes = child.read_bytes()
                    inventory.append({"path": str(child.relative_to(workspace)), "sha256": sha256(child_bytes), "byte_count": len(child_bytes)})
            attachment_records.append({"path": item, "type": "directory", "files": inventory})
            continue
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        record: dict[str, Any] = {"path": item, "sha256": sha256(raw), "byte_count": len(raw), "mime_type": mime}
        if mime.startswith("image/"):
            data_uri = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            content.append({"type": "input_image", "image_url": data_uri})
            record["base64"] = base64.b64encode(raw).decode("ascii")
        else:
            record["text_content"] = raw.decode("utf-8")
            # The content is recorded for provenance but made available through the file tool,
            # so procedural file-reading remains observable in the actual model trace.
        attachment_records.append(record)
    return [{"role": "user", "content": content}], attachment_records


def build_toolset(case: dict[str, Any]) -> list[dict[str, Any]]:
    disabled = set(case.get("tool_policy", {}).get("disable", []))
    tools: list[dict[str, Any]] = []
    if "web_search" not in disabled:
        tools.append({"type": "web_search"})
    tools.extend(spec for spec in local_tool_specs() if spec["name"] not in disabled)
    return tools


def extract_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    pieces = []
    for item in response.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text") and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
    return "\n".join(pieces)


def function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in response.get("output", []) if item.get("type") == "function_call"]


def extract_web_events(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in response.get("output", []) if item.get("type") in ("web_search_call", "web_search_result")]


def api_post(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:10000]
        raise RuntimeError(redact_error(f"HTTP {exc.code}: {detail}")) from exc


def git(args: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    askpass_path: str | None = None
    if env.get("GITHUB_TOKEN"):
        fd, askpass_path = tempfile.mkstemp(prefix="benchmark-git-askpass-")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("#!/bin/sh\ncase \"$1\" in\n  *sername*) printf '%s\\n' 'x-access-token' ;;\n  *assword*) printf '%s\\n' \"$GITHUB_TOKEN\" ;;\n  *) exit 1 ;;\nesac\n")
        os.chmod(askpass_path, 0o700)
        env["GIT_ASKPASS"] = askpass_path
        env["GIT_ASKPASS_REQUIRE"] = "force"
    try:
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    finally:
        if askpass_path:
            Path(askpass_path).unlink(missing_ok=True)


def ensure_main_worktree() -> None:
    inside = git(["rev-parse", "--is-inside-work-tree"])
    branch = git(["branch", "--show-current"])
    origin = git(["remote", "get-url", "origin"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RuntimeError("Durable execution requires a Git worktree")
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        raise RuntimeError("Durable execution requires the main branch")
    if origin.returncode != 0 or not origin.stdout.strip().startswith("https://github.com/"):
        raise RuntimeError("Durable execution requires an HTTPS GitHub origin remote")
    staged = git(["diff", "--cached", "--name-only"])
    if staged.returncode != 0 or staged.stdout.strip():
        raise RuntimeError("Durable execution requires an empty Git index; commit unrelated staged changes first")


def commit_push(paths: list[str], message: str) -> str:
    ensure_main_worktree()
    added = git(["add", "--", *paths])
    if added.returncode:
        raise RuntimeError(redact_error(f"git add failed: {added.stderr.strip()}"))
    changed = git(["diff", "--cached", "--quiet"])
    if changed.returncode == 0:
        head = git(["rev-parse", "HEAD"])
        if head.returncode:
            raise RuntimeError("No commit exists to push")
        commit_sha = head.stdout.strip()
    else:
        committed = git(["commit", "-m", message], timeout=45)
        if committed.returncode:
            raise RuntimeError(redact_error(f"git commit failed: {committed.stderr.strip()}"))
        head = git(["rev-parse", "HEAD"])
        commit_sha = head.stdout.strip()
    pushed = git(["push", "origin", "main"], timeout=90)
    if pushed.returncode:
        raise RuntimeError(redact_error(f"git push failed: {pushed.stderr.strip() or pushed.stdout.strip()}"))
    fetched = git(["fetch", "origin", "main"], timeout=90)
    if fetched.returncode:
        raise RuntimeError(redact_error(f"git fetch for remote verification failed: {fetched.stderr.strip()}"))
    return commit_sha


def verify_remote_file(path: str, expected: bytes) -> str:
    remote = git(["show", f"origin/main:{path}"], timeout=30)
    if remote.returncode:
        raise RuntimeError(f"remote readback failed for {path}: {remote.stderr.strip()}")
    actual = remote.stdout.encode("utf-8")
    if actual != expected:
        raise RuntimeError(f"remote readback content mismatch for {path}")
    head = git(["rev-parse", "refs/remotes/origin/main"])
    if head.returncode:
        raise RuntimeError("Cannot read fetched remote main SHA")
    return head.stdout.strip()


def append_event(event: dict[str, Any]) -> bytes:
    path = ROOT / "run-reservations.jsonl"
    line = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    append_fsync(path, line)
    return line


def event_states(run_id: str) -> list[dict[str, Any]]:
    path = ROOT / "run-reservations.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            if event.get("run_id") == run_id:
                result.append(event)
    return result


def persist_event(run_id: str, event: dict[str, Any]) -> None:
    path = ROOT / "run-reservations.jsonl"
    line = append_event(event)
    rel = repo_relative(path)
    commit_push([rel], f"benchmark run {run_id}: {event['state']}")
    verify_remote_file(rel, path.read_bytes())


def record_receipt(entry: dict[str, Any], receipt: dict[str, Any]) -> None:
    existing = event_states(entry["run_id"])
    if any(e.get("state") == receipt["state"] and e.get("raw_sha256") == receipt.get("raw_sha256") for e in existing):
        rel = repo_relative(ROOT / "run-reservations.jsonl")
        commit_push([rel], f"benchmark run {entry['run_id']}: resume receipt persistence")
        verify_remote_file(rel, (PROJECT_ROOT / rel).read_bytes())
        return
    persist_event(entry["run_id"], receipt)


def append_runs_index(record: dict[str, Any]) -> None:
    path = ROOT / "runs.jsonl"
    run_id = record["run_id"]
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("run_id") == run_id:
                raise FileExistsError(f"run_id already appears in runs.jsonl: {run_id}")
    payload = {
        "run_id": run_id,
        "testcase_id": record["testcase_id"],
        "rule_version": record["rule_version"],
        "raw_path": record["raw_path"],
        "raw_sha256": record["raw_sha256"],
        "execution_status": record["execution_status"],
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
    }
    append_fsync(path, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")


def raw_path_for(entry: dict[str, Any]) -> Path:
    return RUNS / entry["testcase"] / entry["rule"] / f"R{entry['repeat']}.json"


def acquire_run_lock(entry: dict[str, Any]) -> Path:
    path = RUNS / ".locks" / f"{entry['run_id']}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_new(path, json_bytes({"run_id": entry["run_id"], "pid": os.getpid(), "acquired_at": utc_now()}))
    return path


def release_run_lock(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.exists():
        fsync_dir(path.parent)


def prepare_reservation(entry: dict[str, Any], request_sha: str, model: str, reasoning: str) -> None:
    run_id = entry["run_id"]
    history = event_states(run_id)
    states = [e.get("state") for e in history]
    if "REQUEST_STARTED" in states or "LOCAL_COMPLETED" in states or "DURABLY_COMPLETED" in states:
        raise RuntimeError(f"run_id {run_id} already has a request/resolution event; refusing duplicate model call")
    if not states:
        event = {"run_id": run_id, "state": "RESERVED", "request_sha256": request_sha, "model_id": model, "reasoning_setting": reasoning, "recorded_at": utc_now()}
        persist_event(run_id, event)
    event = {"run_id": run_id, "state": "REQUEST_STARTED", "request_sha256": request_sha, "model_id": model, "reasoning_setting": reasoning, "recorded_at": utc_now()}
    persist_event(run_id, event)


def persist_raw(entry: dict[str, Any], raw_path: Path, raw: bytes, record: dict[str, Any]) -> tuple[str, bool, str]:
    rel_raw = str(raw_path.relative_to(ROOT))
    index = ROOT / "runs.jsonl"
    repo_raw = repo_relative(raw_path)
    repo_index = repo_relative(index)
    record["raw_path"] = rel_raw
    record["raw_sha256"] = sha256(raw)
    append_runs_index(record)
    try:
        commit_sha = commit_push([repo_raw, repo_index], f"benchmark run {entry['run_id']}: raw result")
        remote_raw_sha = verify_remote_file(repo_raw, raw)
        verify_remote_file(repo_index, index.read_bytes())
    except Exception as exc:
        record["durability_state"] = "LOCAL_COMPLETED" if record["execution_status"] == "MODEL_RESPONSE_RECEIVED" else "LOCAL_FAILED_ATTEMPT"
        record["durability_error"] = str(exc)
        state = record["durability_state"]
        if not any(e.get("state") == state and e.get("raw_sha256") == sha256(raw) for e in event_states(entry["run_id"])):
            append_event({"run_id": entry["run_id"], "state": state, "raw_sha256": sha256(raw), "reason": str(exc), "recorded_at": utc_now()})
        # The raw file/index already exist locally. Preserve them and never retry the model request.
        return "", False, str(exc)
    receipt_state = "DURABLY_COMPLETED" if record["execution_status"] == "MODEL_RESPONSE_RECEIVED" else "DURABLY_RECORDED_FAILED_ATTEMPT"
    receipt = {"run_id": entry["run_id"], "state": receipt_state, "raw_sha256": sha256(raw), "raw_commit_sha": commit_sha, "remote_main_sha_after_readback": remote_raw_sha, "remote_readback_at": utc_now()}
    try:
        record_receipt(entry, receipt)
    except Exception as exc:
        # Raw data was already read back from origin/main, so receipt-sync trouble does not
        # change the remote-readback fact. The append-only local event can be synced on resume.
        record["receipt_sync_error"] = str(exc)
    record["durability_state"] = receipt_state
    return commit_sha, True, receipt_state


def response_loop(case: dict[str, Any], instruction: str, initial_input: list[dict[str, Any]],
                  tools: list[dict[str, Any]], workspace: Path, model: str, reasoning: str,
                  temperature: float | None, max_tokens: int, timeout: int, api_key: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    full_input = list(initial_input)
    trace: list[dict[str, Any]] = []
    tool_calls_log: list[dict[str, Any]] = []
    tool_results_log: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    assistant = ""
    actual_model = model
    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        payload = {
            "model": model,
            "instructions": instruction,
            "input": full_input,
            "tools": tools,
            "reasoning": {"effort": reasoning},
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        # No previous_response_id or conversation/session identifier is sent.
        trace.append({"kind": "api_request", "round": round_no, "payload": payload})
        try:
            response = api_post(payload, api_key, timeout)
        except Exception as exc:
            errors.append({"stage": "api_request", "round": round_no, "error": f"{type(exc).__name__}: {exc}"})
            trace.append({"kind": "api_error", "round": round_no, "error": errors[-1]["error"]})
            break
        actual_model = response.get("model", model)
        trace.append({"kind": "api_response", "round": round_no, "response": response})
        assistant = extract_text(response)
        web_events = extract_web_events(response)
        for event in web_events:
            tool_calls_log.append({"tool_name": "web_search", "arguments": event.get("action", {}), "raw_call": event})
            tool_results_log.append({"tool_name": "web_search", "result": event})
        calls = function_calls(response)
        if not calls:
            if response.get("status") not in (None, "completed"):
                errors.append({"stage": "response", "status": response.get("status"), "incomplete_details": response.get("incomplete_details")})
            break
        round_outputs = []
        for call in calls:
            call_id = call.get("call_id") or call.get("id")
            name = call.get("name", "")
            try:
                arguments = json.loads(call.get("arguments", "{}"))
            except json.JSONDecodeError as exc:
                arguments = {}
                result = {"ok": False, "error": f"Invalid function-call JSON: {exc}"}
            else:
                if name not in tool_names_for(case):
                    result = {"ok": False, "error": f"Tool is not enabled for this testcase: {name}"}
                else:
                    result = tool_executor(name, arguments, case, workspace)
            tool_calls_log.append({"tool_name": name, "call_id": call_id, "arguments": arguments, "raw_call": call})
            tool_results_log.append({"tool_name": name, "call_id": call_id, "result": result})
            trace.append({"kind": "function_tool_result", "round": round_no, "tool_name": name, "call_id": call_id, "arguments": arguments, "result": result})
            output_item = {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)[:MAX_TOOL_OUTPUT_CHARS]}
            if call.get("caller") is not None:
                output_item["caller"] = call["caller"]
            round_outputs.append(output_item)
        # Resubmit this run's complete local context, not prior_response_id. No other run shares it.
        full_input.extend(response.get("output", []))
        full_input.extend(round_outputs)
    else:
        errors.append({"stage": "tool_loop", "error": f"Maximum tool rounds reached ({MAX_TOOL_ROUNDS})"})
    return assistant, tool_calls_log, tool_results_log, trace, errors, actual_model


def snapshot_workspace(workspace: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        raw = path.read_bytes()
        item: dict[str, Any] = {"path": str(path.relative_to(workspace)), "sha256": sha256(raw), "byte_count": len(raw)}
        try:
            item["utf8_content"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            item["base64"] = base64.b64encode(raw).decode("ascii")
        items.append(item)
    return items


def execute_entry(entry: dict[str, Any], case: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    layout = validate_layout()
    raw_rule, rule_hash, manifest = rule_info(entry["rule"])
    if not layout["valid_structure"]:
        raise RuntimeError("Integrity validation failed: " + "; ".join(layout["errors"]))
    target = raw_path_for(entry)
    if target.exists():
        raise FileExistsError(f"run_id already has a raw file; refusing duplicate: {target}")
    if not manifest.get("usable_for_ab"):
        raise RuntimeError(f"{entry['rule']} source is MISSING_INPUT; model request blocked")
    if dry_run:
        return {"run_id": entry["run_id"], "dry_run": True, "model_request_made": False, "rule_hash": rule_hash, "case_id": case["id"], "input": case["input"], "tools": sorted(tool_names_for(case))}
    if not layout["ready_for_model_request"]:
        raise RuntimeError("READY_FOR_PILOT gate is not satisfied; real model requests are blocked")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_config = RUNTIME_CONFIG["api"]
    reasoning_config = RUNTIME_CONFIG["reasoning"]
    request_config = RUNTIME_CONFIG["request"]
    generation_config = RUNTIME_CONFIG["generation"]
    model = str(api_config["exact_model_id"])
    reasoning = str(reasoning_config["effort"])
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required via environment variable; no key is stored in files")
    temperature_spec = generation_config.get("temperature", {"status": "OMITTED"})
    temperature = None if str(temperature_spec.get("status", "OMITTED")).startswith("OMITTED") else float(temperature_spec["value"])
    max_tokens = int(generation_config["max_output_tokens"])
    timeout = int(request_config["timeout_seconds"])
    if (temperature is not None and not 0 <= temperature <= 2) or max_tokens < 1 or timeout < 1:
        raise RuntimeError("Invalid frozen runtime parameter")

    workspace = copy_workspace(case["id"], entry["run_id"])
    user_input, attachment_records = make_request_input(case, case["id"], workspace)
    initial_workspace_snapshot = snapshot_workspace(workspace)
    toolset = build_toolset(case)
    instructions = BASELINE + "\n\n--- SINGLE RULE VERSION: " + entry["rule"] + " ---\n" + raw_rule.decode("utf-8")
    request_fingerprint = sha256(json_bytes({"model": model, "instructions": instructions, "input": user_input, "tools": toolset, "reasoning": reasoning, "temperature": temperature, "max_output_tokens": max_tokens, "store": False}))
    started = utc_now()
    prepare_reservation(entry, request_fingerprint, model, reasoning)
    errors: list[dict[str, Any]] = []
    try:
        assistant, tool_calls, tool_results, trace, errors, actual_model = response_loop(
            case, instructions, user_input, toolset, workspace, model, reasoning, temperature, max_tokens, timeout, api_key
        )
        status = "MODEL_RESPONSE_RECEIVED" if assistant.strip() and not errors else "RUN_INCOMPLETE"
    except Exception as exc:
        assistant, tool_calls, tool_results, trace, actual_model = "", [], [], [], model
        errors = [{"stage": "request", "error": f"{type(exc).__name__}: {exc}"}]
        status = "REQUEST_FAILED"
    finished = utc_now()
    record = {
        "run_id": entry["run_id"], "testcase_id": case["id"], "rule_version": entry["rule"], "rule_hash": rule_hash,
        "model": actual_model, "model_requested": model, "reasoning_setting": reasoning,
        "model_parameters": {"temperature": temperature, "temperature_status": temperature_spec.get("status", "OMITTED"), "max_output_tokens": max_tokens, "request_timeout_seconds": timeout, "store": False, "previous_response_id": None, "api_endpoint": API_URL},
        "tool_permissions": tool_permissions_for(case), "tool_policy": case.get("tool_policy", {}),
        "execution_environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "runner_sha256": sha256(Path(__file__).read_bytes()),
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "runner_os": os.environ.get("RUNNER_OS"),
            "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
        },
        "testcase_sha256": sha256(json_bytes(case)), "input": case["input"], "input_payload": user_input,
        "attachments": attachment_records, "initial_workspace_snapshot": initial_workspace_snapshot,
        "final_workspace_snapshot": snapshot_workspace(workspace),
        "assistant_output": assistant, "tool_calls": tool_calls, "tool_results": tool_results,
        "execution_trace": trace, "errors": errors, "started_at": started, "finished_at": finished,
        "execution_status": status, "durability_state": "PENDING", "request_sha256": request_fingerprint,
        "session_isolation": {"fresh_input_array": True, "previous_response_id_sent": False, "cross_run_history": False},
    }
    raw = json_bytes(record)
    raw_path = raw_path_for(entry)
    write_new(raw_path, raw)
    _, durable, durability_status = persist_raw(entry, raw_path, raw, record)
    record["durability_state"] = durability_status if durable else ("LOCAL_COMPLETED" if status == "MODEL_RESPONSE_RECEIVED" else "LOCAL_FAILED_ATTEMPT")
    return {"run_id": entry["run_id"], "execution_status": status, "durability_state": record["durability_state"], "raw_path": record["raw_path"], "raw_sha256": record["raw_sha256"], "model_request_made": True, "errors": errors, "durability_error": record.get("durability_error")}


def persist_existing(entry: dict[str, Any]) -> dict[str, Any]:
    """Resume persistence for an existing raw run; this path never calls the model."""
    path = raw_path_for(entry)
    if not path.exists():
        return {"run_id": entry["run_id"], "status": "NO_LOCAL_RAW"}
    raw = path.read_bytes()
    index_path = ROOT / "runs.jsonl"
    repo_raw = repo_relative(path)
    repo_index = repo_relative(index_path)
    indexed = index_path.exists() and any(json.loads(x).get("run_id") == entry["run_id"] for x in index_path.read_text(encoding="utf-8").splitlines() if x.strip())
    if not indexed:
        record = read_json(path)
        record["raw_path"] = str(path.relative_to(ROOT))
        record["raw_sha256"] = sha256(raw)
        append_runs_index(record)
    try:
        head = commit_push([repo_raw, repo_index], f"benchmark run {entry['run_id']}: resume persistence")
        remote_head = verify_remote_file(repo_raw, raw)
        verify_remote_file(repo_index, index_path.read_bytes())
        record = read_json(path)
        receipt = {"run_id": entry["run_id"], "state": "DURABLY_COMPLETED" if record.get("execution_status") == "MODEL_RESPONSE_RECEIVED" else "DURABLY_RECORDED_FAILED_ATTEMPT", "raw_sha256": sha256(raw), "raw_commit_sha": head, "remote_main_sha_after_readback": remote_head, "remote_readback_at": utc_now()}
        receipt_error = None
        try:
            record_receipt(entry, receipt)
        except Exception as exc:
            receipt_error = str(exc)
        return {"run_id": entry["run_id"], "status": receipt["state"], "model_request_made": False, "receipt_sync_error": receipt_error}
    except Exception as exc:
        record = read_json(path)
        state = "LOCAL_COMPLETED" if record.get("execution_status") == "MODEL_RESPONSE_RECEIVED" else "LOCAL_FAILED_ATTEMPT"
        if not any(e.get("state") == state and e.get("raw_sha256") == sha256(raw) for e in event_states(entry["run_id"])):
            append_event({"run_id": entry["run_id"], "state": state, "raw_sha256": sha256(raw), "reason": str(exc), "recorded_at": utc_now()})
        return {"run_id": entry["run_id"], "status": state, "model_request_made": False, "persistence_error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-config", help="JSON config selecting an isolated experiment data root")
    parser.add_argument("--testcase", help="Case id, e.g. T01")
    parser.add_argument("--rule", help="Rule label from the selected experiment config")
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--all", action="store_true", help="Run the fixed plan (not recommended until pilot approval)")
    parser.add_argument("--dry-run", action="store_true", help="Validate selection and print plan; never call a model")
    parser.add_argument("--resume", action="store_true", help="Persist existing local raw runs first; never re-call an existing run_id")
    args = parser.parse_args()
    try:
        configure_experiment(args.experiment_config)
        if args.rule and args.rule not in RULE_NAMES:
            raise ValueError(f"Unknown rule {args.rule}; choose from: {', '.join(RULE_NAMES)}")
        report = validate_layout()
        if not report["valid_structure"]:
            raise RuntimeError("Integrity validation failed: " + "; ".join(report["errors"]))
        entries = selected_entries(args)
        if not entries:
            raise ValueError("No run-plan entry matches the selection")
        if args.resume:
            output = []
            for entry in entries:
                if raw_path_for(entry).exists():
                    output.append(persist_existing(entry))
                    release_run_lock(RUNS / ".locks" / f"{entry['run_id']}.lock")
                elif event_states(entry["run_id"]) and "REQUEST_STARTED" in [e.get("state") for e in event_states(entry["run_id"])]:
                    output.append({"run_id": entry["run_id"], "status": "IN_FLIGHT_UNRESOLVED", "model_request_made": False, "action": "manual reconciliation required; duplicate call blocked"})
            print(json.dumps({"integrity": report, "resume_results": output}, ensure_ascii=False, indent=2))
            return 0
        if args.dry_run:
            cases = {c["id"]: c for c in load_case_data()[0]["cases"]}
            output = [{"run_id": e["run_id"], "testcase": e["testcase"], "rule": e["rule"], "repeat": e["repeat"], "tools": tool_permissions_for(cases[e["testcase"]]), "model_request_made": False} for e in entries]
            print(json.dumps({"integrity": report, "selection_count": len(entries), "selected": output, "model_request_made": False}, ensure_ascii=False, indent=2))
            return 0
        cases = {c["id"]: c for c in load_case_data()[0]["cases"]}
        output = []
        # The saved plan order is randomized once; no condition-dependent reordering occurs here.
        for entry in entries:
            existing = raw_path_for(entry)
            if existing.exists():
                output.append(persist_existing(entry))
                release_run_lock(RUNS / ".locks" / f"{entry['run_id']}.lock")
                continue
            states = [e.get("state") for e in event_states(entry["run_id"])]
            if "REQUEST_STARTED" in states:
                output.append({"run_id": entry["run_id"], "status": "IN_FLIGHT_UNRESOLVED", "model_request_made": False, "action": "manual reconciliation required; duplicate call blocked"})
                continue
            lock = acquire_run_lock(entry)
            try:
                output.append(execute_entry(entry, cases[entry["testcase"]], dry_run=False))
            except Exception:
                history = event_states(entry["run_id"])
                request_started = any(e.get("state") == "REQUEST_STARTED" for e in history)
                if not raw_path_for(entry).exists() and not request_started:
                    release_run_lock(lock)
                raise
            else:
                release_run_lock(lock)
        print(json.dumps({"integrity": report, "results": output}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}", "model_request_made": False}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
