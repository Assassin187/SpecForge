#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
SPEC_ROOT="${REPO_ROOT}/specs-example/mqtt_specs"
OUTPUT_ROOT="${SCRIPT_DIR}/out"
RUNS=10
START_ROUND=1
MAX_REPAIR_ROUNDS="${MAX_REPAIR_ROUNDS:-3}"
OVERWRITE=0
STOP_ON_FAILURE=0
MODELS=all

# 可通过同名环境变量固定具体模型快照，避免把服务端默认版本带入实验。
GPT_MODEL="${GPT_MODEL:-gpt-5.4}"
GPT_API_KEY_ENV="${GPT_API_KEY_ENV:-GPT_API}"
# GPT_BASE_URL="${GPT_BASE_URL:-https://api.openai.com/v1}"
GPT_BASE_URL="${GPT_BASE_URL:-https://api.nuwaapi.com/v1/chat/completions}"

DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-pro}"
DEEPSEEK_API_KEY_ENV="${DEEPSEEK_API_KEY_ENV:-DS_API}"
DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com/v1}"

usage() {
  cat <<'USAGE'
Usage: ./run_gold_mqtt_model_robustness.sh [options]

Generate and repair the full-spec (gold MQTT) implementation independently for
GPT and DeepSeek. Each model writes fixed run directories under:
  evaluation/spec_ablation/out/gpt_mqtt/run_01 ... run_10
  evaluation/spec_ablation/out/deepseek_mqtt/run_01 ... run_10

Options:
  --runs N                 Runs per model. Default: 10
  --start-round N          First run number. Default: 1
  --max-repair-rounds N    Maximum repair rounds per run. Default: 3
  --models LIST            Models to run: all, deepseek, or gpt. Default: all
  --overwrite              Permit replacing an existing target run directory.
  --stop-on-failure        Stop after the first failed run.
  -h, --help               Show this help.

Configuration is supplied through environment variables:
  GPT_MODEL, GPT_API_KEY_ENV, GPT_BASE_URL
  DEEPSEEK_MODEL, DEEPSEEK_API_KEY_ENV, DEEPSEEK_BASE_URL
USAGE
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs) RUNS="${2:?missing value for --runs}"; shift 2 ;;
    --start-round) START_ROUND="${2:?missing value for --start-round}"; shift 2 ;;
    --max-repair-rounds) MAX_REPAIR_ROUNDS="${2:?missing value for --max-repair-rounds}"; shift 2 ;;
    --models) MODELS="${2:?missing value for --models}"; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    --stop-on-failure) STOP_ON_FAILURE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "${RUNS}" =~ ^[0-9]+$ && "${RUNS}" -ge 1 ]] || die "--runs must be a positive integer"
[[ "${START_ROUND}" =~ ^[0-9]+$ && "${START_ROUND}" -ge 1 ]] || die "--start-round must be a positive integer"
[[ "${MAX_REPAIR_ROUNDS}" =~ ^[0-9]+$ ]] || die "--max-repair-rounds must be a non-negative integer"
[[ "${MODELS}" == "all" || "${MODELS}" == "deepseek" || "${MODELS}" == "gpt" ]] || die "--models must be all, deepseek, or gpt"
[[ -d "${SPEC_ROOT}" ]] || die "gold MQTT specs not found: ${SPEC_ROOT}"

run_model() {
  local label="$1"
  local model="$2"
  local api_key_env="$3"
  local base_url="$4"
  local run_dir
  local round

  [[ -n "${!api_key_env:-}" ]] || die "${api_key_env} is not set for ${label}"
  base_url="${base_url%/}"
  base_url="${base_url%/chat/completions}"
  for ((round = START_ROUND; round < START_ROUND + RUNS; round++)); do
    run_dir="${OUTPUT_ROOT}/${label}_mqtt/run_$(printf '%02d' "${round}")"
    if [[ -e "${run_dir}" && "${OVERWRITE}" -ne 1 ]]; then
      die "target already exists: ${run_dir} (use --overwrite to replace it)"
    fi
    echo "===== ${label} MQTT run ${round} start: model=${model} ====="
    if ! python3 -m agent coder \
      --spec-root "${SPEC_ROOT}" \
      --output-dir "${run_dir}" \
      --api-key-env "${api_key_env}" \
      --model "${model}" \
      --base-url "${base_url}" \
      --max-repair-rounds "${MAX_REPAIR_ROUNDS}" \
      generate; then
      echo "===== ${label} MQTT run ${round} failed =====" >&2
      failures=$((failures + 1))
      [[ "${STOP_ON_FAILURE}" -eq 0 ]] || return 1
      continue
    fi
    echo "===== ${label} MQTT run ${round} done ====="
  done
}

mkdir -p "${OUTPUT_ROOT}"
cd "${REPO_ROOT}"
failures=0
if [[ "${MODELS}" == "all" || "${MODELS}" == "deepseek" ]]; then
  run_model deepseek "${DEEPSEEK_MODEL}" "${DEEPSEEK_API_KEY_ENV}" "${DEEPSEEK_BASE_URL}"
fi
if [[ "${MODELS}" == "all" || "${MODELS}" == "gpt" ]]; then
  run_model gpt "${GPT_MODEL}" "${GPT_API_KEY_ENV}" "${GPT_BASE_URL}"
fi

if [[ "${failures}" -gt 0 ]]; then
  echo "Completed with ${failures} failed run(s)." >&2
  exit 1
fi
