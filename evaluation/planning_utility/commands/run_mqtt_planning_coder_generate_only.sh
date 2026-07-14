#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
OUTPUT_ROOT="${REPO_ROOT}/evaluation/planning_utility/out"
RUN_ROOT="${OUTPUT_ROOT}/mqtt_planning_coder_generate_only_$(date +%Y%m%d_%H%M%S)"
FACTS="${REPO_ROOT}/agent/facts/gold_facts/mqtt_min/protocol_facts.json"
PLANNING_RUN="${RUN_ROOT}/planning_run"
CODER_OUT="${RUN_ROOT}/coder_out"
API_KEY_ENV="${API_KEY_ENV:-ALI_API}"

mkdir -p "${RUN_ROOT}"
cd "${REPO_ROOT}"

python3 -m agent.planning plan \
  --facts "${FACTS}" \
  --out "${PLANNING_RUN}" \
  --api-key-env "${API_KEY_ENV}"

python3 -m agent.planning validate --run-dir "${PLANNING_RUN}"

MANIFEST="${PLANNING_RUN}/_planning/run_manifest.json"
RUN_STATUS="$(jq -r '.run_status' "${MANIFEST}")"
QUALIFIED="$(jq -r '.qualification_passed' "${MANIFEST}")"
SPEC_ROOT="$(jq -r '.specs_root // empty' "${MANIFEST}")"
if [[ -z "${SPEC_ROOT}" || ! -d "${SPEC_ROOT}" ]]; then
  echo "Planning did not materialize a usable specs_root" >&2
  exit 1
fi

python3 -m agent.coder \
  --spec-root "${SPEC_ROOT}" \
  --output-dir "${RUN_ROOT}/coder_validate" \
  --api-key-env "${API_KEY_ENV}" \
  validate

python3 -m agent.coder \
  --spec-root "${SPEC_ROOT}" \
  --output-dir "${CODER_OUT}" \
  --api-key-env "${API_KEY_ENV}" \
  --skip-repair \
  generate

echo "Planning output: ${PLANNING_RUN}"
echo "Planning status: ${RUN_STATUS}; qualification_passed=${QUALIFIED}; specs_root=${SPEC_ROOT}"
echo "Coder output: ${CODER_OUT}"
echo "Generation-only completed; initial compile was not measured."
