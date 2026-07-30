from pico.evaluation.context_memory_benchmark import (
    TASKS,
    render_report,
    run_context_memory_paired_benchmark,
    write_artifacts,
)


def test_task_matrix_has_five_tasks_per_category():
    assert len(TASKS) == 15
    assert {category: sum(1 for task in TASKS if task.category == category) for category in {
        "code_lookup", "cross_file_analysis", "code_edit"
    }} == {"code_lookup": 5, "cross_file_analysis": 5, "code_edit": 5}


def test_paired_benchmark_runs_all_variants_and_preserves_quality():
    payload = run_context_memory_paired_benchmark(repetitions=1)

    assert payload["total_runs"] == 45
    assert set(payload["variants"]) == {"full", "no_context_reduction", "no_memory"}
    assert all(summary["verifier_pass_rate"] == 1.0 for summary in payload["variants"].values())
    assert payload["variants"]["full"]["avg_estimated_input_tokens"] < payload["variants"]["no_context_reduction"]["avg_estimated_input_tokens"]
    assert payload["variants"]["full"]["total_repeated_reads"] < payload["variants"]["no_memory"]["total_repeated_reads"]
    assert payload["comparisons"]["full_vs_no_context_reduction"]["verifier_pass_rate_delta"] == 0
    assert payload["comparisons"]["full_vs_no_memory"]["verifier_pass_rate_delta"] == 0


def test_benchmark_artifacts_make_proxy_boundary_explicit(tmp_path):
    payload = run_context_memory_paired_benchmark(repetitions=1, tasks=TASKS[:1])
    json_path = tmp_path / "results.json"
    markdown_path = tmp_path / "report.md"

    written = write_artifacts(payload, json_path, markdown_path)
    report = render_report(payload)

    assert written == {"json": str(json_path), "markdown": str(markdown_path)}
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert "estimated_proxy" in json_path.read_text(encoding="utf-8")
    assert "provider telemetry only when usage source is actual" in report
