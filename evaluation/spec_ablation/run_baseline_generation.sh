#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PROTOCOL="mqtt"
VIEW=""
START_ROUND=1
ROUNDS=1
SPEC_VIEW_ROOT="${SCRIPT_DIR}/specs"
FULL_SPEC_ROOT="${REPO_ROOT}/specs-example"
OUTPUT_ROOT="${SCRIPT_DIR}/out"
API_KEY_ENV="${API_KEY_ENV:-ALI_API}"
MAX_REPAIR_ROUNDS=""
SKIP_REPAIR=0
VALIDATE_FIRST=1
STOP_ON_FAILURE=0

usage() {
  cat <<'USAGE'
Usage:
  run_baseline_generation.sh --view s1|s2|s3|s4|all [options]

Examples:
  ./run_baseline_generation.sh --protocol mqtt --view s3 --start-round 1 --rounds 5
  ./run_baseline_generation.sh --protocol mqtt --view s4 --rounds 1
  ./run_baseline_generation.sh --protocol mqtt --view all --rounds 3 --skip-repair

Options:
  --protocol NAME          Protocol key: mqtt/http/coap/smtp. Default: mqtt
  --view VIEW              Baseline view: s1/s2/s3/s4/all. Required.
  --start-round N          First round number. Default: 1
  --rounds N               Number of rounds to run. Default: 1
  --spec-view-root PATH    Root containing transformed S1-S3 specs. Default: evaluation/spec_ablation/specs
  --full-spec-root PATH    Root containing S4 full specs. Default: specs-example
  --output-root PATH       Generation output root. Default: evaluation/spec_ablation/out
  --api-key-env NAME       API key environment variable. Default: ALI_API
  --max-repair-rounds N    Override coder repair round limit.
  --skip-repair            Stop after source generation.
  --no-validate            Skip pre-generation validation.
  --stop-on-failure        Stop when one generation run fails.
  -h, --help               Show this help.

Notes:
  S1-S3 assume transformed view specs already exist under:
    <spec-view-root>/<protocol>/<s1|s2|s3>

  S4 assumes full SpecForge specs already exist under:
    <full-spec-root>/<protocol>_specs
USAGE
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

abs_path() {
  local value="$1"
  if [[ "${value}" = /* ]]; then
    printf '%s\n' "${value}"
  else
    printf '%s\n' "${REPO_ROOT}/${value}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --protocol)
      PROTOCOL="${2:?missing value for --protocol}"
      shift 2
      ;;
    --view)
      VIEW="${2:?missing value for --view}"
      shift 2
      ;;
    --start-round)
      START_ROUND="${2:?missing value for --start-round}"
      shift 2
      ;;
    --rounds)
      ROUNDS="${2:?missing value for --rounds}"
      shift 2
      ;;
    --spec-view-root)
      SPEC_VIEW_ROOT="$(abs_path "${2:?missing value for --spec-view-root}")"
      shift 2
      ;;
    --full-spec-root)
      FULL_SPEC_ROOT="$(abs_path "${2:?missing value for --full-spec-root}")"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$(abs_path "${2:?missing value for --output-root}")"
      shift 2
      ;;
    --api-key-env)
      API_KEY_ENV="${2:?missing value for --api-key-env}"
      shift 2
      ;;
    --max-repair-rounds)
      MAX_REPAIR_ROUNDS="${2:?missing value for --max-repair-rounds}"
      shift 2
      ;;
    --skip-repair)
      SKIP_REPAIR=1
      shift
      ;;
    --no-validate)
      VALIDATE_FIRST=0
      shift
      ;;
    --stop-on-failure)
      STOP_ON_FAILURE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "${VIEW}" ]] || die "--view is required"
[[ "${START_ROUND}" =~ ^[0-9]+$ ]] || die "--start-round must be an integer"
[[ "${ROUNDS}" =~ ^[0-9]+$ ]] || die "--rounds must be an integer"
[[ "${ROUNDS}" -ge 1 ]] || die "--rounds must be >= 1"

case "${PROTOCOL}" in
  mqtt|http|coap|smtp) ;;
  *) die "unsupported protocol '${PROTOCOL}'" ;;
esac

case "${VIEW}" in
  s1|s2|s3|s4) VIEWS=("${VIEW}") ;;
  all) VIEWS=(s1 s2 s3 s4) ;;
  *) die "unsupported view '${VIEW}'" ;;
esac

mkdir -p "${OUTPUT_ROOT}"
cd "${REPO_ROOT}"

view_generator_args() {
  local view="$1"
  local round="$2"
  local view_root="${SPEC_VIEW_ROOT}/${PROTOCOL}"
  [[ -d "${view_root}/${view}" ]] || die "missing ${view^^} transformed specs: ${view_root}/${view}"

  VIEW_GENERATOR_CMD=(
    python3 -m evaluation.spec_ablation.view_generator
    --view "${view}"
    --view-root "${view_root}"
    --output-dir "${OUTPUT_ROOT}/$(date +%Y%m%d_%H%M%S)_${PROTOCOL}_${view^^}_round_${round}"
    --round-number "${round}"
    --api-key-env "${API_KEY_ENV}"
  )
  if [[ -n "${MAX_REPAIR_ROUNDS}" ]]; then
    VIEW_GENERATOR_CMD+=(--max-repair-rounds "${MAX_REPAIR_ROUNDS}")
  fi
  if [[ "${SKIP_REPAIR}" -eq 1 ]]; then
    VIEW_GENERATOR_CMD+=(--skip-repair)
  fi
}

s4_coder_args() {
  local round="$1"
  local spec_root="${FULL_SPEC_ROOT}/${PROTOCOL}_specs"
  local output_dir="${OUTPUT_ROOT}/$(date +%Y%m%d_%H%M%S)_${PROTOCOL}_S4_round_${round}"
  [[ -d "${spec_root}" ]] || die "missing S4 full specs: ${spec_root}"

  S4_CODER_CMD=(
    python3 -m agent coder
    --spec-root "${spec_root}"
    --output-dir "${output_dir}"
    --api-key-env "${API_KEY_ENV}"
  )
  if [[ -n "${MAX_REPAIR_ROUNDS}" ]]; then
    S4_CODER_CMD+=(--max-repair-rounds "${MAX_REPAIR_ROUNDS}")
  fi
  if [[ "${SKIP_REPAIR}" -eq 1 ]]; then
    S4_CODER_CMD+=(--skip-repair)
  fi
}

run_view_generator() {
  local view="$1"
  local round="$2"
  local view_root="${SPEC_VIEW_ROOT}/${PROTOCOL}"
  if [[ "${VALIDATE_FIRST}" -eq 1 ]]; then
    python3 -m evaluation.spec_ablation.view_generator \
      --view "${view}" \
      --view-root "${view_root}" \
      --api-key-env "${API_KEY_ENV}" \
      validate || return $?
  fi
  view_generator_args "${view}" "${round}"
  "${VIEW_GENERATOR_CMD[@]}" generate || return $?
}

run_s4() {
  local round="$1"
  local spec_root="${FULL_SPEC_ROOT}/${PROTOCOL}_specs"
  if [[ "${VALIDATE_FIRST}" -eq 1 ]]; then
    python3 -m agent coder \
      --spec-root "${spec_root}" \
      --api-key-env "${API_KEY_ENV}" \
      validate || return $?
  fi
  s4_coder_args "${round}"
  "${S4_CODER_CMD[@]}" generate || return $?
}

FAILURES=0
for view in "${VIEWS[@]}"; do
  for ((offset = 0; offset < ROUNDS; offset++)); do
    round=$((START_ROUND + offset))
    echo "===== ${PROTOCOL} ${view^^} round ${round} start ====="
    if [[ "${view}" == "s4" ]]; then
      if ! run_s4 "${round}"; then
        echo "===== ${PROTOCOL} ${view^^} round ${round} failed =====" >&2
        FAILURES=$((FAILURES + 1))
        [[ "${STOP_ON_FAILURE}" -eq 0 ]] || exit 1
        continue
      fi
    else
      if ! run_view_generator "${view}" "${round}"; then
        echo "===== ${PROTOCOL} ${view^^} round ${round} failed =====" >&2
        FAILURES=$((FAILURES + 1))
        [[ "${STOP_ON_FAILURE}" -eq 0 ]] || exit 1
        continue
      fi
    fi
    echo "===== ${PROTOCOL} ${view^^} round ${round} done ====="
  done
done

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "Completed with ${FAILURES} failed generation run(s)." >&2
  exit 1
fi
