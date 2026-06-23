# MQTT / CoAP / SMTP Minimum Matrix 复现说明

## 目标

Step 6 的目标是用同一入口复现 MQTT、CoAP、SMTP 三个协议的 minimum planning -> coder -> compile -> smoke 链路，并把 failure taxonomy 和 artifacts 固定归档。

本轮不压缩 strict specs，不关闭 validator，不为单协议加入协议外硬编码。

## 输入边界

protocol facts 只使用 `agent/facts/gold_facts` 中当前已有文件：

| protocol | facts_path | target_profile |
| --- | --- | --- |
| MQTT | `agent/facts/gold_facts/mqtt_min/protocol_facts.json` | `agent/planning/planning_target_profile_mqtt.json` |
| CoAP | `agent/facts/gold_facts/coap_min/protocol_facts.json` | `agent/planning/planning_target_profile_coap.json` |
| SMTP | `agent/facts/gold_facts/smtp_min/protocol_facts.json` | `agent/planning/planning_target_profile_smtp_min.json` |

SMTP 当前 facts 是现有 `smtp_min` fixture，包含 `AUTH LOGIN/PLAIN`，并记录 `MAIL/RCPT/DATA` 的 authentication 前置条件。Step 6 runner 按 facts 执行，不把 no-AUTH SMTP 行为伪装成 protocol fact。

## 统一入口

完整执行：

```bash
python3 tools/eval/run_minimum_matrix.py \
  --protocol mqtt \
  --protocol coap \
  --protocol smtp \
  --output-root agent/eval_out/minimum_matrix
```

复用已有 planning run 只验证归档和后续阶段：

```bash
python3 tools/eval/run_minimum_matrix.py \
  --skip-planning \
  --planning-dir mqtt=agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260618_171930_310767 \
  --protocol mqtt \
  --output-root agent/eval_out/minimum_matrix
```

## 每协议执行步骤

每个协议按相同顺序执行：

1. 检查 facts 和 target profile 是否存在。
2. `python3 -m agent.planning validate`。
3. `python3 -m agent.planning plan`，或在 `--skip-planning` 下复制已有 planning run。
4. `python3 -m agent.planning verify`。
5. 检查 `spec_bundle/`。
6. `python3 -m agent coder --spec-root <spec_bundle> validate`。
7. `python3 -m agent coder --spec-root <spec_bundle> --output-dir <coder_out> generate`。
8. 从 `coder_out/_agent_logs/run_manifest.json` 提取 compile 和 repair iterations。
9. compile 通过且 binary 存在时运行 `python3 -m agent coder test`。
10. 写入 `<matrix>/<protocol>/summary.json` 和顶层 `matrix_summary.json` / `matrix_summary.md`。

## Artifact Layout

默认输出：

```text
agent/eval_out/minimum_matrix/<timestamp>/
  matrix_summary.json
  matrix_summary.md
  mqtt/
    planning_validate/
    planning_run/
    coder_out/
    logs/
    summary.json
  coap/
    ...
  smtp/
    ...
```

每个 protocol summary 包含：

```text
protocol
facts_path
target_profile_path
planning_run_dir
spec_bundle_path
planning_status
readiness_status
schema_loader_rendered_header_status
coder_output_dir
compile_status
repair_iterations
smoke_status
failure_stage
failure_categories
main_diagnostic
artifact_archived
protocol_facts
inferred_engineering_decisions
open_assumptions
```

## Failure Taxonomy

runner 会把失败归入以下类别之一或多个：

| category | 含义 |
| --- | --- |
| `input_blocker` | facts、target profile 或复用 planning run 缺失 |
| `planning_stage_failure` | planning validate/plan/verify 失败 |
| `specs_compiler_lowering_failure` | planning 通过但 `spec_bundle/` 缺失 |
| `coder_loader_failure` | coder spec loader validation 失败 |
| `coder_loader_header_failure` | coder loader/header/rendered header/dummy TU 类失败 |
| `source_compile_failure` | coder generate 后 source compile 失败 |
| `smoke_behavior_failure` | binary 存在但 minimum smoke 失败 |
| `llm_json_error` | LLM 输出 JSON 解析类失败 |
| `llm_candidate_semantic_error` | LLM candidate 语义绑定类失败 |

## Step 6 判定

Step 6 不要求所有协议本轮立即 pass，但必须满足：

1. 每个协议得到 planning/readiness/loader/compile/smoke 状态记录，或输入 blocker 被结构化记录。
2. readiness passed 的 bundle 不在 coder loader/header 阶段失败。
3. 不存在 false planning success。
4. 不存在 silent dependency fallback。
5. 不存在 coder 阶段才暴露的 deterministic header closure failure。
6. `docs/CURRENT_TASK_STATUS.md` 记录最新结果和下一轮 Step 7 入口。
