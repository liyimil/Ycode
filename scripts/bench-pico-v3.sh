#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

workspace=""
prompt_file=""
session_id=""
max_steps="${PICO_BENCH_MAX_STEPS:-16}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace="$2"
      shift 2
      ;;
    --prompt-file)
      prompt_file="$2"
      shift 2
      ;;
    --session-id)
      session_id="$2"
      shift 2
      ;;
    --task-id|--model-id)
      shift 2
      ;;
    --max-steps)
      max_steps="$2"
      shift 2
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -n "${PICO_BENCH_MAX_STEPS:-}" ]]; then
  max_steps="$PICO_BENCH_MAX_STEPS"
fi

if [[ -z "$workspace" || -z "$prompt_file" || -z "$session_id" ]]; then
  echo "missing --workspace, --prompt-file, or --session-id" >&2
  exit 2
fi

sandbox="${CLAWBENCH_SANDBOX:-$(dirname "$prompt_file")}"
effective_prompt_file="$prompt_file"
if [[ "${PICO_BENCH_ARTIFACT_GUARDRAILS:-1}" != "0" ]]; then
  effective_prompt_file="$sandbox/pico-benchmark-prompt.txt"
  {
    cat "$prompt_file"
    printf '\n\n'
    cat <<'EOF'
---
Benchmark artifact discipline:
- Match required artifact paths, filenames, headers, and exact file contents literally.
- If an artifact asks for a bare filename such as `ticket_102.txt`, write only the bare filename, not a directory-prefixed path.
- Before returning the final answer, read back required artifacts and verify exact formatting.
EOF
  } > "$effective_prompt_file"
fi

if [[ -n "${PICO_BENCH_ENV:-}" ]]; then
  if [[ ! -f "$PICO_BENCH_ENV" ]]; then
    echo "PICO_BENCH_ENV does not exist: $PICO_BENCH_ENV" >&2
    exit 2
  fi
  # shellcheck source=/dev/null
  set -a
  source "$PICO_BENCH_ENV"
  set +a
fi

copy_env_if_missing() {
  local target="$1"
  local source="$2"
  if [[ -z "${!target:-}" && -n "${!source:-}" ]]; then
    export "$target=${!source}"
  fi
}

copy_env_if_missing DEEPSEEK_API_KEY PICO_DEEPSEEK_API_KEY
copy_env_if_missing DEEPSEEK_BASE_URL PICO_DEEPSEEK_API_BASE
copy_env_if_missing DEEPSEEK_MODEL PICO_DEEPSEEK_MODEL
copy_env_if_missing OPENAI_API_KEY PICO_OPENAI_API_KEY
copy_env_if_missing OPENAI_BASE_URL PICO_OPENAI_API_BASE
copy_env_if_missing OPENAI_MODEL PICO_OPENAI_MODEL
copy_env_if_missing ANTHROPIC_API_KEY PICO_ANTHROPIC_API_KEY
copy_env_if_missing ANTHROPIC_BASE_URL PICO_ANTHROPIC_API_BASE
copy_env_if_missing ANTHROPIC_MODEL PICO_ANTHROPIC_MODEL

provider="${PICO_BENCH_PROVIDER:-deepseek}"
final_readiness="${PICO_BENCH_FINAL_READINESS:-warn}"
cmd=(
  uv run pico
  --cwd "$workspace"
  --repo-root "$workspace"
  --prompt-file "$effective_prompt_file"
  --session-id "$session_id"
  --provider "$provider"
  --approval auto
  --non-interactive
  --final-readiness "$final_readiness"
  --no-auto-dream
  --max-steps "$max_steps"
)

if [[ -n "${PICO_BENCH_MODEL:-}" ]]; then
  cmd+=(--model "$PICO_BENCH_MODEL")
fi

if [[ -n "${PICO_BENCH_CONFIG:-}" ]]; then
  cmd+=(--config "$PICO_BENCH_CONFIG")
fi

set +e
(
  cd "$repo_root"
  "${cmd[@]}"
)
status=$?
set -e

metadata_path="$sandbox/pico-adapter-metadata.json"
(
  cd "$repo_root"
  uv run python -m pico.evaluation.harnessbench \
    --workspace "$workspace" \
    --session-id "$session_id" \
    --returncode "$status" \
    --output "$metadata_path"
)

exit "$status"
