"""Paired context and memory benchmark used for resume-grade evidence.

The deterministic mode intentionally reports estimated input tokens.  It is a
runtime A/B test, not a claim about provider billing telemetry.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.context_manager import ContextManager
from ..core.runtime import Pico, SessionStore
from ..core.workspace import WorkspaceContext
from ..providers import OpenAICompatibleModelClient
from ..testing import ScriptedModelClient


DEFAULT_ARTIFACT_PATH = Path("artifacts/context-memory-paired-v1.json")
DEFAULT_REPORT_PATH = Path("artifacts/context-memory-paired-v1.md")
VARIANTS = ("full", "no_context_reduction", "no_memory")


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    category: str
    files: dict[str, str]
    evidence_paths: tuple[str, ...]
    evidence_markers: tuple[str, ...]
    followup_prompt: str
    expected_answer: str
    edit_path: str = ""
    old_text: str = ""
    new_text: str = ""


TASKS = (
    BenchmarkTask("lookup_timeout", "code_lookup", {"config/runtime.txt": "timeout_seconds=120\n"}, ("config/runtime.txt",), ("timeout_seconds=120",), "What timeout is configured? Reply with the exact setting only.", "timeout_seconds=120"),
    BenchmarkTask("lookup_api_prefix", "code_lookup", {"config/api.txt": "api_prefix=/v1/internal\n"}, ("config/api.txt",), ("api_prefix=/v1/internal",), "What API prefix is configured? Reply with the exact setting only.", "api_prefix=/v1/internal"),
    BenchmarkTask("lookup_retry_limit", "code_lookup", {"config/retry.txt": "retry_limit=4\n"}, ("config/retry.txt",), ("retry_limit=4",), "What retry limit is configured? Reply with the exact setting only.", "retry_limit=4"),
    BenchmarkTask("lookup_cache_ttl", "code_lookup", {"config/cache.txt": "cache_ttl_seconds=300\n"}, ("config/cache.txt",), ("cache_ttl_seconds=300",), "What cache TTL is configured? Reply with the exact setting only.", "cache_ttl_seconds=300"),
    BenchmarkTask("lookup_worker_count", "code_lookup", {"config/workers.txt": "worker_count=6\n"}, ("config/workers.txt",), ("worker_count=6",), "What worker count is configured? Reply with the exact setting only.", "worker_count=6"),
    BenchmarkTask("analyze_auth_flow", "cross_file_analysis", {"src/route.txt": "login calls auth.validate\n", "src/auth.txt": "auth.validate uses strict policy\n"}, ("src/route.txt", "src/auth.txt"), ("login calls auth.validate", "auth.validate uses strict policy"), "What policy does the login flow use? Reply with the conclusion only.", "strict policy"),
    BenchmarkTask("analyze_order_storage", "cross_file_analysis", {"src/order.txt": "create_order calls repository.save\n", "src/repository.txt": "repository.save writes to postgres\n"}, ("src/order.txt", "src/repository.txt"), ("create_order calls repository.save", "repository.save writes to postgres"), "Where is a created order stored? Reply with the conclusion only.", "postgres"),
    BenchmarkTask("analyze_event_delivery", "cross_file_analysis", {"src/publisher.txt": "publish delegates to queue.send\n", "src/queue.txt": "queue.send targets audit-events\n"}, ("src/publisher.txt", "src/queue.txt"), ("publish delegates to queue.send", "queue.send targets audit-events"), "Which queue receives published events? Reply with the conclusion only.", "audit-events"),
    BenchmarkTask("analyze_health_dependency", "cross_file_analysis", {"src/health.txt": "health_check calls database.ping\n", "src/database.txt": "database.ping checks primary-db\n"}, ("src/health.txt", "src/database.txt"), ("health_check calls database.ping", "database.ping checks primary-db"), "Which dependency determines database health? Reply with the conclusion only.", "primary-db"),
    BenchmarkTask("analyze_report_source", "cross_file_analysis", {"src/report.txt": "build_report calls metrics.load\n", "src/metrics.txt": "metrics.load reads warehouse_daily\n"}, ("src/report.txt", "src/metrics.txt"), ("build_report calls metrics.load", "metrics.load reads warehouse_daily"), "What data source feeds the report? Reply with the conclusion only.", "warehouse_daily"),
    BenchmarkTask("edit_timeout", "code_edit", {"constraints.txt": "required timeout is 45\n", "src/client.txt": "timeout=10\n"}, ("constraints.txt", "src/client.txt"), ("required timeout is 45", "timeout=10"), "Apply the remembered timeout constraint to src/client.txt.", "updated timeout", "src/client.txt", "timeout=10", "timeout=45"),
    BenchmarkTask("edit_retries", "code_edit", {"constraints.txt": "required retries is 5\n", "src/worker.txt": "retries=2\n"}, ("constraints.txt", "src/worker.txt"), ("required retries is 5", "retries=2"), "Apply the remembered retry constraint to src/worker.txt.", "updated retries", "src/worker.txt", "retries=2", "retries=5"),
    BenchmarkTask("edit_schema", "code_edit", {"constraints.txt": "required schema_version is 2\n", "config/service.txt": "schema_version=1\n"}, ("constraints.txt", "config/service.txt"), ("required schema_version is 2", "schema_version=1"), "Apply the remembered schema constraint to config/service.txt.", "updated schema", "config/service.txt", "schema_version=1", "schema_version=2"),
    BenchmarkTask("edit_pool_size", "code_edit", {"constraints.txt": "required pool_size is 8\n", "config/database.txt": "pool_size=3\n"}, ("constraints.txt", "config/database.txt"), ("required pool_size is 8", "pool_size=3"), "Apply the remembered pool constraint to config/database.txt.", "updated pool size", "config/database.txt", "pool_size=3", "pool_size=8"),
    BenchmarkTask("edit_feature_flag", "code_edit", {"constraints.txt": "required feature flag is enabled\n", "config/feature.txt": "feature_enabled=false\n"}, ("constraints.txt", "config/feature.txt"), ("required feature flag is enabled", "feature_enabled=false"), "Apply the remembered feature constraint to config/feature.txt.", "updated feature flag", "config/feature.txt", "feature_enabled=false", "feature_enabled=true"),
)


class _PairedBenchmarkClient(ScriptedModelClient):
    def __init__(self, task: BenchmarkTask):
        super().__init__([])
        self.task = task
        self.phase = "bootstrap"
        self.read_index = 0
        self.followup_reads = 0
        self.followup_prompt_chars = 0
        self.followup_model_calls = 0

    def start_followup(self):
        self.phase = "followup"
        self.read_index = 0
        self.followup_reads = 0
        self.followup_prompt_chars = 0
        self.followup_model_calls = 0

    def complete(self, prompt, max_new_tokens, **kwargs):
        del max_new_tokens, kwargs
        prompt = str(prompt)
        self.prompts.append(prompt)
        estimated = max(1, (len(prompt) + 3) // 4)
        self.last_completion_metadata = {
            "input_tokens": estimated,
            "output_tokens": 16,
            "synthetic": True,
        }
        if self.phase == "bootstrap":
            if self.read_index < len(self.task.evidence_paths):
                path = self.task.evidence_paths[self.read_index]
                self.read_index += 1
                return _tool_call("read_file", {"path": path, "start": 1, "end": 80})
            self.phase = "waiting"
            return "<final>Done.</final>"

        if self.phase == "followup":
            self.followup_prompt_chars += len(prompt)
            self.followup_model_calls += 1
            evidence_visible = all(marker.lower() in prompt.lower() for marker in self.task.evidence_markers)
            if not evidence_visible and self.read_index < len(self.task.evidence_paths):
                path = self.task.evidence_paths[self.read_index]
                self.read_index += 1
                self.followup_reads += 1
                return _tool_call("read_file", {"path": path, "start": 1, "end": 80})
            if self.task.category == "code_edit":
                self.phase = "edited"
                return _tool_call(
                    "patch_file",
                    {"path": self.task.edit_path, "old_text": self.task.old_text, "new_text": self.task.new_text},
                )
            self.phase = "done"
            return f"<final>{self.task.expected_answer}</final>"

        if self.phase == "edited":
            self.followup_prompt_chars += len(prompt)
            self.followup_model_calls += 1
            self.phase = "done"
            return f"<final>{self.task.expected_answer}</final>"
        return f"<final>{self.task.expected_answer}</final>"


class _LiveBenchmarkClient(OpenAICompatibleModelClient):
    def __init__(self, *, model, base_url, api_key, timeout=180):
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.0,
            timeout=timeout,
            reasoning_effort="none",
        )
        self.prompts = []
        self.followup_usage = []
        self.in_followup = False

    def start_followup(self):
        self.in_followup = True
        self.prompts = []
        self.followup_usage = []

    def complete(self, prompt, max_new_tokens, **kwargs):
        if self.in_followup:
            self.prompts.append(str(prompt))
        text = super().complete(prompt, max_new_tokens, **kwargs)
        if self.in_followup:
            self.followup_usage.append(dict(self.last_completion_metadata or {}))
        return text


def _tool_call(name, args):
    return f'<tool>{json.dumps({"name": name, "args": args}, separators=(",", ":"))}</tool>'


def _write_fixture(root: Path, task: BenchmarkTask):
    for relative, content in task.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _truncate_read_observations(agent):
    for item in agent.session.get("history", []):
        if item.get("role") == "tool" and item.get("name") == "read_file":
            item["content"] = "(read observation removed from transcript; use memory or reread)"
    agent.session_store.save(agent.session)


def _seed_long_history(agent, entries=18):
    for index in range(int(entries)):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"unrelated-history-{index}-" + ("context-noise-" * 110),
                "created_at": f"2026-07-01T10:{index:02d}:00+00:00",
            }
        )


def _build_agent(root: Path, task: BenchmarkTask, client=None):
    client = client or _PairedBenchmarkClient(task)
    agent = Pico(
        model_client=client,
        workspace=WorkspaceContext.build(root),
        session_store=SessionStore(root / ".pico" / "sessions"),
        approval_policy="auto",
        max_steps=8,
        max_new_tokens=768,
        auto_dream=False,
    )
    agent.context_manager = ContextManager(
        agent,
        total_budget=60_000,
        section_budgets={"history": 6_000, "relevant_memory": 2_000},
    )
    return agent, client


def _verify(task: BenchmarkTask, root: Path, answer: str):
    if task.category == "code_edit":
        text = (root / task.edit_path).read_text(encoding="utf-8")
        return task.new_text in text and task.old_text not in text
    return task.expected_answer.lower() in answer.strip().lower()


def _run_row(
    task: BenchmarkTask,
    variant: str,
    repeat: int,
    *,
    mode="deterministic",
    model="mimo-v2.5-pro",
    base_url="https://api.xiaomimimo.com/v1",
    api_key="",
):
    with tempfile.TemporaryDirectory(prefix="pico-context-memory-paired-") as temp_dir:
        root = Path(temp_dir)
        _write_fixture(root, task)
        if mode == "live":
            client = _LiveBenchmarkClient(model=model, base_url=base_url, api_key=api_key)
        else:
            client = _PairedBenchmarkClient(task)
        agent, client = _build_agent(root, task, client=client)
        bootstrap = (
            "Read every listed evidence file with tools and remember the relevant facts. "
            "After all files have been read, reply with Done only: " + ", ".join(task.evidence_paths)
        )
        bootstrap_answer = agent.ask(bootstrap)
        if mode == "deterministic" and bootstrap_answer != "Done.":
            raise AssertionError(f"bootstrap failed for {task.id}")
        _truncate_read_observations(agent)
        _seed_long_history(agent)
        if variant == "no_context_reduction":
            agent.feature_flags["context_reduction"] = False
        elif variant == "no_memory":
            agent.feature_flags["memory"] = False
            agent.feature_flags["relevant_memory"] = False
        client.start_followup()
        answer = agent.ask(task.followup_prompt)
        verifier_passed = _verify(task, root, answer)
        if mode == "live":
            prompt_chars = sum(len(prompt) for prompt in client.prompts)
            actual_usage = bool(client.followup_usage) and all(
                item.get("input_tokens") is not None and item.get("output_tokens") is not None
                for item in client.followup_usage
            )
            input_tokens = sum(int(item.get("input_tokens", 0) or 0) for item in client.followup_usage)
            cached_tokens = sum(int(item.get("cached_tokens", 0) or 0) for item in client.followup_usage)
            output_tokens = sum(int(item.get("output_tokens", 0) or 0) for item in client.followup_usage)
            trace_path = agent.run_store.trace_path(agent.current_task_state)
            events = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            repeated_reads = sum(
                1
                for event in events
                if event.get("event") == "tool_executed" and event.get("name") == "read_file"
            )
            model_calls = len(client.followup_usage)
            usage_source = "actual" if actual_usage else "estimated_proxy"
            if not actual_usage:
                input_tokens = max(1, (prompt_chars + 3) // 4)
        else:
            prompt_chars = int(client.followup_prompt_chars)
            input_tokens = max(1, (prompt_chars + 3) // 4)
            cached_tokens = 0
            output_tokens = int(client.followup_model_calls) * 16
            repeated_reads = int(client.followup_reads)
            model_calls = int(client.followup_model_calls)
            usage_source = "estimated_proxy"
        final_prompt = client.prompts[-1] if client.prompts else ""
        return {
            "status": "completed",
            "task_id": task.id,
            "category": task.category,
            "variant": variant,
            "repeat": int(repeat),
            "verifier_passed": verifier_passed,
            "correct": verifier_passed,
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "usage_source": usage_source,
            "estimated_input_tokens": max(1, (prompt_chars + 3) // 4),
            "prompt_chars": prompt_chars,
            "model_calls": model_calls,
            "repeated_reads": repeated_reads,
            "tool_steps": int(agent.current_task_state.tool_steps),
            "attempts": int(agent.current_task_state.attempts),
            "current_request_preserved": f"Current user request:\n{task.followup_prompt}" in final_prompt,
        }


def _mean(rows, field):
    if not rows:
        return 0.0
    values = []
    for row in rows:
        if field == "input_tokens":
            value = row.get(field, row.get("estimated_input_tokens", 0))
        else:
            value = row.get(field, 0)
        values.append(float(value))
    return sum(values) / len(values)


def _rate(rows, field):
    return sum(1 for row in rows if row[field]) / len(rows) if rows else 0.0


def _variant_summary(rows):
    return {
        "run_count": len(rows),
        "verifier_pass_rate": _rate(rows, "verifier_passed"),
        "avg_input_tokens": _mean(rows, "input_tokens"),
        "avg_cached_tokens": _mean(rows, "cached_tokens"),
        "avg_output_tokens": _mean(rows, "output_tokens"),
        "avg_estimated_input_tokens": _mean(rows, "estimated_input_tokens"),
        "avg_tool_steps": _mean(rows, "tool_steps"),
        "avg_attempts": _mean(rows, "attempts"),
        "total_repeated_reads": sum(int(row["repeated_reads"]) for row in rows),
        "current_request_preserved_rate": _rate(rows, "current_request_preserved"),
    }


def _reduction(treatment, control):
    return (float(control) - float(treatment)) / float(control) if control else 0.0


def _comparison(treatment, control):
    return {
        "input_token_reduction": _reduction(
            treatment["avg_input_tokens"], control["avg_input_tokens"]
        ),
        "estimated_input_token_reduction": _reduction(
            treatment["avg_estimated_input_tokens"], control["avg_estimated_input_tokens"]
        ),
        "repeated_read_reduction": control["total_repeated_reads"] - treatment["total_repeated_reads"],
        "repeated_read_reduction_rate": _reduction(
            treatment["total_repeated_reads"], control["total_repeated_reads"]
        ),
        "avg_tool_step_reduction": control["avg_tool_steps"] - treatment["avg_tool_steps"],
        "avg_tool_step_reduction_rate": _reduction(
            treatment["avg_tool_steps"], control["avg_tool_steps"]
        ),
        "verifier_pass_rate_delta": treatment["verifier_pass_rate"] - control["verifier_pass_rate"],
    }


def enrich_benchmark_payload(payload):
    rows = list(payload.get("rows", []))
    variants = {
        variant: _variant_summary([row for row in rows if row["variant"] == variant])
        for variant in VARIANTS
    }
    category_variants = {
        category: {
            variant: _variant_summary(
                [row for row in rows if row["category"] == category and row["variant"] == variant]
            )
            for variant in VARIANTS
        }
        for category in ("code_lookup", "cross_file_analysis", "code_edit")
    }
    knowledge_rows = [row for row in rows if row["category"] != "code_edit"]
    knowledge_variants = {
        variant: _variant_summary([row for row in knowledge_rows if row["variant"] == variant])
        for variant in VARIANTS
    }
    payload["variants"] = variants
    payload["category_variants"] = category_variants
    payload["knowledge_followup_variants"] = knowledge_variants
    payload.setdefault("comparisons", {})["full_vs_no_context_reduction"] = _comparison(
        variants["full"], variants["no_context_reduction"]
    )
    payload["comparisons"]["full_vs_no_memory"] = _comparison(
        variants["full"], variants["no_memory"]
    )
    payload.setdefault("comparisons", {})["full_vs_no_memory_knowledge_followup"] = _comparison(
        knowledge_variants["full"], knowledge_variants["no_memory"]
    )
    return payload


def _run_row_safely(task, variant, repeat, kwargs, retries=1):
    last_error = None
    for _ in range(int(retries) + 1):
        try:
            return _run_row(task, variant, repeat, **kwargs)
        except Exception as exc:  # preserve the full matrix and allow targeted reruns
            last_error = exc
    payload = {
        "status": "error",
        "task_id": task.id,
        "category": task.category,
        "variant": variant,
        "repeat": int(repeat),
        "verifier_passed": False,
        "correct": False,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "usage_source": "invalid",
        "estimated_input_tokens": 0,
        "prompt_chars": 0,
        "model_calls": 0,
        "repeated_reads": 0,
        "tool_steps": 0,
        "attempts": 0,
        "current_request_preserved": False,
        "error": str(last_error)[:300],
    }
    return payload


def run_context_memory_paired_benchmark(
    repetitions=3,
    tasks=TASKS,
    *,
    mode="deterministic",
    model="mimo-v2.5-pro",
    base_url="https://api.xiaomimimo.com/v1",
    api_key="",
    workers=1,
):
    mode = str(mode)
    if mode not in {"deterministic", "live"}:
        raise ValueError(f"unsupported benchmark mode: {mode}")
    if mode == "live" and not api_key:
        raise ValueError("live mode requires an API key")
    jobs = [
        (task, variant, repeat)
        for repeat in range(int(repetitions))
        for task in tasks
        for variant in VARIANTS
    ]
    kwargs = {"mode": mode, "model": model, "base_url": base_url, "api_key": api_key}
    if int(workers) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(workers)) as executor:
            futures = [
                executor.submit(_run_row_safely, task, variant, repeat, kwargs)
                for task, variant, repeat in jobs
            ]
            rows = [future.result() for future in concurrent.futures.as_completed(futures)]
    else:
        rows = [_run_row_safely(task, variant, repeat, kwargs) for task, variant, repeat in jobs]
    rows.sort(key=lambda row: (row["repeat"], row["task_id"], row["variant"]))
    summaries = {
        variant: _variant_summary([row for row in rows if row["variant"] == variant])
        for variant in VARIANTS
    }
    full = summaries["full"]
    no_context = summaries["no_context_reduction"]
    no_memory = summaries["no_memory"]
    payload = {
        "schema_version": 1,
        "artifact_type": "context-memory-paired-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "provider_model": model if mode == "live" else "scripted",
        "usage_source": (
            "actual" if rows and all(row["usage_source"] == "actual" for row in rows) else "estimated_proxy"
        ),
        "task_count": len(tasks),
        "repetitions": int(repetitions),
        "variant_count": len(VARIANTS),
        "total_runs": len(rows),
        "error_count": sum(1 for row in rows if row["status"] == "error"),
        "category_counts": {
            category: sum(1 for task in tasks if task.category == category)
            for category in ("code_lookup", "cross_file_analysis", "code_edit")
        },
        "variants": summaries,
        "comparisons": {
            "full_vs_no_context_reduction": {
                "input_token_reduction": _reduction(
                    full["avg_input_tokens"], no_context["avg_input_tokens"]
                ),
                "estimated_input_token_reduction": _reduction(
                    full["avg_estimated_input_tokens"], no_context["avg_estimated_input_tokens"]
                ),
                "verifier_pass_rate_delta": full["verifier_pass_rate"] - no_context["verifier_pass_rate"],
            },
            "full_vs_no_memory": {
                "repeated_read_reduction": no_memory["total_repeated_reads"] - full["total_repeated_reads"],
                "avg_tool_step_reduction": no_memory["avg_tool_steps"] - full["avg_tool_steps"],
                "input_token_reduction": _reduction(
                    full["avg_input_tokens"], no_memory["avg_input_tokens"]
                ),
                "estimated_input_token_reduction": _reduction(
                    full["avg_estimated_input_tokens"], no_memory["avg_estimated_input_tokens"]
                ),
                "verifier_pass_rate_delta": full["verifier_pass_rate"] - no_memory["verifier_pass_rate"],
            },
        },
        "rows": rows,
    }
    return enrich_benchmark_payload(payload)


def render_report(payload):
    full = payload["variants"]["full"]
    no_context = payload["variants"]["no_context_reduction"]
    no_memory = payload["variants"]["no_memory"]
    context_cmp = payload["comparisons"]["full_vs_no_context_reduction"]
    memory_cmp = payload["comparisons"]["full_vs_no_memory"]
    knowledge = payload.get("knowledge_followup_variants", {})
    knowledge_full = knowledge.get("full", full)
    knowledge_no_memory = knowledge.get("no_memory", no_memory)
    knowledge_cmp = payload.get("comparisons", {}).get(
        "full_vs_no_memory_knowledge_followup", memory_cmp
    )
    return "\n".join(
        [
            "# Context and Memory Paired Benchmark",
            "",
            f"- Tasks: {payload['task_count']} (5 lookup / 5 analysis / 5 edit)",
            f"- Repetitions: {payload['repetitions']}",
            f"- Variants: {payload['variant_count']}",
            f"- Total runs: {payload['total_runs']}",
            f"- Mode: {payload.get('mode', 'deterministic')}",
            f"- Provider model: {payload.get('provider_model', 'scripted')}",
            f"- Usage source: {payload['usage_source']}",
            "",
            "## Context ablation",
            f"- Verifier pass rate: {full['verifier_pass_rate']:.2%} vs {no_context['verifier_pass_rate']:.2%}",
            f"- Avg input tokens: {full['avg_input_tokens']:.2f} vs {no_context['avg_input_tokens']:.2f}",
            f"- Input token reduction: {context_cmp['input_token_reduction']:.2%}",
            f"- Avg estimated input tokens: {full['avg_estimated_input_tokens']:.2f} vs {no_context['avg_estimated_input_tokens']:.2f}",
            f"- Estimated input token reduction: {context_cmp['estimated_input_token_reduction']:.2%}",
            "",
            "## Memory ablation (lookup + analysis follow-ups)",
            f"- Verifier pass rate: {knowledge_full['verifier_pass_rate']:.2%} vs {knowledge_no_memory['verifier_pass_rate']:.2%}",
            f"- Total repeated reads: {knowledge_full['total_repeated_reads']} vs {knowledge_no_memory['total_repeated_reads']}",
            f"- Repeated read reduction: {knowledge_cmp['repeated_read_reduction_rate']:.2%}",
            f"- Avg tool steps: {knowledge_full['avg_tool_steps']:.2f} vs {knowledge_no_memory['avg_tool_steps']:.2f}",
            f"- Avg tool step reduction: {knowledge_cmp['avg_tool_step_reduction_rate']:.2%}",
            f"- Avg input tokens: {knowledge_full['avg_input_tokens']:.2f} vs {knowledge_no_memory['avg_input_tokens']:.2f}",
            f"- Input token reduction: {knowledge_cmp['input_token_reduction']:.2%}",
            "",
            "## Edit-task safety boundary",
            f"- Full verifier pass rate: {payload.get('category_variants', {}).get('code_edit', {}).get('full', full)['verifier_pass_rate']:.2%}",
            f"- No-memory verifier pass rate: {payload.get('category_variants', {}).get('code_edit', {}).get('no_memory', no_memory)['verifier_pass_rate']:.2%}",
            "- Edit-task reads are retained as safety refreshes and excluded from the memory-efficiency headline.",
            "",
            "## Interpretation",
            "- Token figures are provider telemetry only when usage source is actual.",
            "- A benefit is resume-grade only when the paired verifier pass rate does not regress.",
        ]
    )


def write_artifacts(payload, artifact_path=DEFAULT_ARTIFACT_PATH, report_path=DEFAULT_REPORT_PATH):
    artifact_path = Path(artifact_path)
    report_path = Path(report_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_report(payload) + "\n", encoding="utf-8")
    return {"json": str(artifact_path), "markdown": str(report_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the 15-task paired context and memory benchmark.")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--mode", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--model", default="mimo-v2.5-pro")
    parser.add_argument("--base-url", default="https://api.xiaomimimo.com/v1")
    parser.add_argument("--api-key-env", default="MIMO_API_KEY")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-json", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--output-markdown", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args(argv)
    written = write_artifacts(
        run_context_memory_paired_benchmark(
            repetitions=args.repetitions,
            mode=args.mode,
            model=args.model,
            base_url=args.base_url,
            api_key=os.environ.get(args.api_key_env, ""),
            workers=args.workers,
        ),
        artifact_path=args.output_json,
        report_path=args.output_markdown,
    )
    print(json.dumps(written, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
