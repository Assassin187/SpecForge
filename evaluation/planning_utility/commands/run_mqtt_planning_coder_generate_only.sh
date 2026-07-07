#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
OUTPUT_ROOT="${REPO_ROOT}/evaluation/planning_utility/out"
RUN_ROOT="${OUTPUT_ROOT}/mqtt_planning_coder_generate_only_$(date +%Y%m%d_%H%M%S)"
FACTS="${REPO_ROOT}/agent/facts/gold_facts/mqtt_min/protocol_facts.json"
TARGET_PROFILE="${REPO_ROOT}/agent/planning/planning_target_profile_mqtt.json"
PLANNING_RUN="${RUN_ROOT}/planning_run"
CODER_OUT="${RUN_ROOT}/coder_out"
API_KEY_ENV="${API_KEY_ENV:-ALI_API}"
VALIDATE_TMP="$(mktemp -d)"
trap 'rm -rf "${VALIDATE_TMP}"' EXIT

mkdir -p "${RUN_ROOT}"
cd "${REPO_ROOT}"

python3 -m agent planning validate \
  --facts "${FACTS}" \
  --target-profile "${TARGET_PROFILE}" \
  --output-dir "${VALIDATE_TMP}/planning_validate"

python3 -m agent planning plan \
  --facts "${FACTS}" \
  --target-profile "${TARGET_PROFILE}" \
  --output-dir "${PLANNING_RUN}" \
  --api-key-env "${API_KEY_ENV}"

python3 -m agent planning verify \
  --output-dir "${PLANNING_RUN}"

python3 -m agent coder \
  --spec-root "${PLANNING_RUN}/spec_bundle" \
  --api-key-env "${API_KEY_ENV}" \
  validate

python3 -m agent coder \
  --spec-root "${PLANNING_RUN}/spec_bundle" \
  --output-dir "${CODER_OUT}" \
  --api-key-env "${API_KEY_ENV}" \
  --skip-repair \
  generate

echo "Planning output: ${PLANNING_RUN}"
echo "Coder output: ${CODER_OUT}"
