"""Architecture budget tests for runtime module boundaries."""

from pathlib import Path


def test_core_modules_stay_below_entropy_budget():
    root = Path(__file__).resolve().parents[1]
    budgets = {
        "pico/core/runtime.py": 950,
        "pico/core/before_final_hooks.py": 140,
        "pico/core/evidence_summaries.py": 90,
        "pico/core/final_readiness.py": 120,
        "pico/core/final_readiness_artifacts.py": 160,
        "pico/core/final_readiness_context.py": 60,
        "pico/core/final_readiness_reasons.py": 60,
        "pico/core/final_readiness_tools.py": 100,
        "pico/core/governance.py": 80,
        "pico/core/runtime_events.py": 90,
        "pico/core/runtime_consumers.py": 90,
        "pico/core/artifacts.py": 130,
        "pico/core/task_state.py": 140,
        "pico/core/todo_ledger.py": 120,
        "pico/core/worker_manager.py": 220,
        "pico/core/context_manager.py": 420,
        "pico/core/context_budget_summary.py": 130,
        "pico/core/context_handoff.py": 240,
        "pico/core/context_orchestrator.py": 210,
        "pico/core/context_pressure.py": 140,
        "pico/core/context_report.py": 140,
        "pico/core/context_retention.py": 90,
        "pico/core/context_replacements.py": 160,
        "pico/core/context_sections.py": 170,
        "pico/core/context_usage.py": 130,
        "pico/core/compact.py": 250,
        "pico/core/compact_summary.py": 130,
        "pico/core/completion_governance.py": 240,
        "pico/core/engine.py": 470,
        "pico/core/model_errors.py": 100,
        "pico/core/model_router.py": 40,
        "pico/core/permissions.py": 140,
        "pico/core/tool_policy.py": 90,
        "pico/core/plan_mode.py": 140,
        "pico/core/tool_executor.py": 181,
        "pico/core/tool_profiles.py": 80,
        "pico/core/tool_result_artifacts.py": 60,
        "pico/core/turn_transitions.py": 90,
        "pico/core/verification.py": 80,
        "pico/core/turn_history.py": 280,
        "pico/core/media_history.py": 20,
        "pico/features/skills.py": 220,
        "pico/features/skills_bundled.py": 120,
        "pico/features/skills_runtime.py": 140,
        "pico/tools/registry.py": 360,
        "pico/tools/todos.py": 80,
        "pico/tools/agents.py": 90,
    }

    for relative_path, max_lines in budgets.items():
        line_count = len((root / relative_path).read_text(encoding="utf-8").splitlines())
        assert line_count <= max_lines, f"{relative_path} has {line_count} lines, budget is {max_lines}"
