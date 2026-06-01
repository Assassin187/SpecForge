# 5.4a / 5.4b Controlled Inventory Flow

本文说明 Implementation Plan Synthesis 中 `5.4a_type_inventory` 与
`5.4b_function_inventory` 的 controlled inventory 流程。当前设计把 LLM 限定为
局部 semantic proposal generator：deterministic code 先编译 planning space，LLM
只填语义与提出 module-local optional proposals，最终 identity、coverage、
visibility、ownership、lifecycle obligation 和 coder compatibility 都由 deterministic
reconciler 与 validator 负责。

## 核心边界

- `5.3` 的 `TYPE` / `FUNC` artifacts 是 mandatory seeds，不是完整 inventory。
- `5.4a` 只产出 type inventory，不生成 function signature、behavior、wire mapping、
  calls_allowed、file layout、dependency graph 或 code。
- `5.4b` 只产出 function inventory，不生成 signature、behavior、wire mapping、
  calls_allowed、file layout、dependency graph 或 code。
- 不确定或缺失的协议信息进入 `assumptions` / `unresolved_questions`，不能编造
  protocol facts。
- semantic quality、implementation richness、decomposition warnings 当前只写入
  diagnostics/reporting，不触发 repair，不阻断 pipeline，除非 required coverage 缺失。

## 5.4a Type Data Flow

1. `build_type_planning_space(draft, module_artifact, planning_ir, profile, constraints)`
   为每个 module 编译 deterministic type planning space。
2. Orchestrator 写入
   `_step_logs/007_5_4a_type_planning_space__<module>.json`，并把 planning space 放入
   `type_filling_candidate_prompt` context。
3. LLM 返回 `type_filling_candidate/v1`，只能填 slot semantics、fields、
   enum_values、callback_signature、ownership/lifetime、lifecycle notes，并提出
   optional module-local type proposals。
4. 如果 LLM JSON 不可用，只做 JSON retry；耗尽后使用 empty filling candidate 进入
   deterministic reconciliation，保证 bootstrap pipeline 继续运行。
5. `reconcile_type_filling_candidate(space, candidate)` 生成最终
   `type_inventory_candidate/v1`，并输出 reconciliation report、diagnostics 和
   type obligation sidecar。
6. `validate_type_inventory_candidate()` 校验最终 candidate。blocking errors 阻断；
   quality/richness findings 只进入 warnings/diagnostics。
7. 聚合后 `merge_type_inventory()` 写入 `draft["type_inventory"]`，并同步
   public/public_header types 到 `canonical_types`。

## Type Planning Space

`type_planning_space/v1` 至少包含：

- `mandatory_type_slots`：来自 5.3 `TYPE` artifacts，必须覆盖，LLM 不能删除或改
  identity。
- `derived_type_slots`：从 protocol facts、core design、handler matrix、resource
  lifecycle、message/field indexes、state/resource ownership 派生，原则上必须覆盖。
- `recommended_type_slots`：提升 coder compatibility 的 helper types，例如 parser
  cursor、decode result、encode buffer、validation context、dispatch context、
  event struct、buffer view。
- `allowed_type_refs`：当前 module types、provider public types、canonical public
  types、system types。
- `forbidden_type_refs`：provider private/internal types、unknown natural-language type
  names、other module private types、误用的 state/message/field ids。
- `optional_expansion_policy`：允许 LLM 提出 module-local optional types，但必须带
  `expansion_reason`、source binding 和合法 refs；最终 id/name/module/kind/visibility
  由 reconciler 确认或重写。
- `richness_diagnostics`：stateful/message/resource/callback/parser-heavy module 的
  non-blocking quality findings。

## 5.4b Function Data Flow

1. 以已接受的 5.4a draft 为输入，
   `build_function_planning_space(draft, module_artifact, planning_ir, profile, constraints)`
   编译 deterministic function planning space。
2. Planning space 包含 mandatory `FUNC` seeds、type/lifecycle obligation seeds、
   handler seeds、parser/serializer entry seeds 和 recommended function families。
3. LLM 返回 `function_annotation_candidate/v1`，只能 annotate required seeds，并提出
   justified optional module-local helper functions。
4. 如果 LLM JSON 不可用，只做 JSON retry；耗尽后使用 empty annotation candidate 进入
   deterministic reconciliation。
5. `reconcile_function_annotation_candidate(space, candidate)` 生成最终
   `function_inventory_candidate/v2`，保留所有 required seeds，规范 optional helpers，
   丢弃非法 refs 或 boundary-violating proposals 并记录 diagnostics。
6. 聚合后执行 `reconcile_type_inventory_function_refs()`，把 type lifecycle refs 对齐
   accepted lifecycle/API functions，再进入 5.4c。

## Function Planning Space

`function_planning_space/v1` 至少包含：

- `mandatory_function_seeds`：来自 5.3 `FUNC` artifacts，必须覆盖，identity 不由 LLM
  决定。
- `obligation_function_seeds`：从 5.4a lifecycle/type obligations、owned fields、
  callback types、resource ownership 派生。
- `handler_function_seeds`：从 `handler_matrix` 派生，handler owner module 必须覆盖。
- `parser_serializer_function_seeds`：从 message decode/encode capabilities 和 message
  model 派生。
- `recommended_function_families`：parser_helpers、serializer_helpers、
  validation_helpers、handler_helpers、dispatch_helpers、state_transition_helpers、
  resource_lifecycle_helpers、error_helpers、buffer_helpers、protocol_event_helpers。
- `legal_refs`：合法 type/message/field/handler/capability ids。
- `optional_expansion_policy`：允许 LLM 提出 module-local helper functions；每个 helper
  必须绑定 family、source refs、related type/message/handler/resource。
- `richness_diagnostics`：under-decomposition、missing helper family、coarse split
  candidates 等 non-blocking findings。

## Reconciliation Rules

- Required identity 只来自 deterministic slots/seeds。LLM 不能删除、重命名、改 module、
  改 visibility 或改 required seed identity。
- Type reconciler 固定 `type_id/name/module_id/kind/visibility/defined_in`，只吸收语义
  fields、enum_values、callback_signature、purpose、ownership/lifetime、dependencies、
  assumptions、unresolved_questions。
- Function reconciler 固定 required `function_id/name/module_id/function_kind/visibility/
  api_surface/exported/public_api_role`，只吸收 purpose、grouping_hint、trace notes 和
  optional helper proposals。
- Optional proposals 必须 module-local、refs 合法、source binding 明确；非法 proposal
  不进入最终 candidate，只进入 rejection diagnostics。
- Duplicate merge 顺序为 required > derived > recommended > optional，并保留
  `trace_ref_keys` / `source_reason`。
- Public type 不得依赖 private/module_internal type；unknown type refs 和 provider
  private refs 会被 rejected/corrected。
- Owned/internal/resource/container type 的 lifecycle obligation 写入 sidecar；最终
  type item 只保留 lowering-safe `lifecycle` 与 `related_functions`。

## Artifacts

5.4a 保持最终 artifact 名称，并新增 sidecar：

- `_step_logs/007_5_4a_type_planning_space__<module>.json`
- `_step_logs/007_5_4a_type_reconciliation_report__<module>.json`
- `_step_logs/007_5_4a_type_inventory_diagnostics__<module>.json`
- `_step_logs/007_5_4a_type_obligations__<module>.json`
- `_step_logs/007_5_4a_type_inventory_candidate__<module>.json`
- `_validation_reports/007_5_4a_type_inventory_validation_report__<module>.json`

5.4b 保持最终 artifact 名称，并新增 sidecar：

- `_step_logs/007_5_4b_function_planning_space__<module>.json`
- `_step_logs/007_5_4b_function_reconciliation_report__<module>.json`
- `_step_logs/007_5_4b_function_inventory_diagnostics__<module>.json`
- `_step_logs/007_5_4b_function_inventory_candidate__<module>.json`
- `_validation_reports/007_5_4b_function_inventory_validation_report__<module>.json`

两个阶段的 attempt summary 使用 `controlled_inventory_attempt_summary/v1`，记录
JSON retry、accepted_by、optional accepted/rejected counts。`full_retry_count` 与
`validator_repair_count` 固定为 0。

## Validation Policy

Type blocking errors 包括 unknown type ref、public/private leak、duplicate id/name、
wrong ownership、missing mandatory/derived slot、invalid visibility、pointer ownership
missing、owned/resource/container missing lifecycle obligation。

Function blocking errors 包括 unknown refs、duplicate id/name、missing mandatory FUNC
seed、uncovered lifecycle/type obligation、missing handler seed、missing parser/serializer
entry、public API inconsistency、invalid coder_function_type、wrong module/enum/missing
required field。

Warnings/diagnostics 包括 duplicate concept type、coarse decomposition、missing
recommended slot、under-decomposition、missing helper family、missing parser/serializer
helpers、low implementation richness。这些报告到 validation report 或 diagnostics
artifact，但不改变 lowering/coder strict specs。
