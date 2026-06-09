# Planning 流程稳定性改造必要性评估

## 结论摘要

本次综合判断为：`LOGGING_ONLY`，并建议保留或补充少量 regression tests。当前不建议进入 `FAIL_SOFT_DESIGN_NEEDED`，更不建议 `STRUCTURAL_REFACTOR_NEEDED`。

最近两次完整 planning run 都已经走到 Step 6 / specs compile，final validation 无 blocking diagnostics，`coder_schema_status=passed`，`coder_loader_status=passed`，`coder ready=true`。这说明当前 pipeline 在最终产物可用性上已经明显优于 2026-06-03 到 2026-06-04 的历史失败阶段。

但这两次 run 不是“零噪声成功”。stage-local validation 仍有 warning，LLM candidate 仍有 rejection，部分 stage 仍依赖 fallback / repair / normalizer，且第二次最近 run 仍出现 `invalid_llm_json` 后 retry 成功。因此不能直接判定为 `NO_ACTION_NEEDED`。更准确的状态是：最终稳定性足够进入观察期，但过程稳定性仍值得通过日志和回归测试持续监控。

本报告只评估稳定性和后续动作必要性，不建议新增 Stage Recovery Contract、新 recovery framework、新 LLM repair loop，不建议放宽 `spec_bundle` strict schema，不建议改变 Step 6 specs compiler 边界，也不建议让 LLM 生成 final dependency graph。

## 最近两次成功 Run 摘要

| run | status | final status | coder ready | blocking diagnostics | warnings | retries | fallback/repair used | high-risk stage | evidence path |
|---|---|---|---|---|---:|---|---|---|---|
| `20260609_214554_036173` | success | success | true | none | 116 stage warnings | 4 rejections, 0 invalid JSON | fallback 6, repair 2/2 | 5.2b, 5.4b, 5.4e, 5.5b | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173/_validation_reports/014_planning_validation_report.json` |
| `20260609_205409_170615` | success | success | true | none | 94 stage warnings | 4 rejections, 2 invalid JSON | fallback 6, repair 1/1 | 5.4c, 5.4e, 5.5b | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_205409_170615/_validation_reports/014_planning_validation_report.json` |

两次 run 的 final report 均显示：

- `status=success`；
- `summary.error_count=0`；
- `summary.warning_count=0`；
- `coder_compatibility_status=passed`；
- `coder_schema_status=passed`；
- `coder_loader_status=passed`；
- `diagnostics=[]`；
- `coder_manifest.json` 和 `spec_bundle/` 均存在。

最近 run 的隐藏信号：

- `20260609_214554_036173` 在 5.2b 出现 `codec_module_missing_decoder_encoder_artifacts`，在 5.4b 出现 `public_signature_uses_private_type` / `unknown_or_unexported_signature_type`，在 5.5b 出现 runtime entrypoint lifecycle candidate 不合规，最终均由 retry / repair / fallback 兜住。
- `20260609_205409_170615` 在 5.2b 出现两次 `invalid_llm_json`，之后 retry 成功；5.4c 出现 `unknown_service_requirement_capability`；5.5b 同样使用 deterministic fallback。
- 两次 run 的 5.4e 都有 `missing_call_updates_filled`、`invalid_call_edges_dropped` 或 `aggregate_cycle_edges_removed`，说明 `calls_allowed` 仍是高波动 stage。
- 两次 run 都有 optional item 被 rejected 或 accepted：最近一次 5.3/5.4a 合计 accepted optional 1、rejected optional 3；前一次 5.4a accepted optional 2、rejected optional 1。

Token 消耗热点：

| run | total tokens | top token stages/prompts |
|---|---:|---|
| `20260609_214554_036173` | 703258 | `function_behavior_contract_patch_prompt` 143814, `function_signature_patch_prompt` 129518, `function_annotation_candidate_prompt` 106729 |
| `20260609_205409_170615` | 634876 | `function_behavior_contract_patch_prompt` 138851, `function_signature_patch_prompt` 88826, `function_annotation_candidate_prompt` 88537 |

主要 warning 类型：

| run | warning count | top warning codes |
|---|---:|---|
| `20260609_214554_036173` | 116 | `signature_raw_name_mismatch` 80, `behavior_missing_invariants` 12, `type_inventory_missing_trace_refs` 10 |
| `20260609_205409_170615` | 94 | `signature_raw_name_mismatch` 72, `type_inventory_missing_trace_refs` 10, `missing_function_family` 4 |

这些 warning 没有阻断 final readiness，但它们是质量波动信号，尤其是 signature raw name normalization 和 behavior invariants 缺失。

## 历史运行情况摘要

近 15 条可获得 run 中，历史失败主要集中在 2026-06-03 到 2026-06-04，后续 2026-06-09 的两条完整 run 均成功。历史失败不是单一原因，而是覆盖了 type inventory、wire/access binding、private state unresolved question、coder lowering/public symbol、runtime entrypoint 和 calls_allowed 等多个 stage。

| run | status | final status | coder ready | blocking diagnostics | warnings | retries | fallback/repair used | high-risk stage | evidence path |
|---|---|---|---|---|---:|---|---|---|---|
| `20260609_214554_036173` | success | success | true | none | 116 | 4 rejections | fallback 6, repair 2/2 | 5.2b, 5.4b, 5.4e, 5.5b | `_validation_reports/014_planning_validation_report.json` |
| `20260609_205409_170615` | success | success | true | none | 94 | 4 rejections, 2 invalid JSON | fallback 6, repair 1/1 | 5.4c, 5.4e, 5.5b | `_validation_reports/014_planning_validation_report.json` |
| `token_reduction_mqtt_smoke` | running | missing | false | n/a | n/a | n/a | n/a | interrupted / unfinished record | `_step_logs/000_planning_run_manifest.json` |
| `20260604_195636_025684` | success | success | true | none | 94 | 8 rejections, 2 invalid JSON | fallback 2, repair 4/3 | 5.4b, 5.4c, 5.5a, 5.5b | `_validation_reports/014_planning_validation_report.json` |
| `20260604_181442_648931` | failed | failed | false | `blocking_unresolved_questions` | 104 | 9 rejections | fallback 2, repair 5/4 | private codec state | `_validation_reports/014_planning_validation_report.json` |
| `20260604_154257_695173` | failed | failed | false | `blocking_unresolved_questions` | 99 | 6 rejections | fallback 1, repair 4/4 | private codec state | `_validation_reports/014_planning_validation_report.json` |
| `20260604_145058_347573` | success | success | true | none | 25 | 2 rejections | fallback 1, repair 1/1 | 5.5a, 5.5b | `_validation_reports/014_planning_validation_report.json` |
| `20260604_131404_132244` | failed | failed | false | `implementation_plan_5_3_reconciliation_failed`, `missing_packet_enum_type` | 2 | 1 rejection | none | 5.3 type kind merge | `_validation_reports/014_planning_validation_report.json` |
| `20260604_110928_165072` | failed | failed | false | `readiness_wire_mapping_target_not_accessible` | 50 | 7 rejections, 1 invalid JSON | none | 5.4d wire/access | `_validation_reports/014_planning_validation_report.json` |
| `20260604_103833_903365` | failed | failed | false | `implementation_plan_5_3_reconciliation_failed`, `missing_payload_struct_type` | 1 | none | none | 5.3 payload kind | `_validation_reports/014_planning_validation_report.json` |
| `test1` | success | success | true | none | 120 | 6 rejections | none | stage warning volume | `_validation_reports/014_planning_validation_report.json` |
| `test2` | failed | failed | false | `coder_public_lowering_unresolved` | 13 | 5 rejections | none | coder lowering | `_validation_reports/014_planning_validation_report.json` |
| `20260603_211247_910938` | running | missing | false | n/a | partial | partial | n/a | interrupted / unfinished record | `_step_logs/000_planning_run_manifest.json` |
| `20260603_203730_781291` | success | success | true | none | 56 | 4 rejections | none | stage warning volume | `_validation_reports/014_planning_validation_report.json` |
| `20260603_185458_567661` | failed | failed | false | `coder_forbidden_public_symbol` | 90 | 4 rejections, 1 invalid JSON | none | coder public API | `_validation_reports/014_planning_validation_report.json` |

## 历史失败模式及当前状态

| historical failure pattern | occurrence count | first occurrence | last occurrence | fixed in code? | validated by later runs? | still theoretically possible? | evidence |
|---|---:|---|---|---|---|---|---|
| payload struct 被误归类为 `owned_buffer` | 1 run / 3 diagnostics | 2026-06-04 10:53 | 2026-06-04 10:53 | yes | yes | low | `docs/failure_log.md`, `_type_artifact_kind()`, 后续 success run |
| packet enum 被 merge 成 struct | 1 run | 2026-06-04 13:27 | 2026-06-04 13:27 | yes | yes | low | `inventory_reconciliation._merge_type_items()`, 后续 success run |
| wire mapping `target_path` 非 canonical | 1 run / 2 diagnostics | 2026-06-04 11:53 | 2026-06-04 11:53 | yes | yes | low | `merge_wire_access_binding()`, `validate_wire_access_binding_patch()` |
| stale private codec state blocker | 2 runs | 2026-06-04 16:28 | 2026-06-04 19:07 | yes / mitigated | yes | medium-low | `_module_needs_private_state()`, `cleanup_final_unresolved_questions()` |
| coder public lowering unresolved | 1 run / 4 diagnostics | 2026-06-03 22:00 | 2026-06-03 22:00 | mitigated | yes | medium | `validate_coder_semantics()`, strict final gate |
| forbidden public symbol | 1 run / 3 diagnostics | 2026-06-03 19:36 | 2026-06-03 19:36 | mitigated | yes | medium | readiness / coder public symbol validators |
| invalid LLM JSON | 6 observed process events | 2026-06-03 19:36 | 2026-06-09 21:34 | retried, not eliminated | yes | high | recent run still has 2 invalid JSON events |
| runtime entrypoint invalid LLM candidate | repeated | 2026-06-03 19:36 | 2026-06-09 22:19 | mitigated by deterministic fallback | yes | high | latest two runs still fallback |
| calls_allowed invalid edge / cycle | repeated | 2026-06-03 21:45 | 2026-06-09 22:19 | mitigated by normalizer | yes | medium | `normalize_calls_allowed_candidate()`, `normalize_calls_allowed_aggregate()` |
| manifest left `running` | 2 runs | 2026-06-03 21:45 | 2026-06-09 20:21 | no | no | medium | partial `000_planning_run_manifest.json` with empty artifacts |

## 已发生问题、理论风险、最近表现的区分

### 已发生过的真实历史问题

- 5.3 type inventory 曾把 payload struct 错归类为 `owned_buffer`，导致 `missing_payload_struct_type` 和 `buffer_type_missing_size_fields`。
- 5.3 type inventory 曾把 packet enum merge 成 struct，导致 `missing_packet_enum_type`。
- 5.4d wire/access 曾把 helper-local variable name 当作 canonical `target_path`，导致 final readiness failure。
- final implementation plan 曾残留 private codec state lifecycle blocker，导致 `blocking_unresolved_questions`。
- coder lowering 曾出现 public API lowering unresolved 和 forbidden public symbol。
- LLM 输出曾多次出现 invalid JSON、invalid candidate、invalid calls_allowed edge、runtime entrypoint lifecycle mismatch。
- 历史上存在 manifest 停留在 `running` 的中断或异常 run。

### 当前代码中的理论风险

- orchestrator 的主 `plan()` 没有完整包裹所有 stage execution，未预期 exception 或外部中断仍可能留下 `running` manifest。
- controlled inventory 的 JSON retry 和 deterministic reconciliation 能提高完成率，但也可能掩盖 candidate 质量波动。
- 5.4b signature 阶段仍可能生成 public signature 使用 private/unexported type，只是 validator 能拦截并触发 retry / fallback。
- 5.4c behavior contracts 仍可能出现 unknown capability / function / type reference。
- 5.4e `calls_allowed` 仍可能出现 invalid edge、missing call update、cycle，当前通过 normalizer 和 aggregate cycle removal 缓解。
- 5.6 dependency graph 是 deterministic derived-only，稳定性较好，但 unknown target 会被忽略，可能形成“少边”的质量风险。
- Step 6 specs compiler / coder schema / coder loader strict gate 仍是必要的高影响 final gate。

### 最近成功 run 的实际表现

- 没有 final blocking diagnostics。
- `spec_bundle/` 通过 coder schema 和 coder loader。
- 没有 degraded candidate 字段证据。
- 仍有 invalid JSON retry、candidate rejection、fallback、repair、normalization、optional rejection。
- 高 token 消耗集中在 5.4c behavior、5.4b signature、5.4a function inventory。
- 5.5b runtime entrypoint 两次最近 run 都依赖 deterministic fallback。
- 5.4e calls_allowed 两次最近 run 都出现 missing update fill、invalid edge drop 或 cycle edge removal。

## 当前代码脆弱点评估

| code area | potential fragility | observed in recent runs? | observed historically? | severity | likely frequency | downstream impact | recommended action |
|---|---|---|---|---|---|---|---|
| orchestrator top-level `plan()` | 未预期异常或外部中断可能留下 `running` manifest | no | yes | medium | low | run audit 不完整 | logging-only：stale running run 检查 |
| 5.3 / 5.4a controlled inventory | JSON retry 和 deterministic reconciliation 可能掩盖 candidate 波动 | partial | yes | medium | medium | type/function inventory 质量波动 | test + log |
| 5.4b signatures | public API 可能暴露 private/unexported type | yes | yes | high | medium | coder API breakage | 保留 validator，不新增 recovery |
| 5.4c behavior contracts | unknown capability/type/function ref 仍可能出现 | yes | yes | medium | medium | service contract 质量下降 | log rejection code |
| 5.4d wire/access | coverage 和 canonical path 波动可能影响 coder | no recent | yes | high | low | invalid wire mapping | regression test |
| 5.4e calls_allowed | invalid edge、missing update、cycle removal 高频 | yes | yes | medium | high | dependency/call graph 少边或错边 | log dropped edge / fallback count |
| 5.6 dependency graph | derived-only 稳定，但 unknown target 会被静默丢弃 | no direct error | possible | medium | medium | dependency graph 质量波动 | audit log，不让 LLM 生成 final graph |
| Step 6 specs compiler | strict schema/loader failure 仍是高影响 final gate | no recent | yes | high | low-medium | `spec_bundle` 不可用 | 保持 strict gate |
| stage warning aggregation | final success 可能掩盖 stage warning 高企 | yes | yes | medium | high | 质量趋势不可见 | 每次 run 汇总 warning top codes |

## 是否需要稳定性改造

推荐级别：`LOGGING_ONLY`。

理由：

- 最近两次 run 的 final artifact 可用，`coder_schema_status` 和 `coder_loader_status` 均通过。
- 历史高影响失败模式中，payload kind、packet enum kind、wire target path、private stale blocker 都已经有当前代码防护，并被后续成功 run 交叉验证。
- 当前仍反复出现的风险主要是 retry / fallback / warning / normalization，而不是 final result unavailable。
- 这些风险更适合通过日志指标、regression tests 和趋势观察处理，而不是引入新的 fail-soft 架构。
- 新增 recovery framework 会增加复杂度，并可能让 fallback 伪装成高质量 accepted artifact，不符合当前目标。

不建议 `NO_ACTION_NEEDED`，因为最近两次 run 仍存在：

- stage-local warnings；
- LLM rejection；
- invalid JSON retry；
- fallback；
- repair；
- optional item rejection；
- calls_allowed normalizer drop / cycle removal。

不建议 `FAIL_SOFT_DESIGN_NEEDED`，因为：

- 没有证据显示同类高概率错误仍会导致 final failure；
- strict schema / loader 已能拦截不可用 `spec_bundle`；
- 历史主要 failure 已通过局部 guard 和 tests 缓解；
- 当前问题更像 observability 和 regression coverage，不是架构性 recovery 缺口。

不建议 `STRUCTURAL_REFACTOR_NEEDED`，因为：

- 当前架构已经可以完整跑通；
- 失败模式大多可由局部 validator、normalizer、deterministic merge 和 tests 覆盖；
- 没有证据证明现有架构无法通过局部 guard / logging / test 解决稳定性风险。

## 建议的最小后续动作

只建议增加或保留以下 logging / regression checks：

- stale `running` manifest 检查：识别长时间停留在 `status=running` 且 artifacts 为空或缺 final report 的 run。
- 每次 run 汇总 stage warning top codes，特别是 `signature_raw_name_mismatch`、`type_inventory_missing_trace_refs`、`behavior_missing_invariants`。
- 每次 run 汇总 LLM rejection、`invalid_llm_json`、retry count、fallback count、repair count。
- 每次 run 汇总 optional item accepted / rejected count。
- 每次 run 汇总 5.4e normalizer stats，包括 `invalid_call_edges_dropped`、`missing_call_updates_filled`、`aggregate_cycle_edges_removed`。
- 增加 dependency graph audit：记录 derived edges count，以及 ignored unknown function/file targets count。
- 保留 runtime entrypoint fallback regression，确认 deterministic fallback 产物稳定且不会伪装成 LLM accepted artifact。
- 保留 wire/access canonical path regression。
- 保留 payload struct / packet enum kind precedence regression。
- 保留 stale private state unresolved cleanup regression。

## Test 结果

当前环境中 `pytest` 不可用：

```text
/home/ljf/miniconda3/bin/python3: No module named pytest
```

已改用 `unittest` 验证现有测试模块，均通过：

```bash
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering agent.planning.tests.test_validators
```

结果：

```text
Ran 141 tests in 13.549s
OK
```

```bash
python3 -m unittest agent.planning.tests.test_preflight agent.planning.tests.test_compatibility_discovery
```

结果：

```text
Ran 16 tests in 6.155s
OK
```

## 明确不建议修改的部分

- 不新增 Stage Recovery Contract。
- 不新增新的 recovery framework。
- 不新增新的 LLM repair loop。
- 不修改 `implementation_plan/v1`。
- 不放宽 `spec_bundle/` strict schema。
- 不改变 Step 6 specs compiler 的边界。
- 不让 LLM 生成 final dependency graph。
- 不让 fallback 伪装成高质量 accepted artifact。
- 不移除 final readiness gate。
- 不降低 coder schema / coder loader strict validation。
- 不把 stage warning 直接升级为 blocking，除非未来 run 证明其稳定导致 final failure 或 coder failure。

## 最终判断

当前 planning 流程稳定性已经足以继续使用并观察。历史失败模式不能忽略，但多数高影响 failure 已经通过当前代码路径被修复或缓解，并被后续成功 run 验证。最近两次成功 run 仍暴露出过程波动，因此最佳后续动作是 `LOGGING_ONLY`，辅以 targeted regression tests。

不建议立即做 fail-soft 改造。引入额外 recovery complexity 的收益目前不足，反而可能降低 artifact 质量可解释性和 final validation 的清晰边界。
