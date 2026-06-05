# 2026-06-04 Type Inventory Payload Kind Failure

## 背景

最新 planning run 在 `5.3_type_data:codec` 阶段失败：

- `implementation_plan_5_3_reconciliation_failed`
- `buffer_type_missing_size_fields`
- `missing_payload_struct_type`

输出目录：

`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_103833_903365`

## 现象

`_agent_logs/007_5_3_type_data_inventory_candidate__codec.json` 中已经存在以下 payload 类型：

- `type:codec:mqtt_connect_payload_t`
- `type:codec:mqtt_subscribe_payload_t`
- `type:codec:mqtt_publish_payload_t`

但这些类型被标记为：

```json
"kind": "owned_buffer"
```

而 validator 对 `payload_struct` target 只接受 `struct`、`view_struct`、`result_struct`。因此实际 payload 类型存在，但因为 kind 错误，被判定为缺失。

## 根因链条

1. `inventory_planning_space._type_artifact_kind()` 把包含 `payload` 的 TYPE artifact 归类为 `owned_buffer`。
2. 5.2b 的 codec `module_artifacts` 中包含 `mqtt_connect_payload_t`、`mqtt_subscribe_payload_t`、`mqtt_publish_payload_t`，因此 5.3 mandatory slots 一开始就是错误的 `owned_buffer`。
3. protocol facts 同时派生出正确的 `payload_struct` targets，kind 是 `struct`，但它们与 mandatory slots 同名。
4. `inventory_reconciliation._merge_type_items()` 合并 duplicate type slot 时只合并 fields、enum、refs、lifecycle，没有让更具体的 derived `payload_struct` 修正已有 kind。
5. validator 随后同时触发：
   - `buffer_type_missing_size_fields`：`owned_buffer` 必须有 length/capacity 字段。
   - `missing_payload_struct_type`：payload target 查找不接受 `owned_buffer`。

## 推荐修法

优先修正 `inventory_planning_space._type_artifact_kind()`：

- 不再把 `payload` 当作 `owned_buffer` 触发词。
- `payload` 在 protocol planning 中应默认表示 message-specific payload `struct`。
- 真正的 owned byte buffer 应由 `buffer`、`bytes` 或 `target_kind == "owned_buffer"` 表达。

同时建议增强 `inventory_reconciliation._merge_type_items()`：

- 当 incoming duplicate 来自更具体的 `payload_struct` target，且 existing kind 是误判的 `owned_buffer` 时，允许 incoming 的 `struct` kind 覆盖 existing kind。
- 这样可以防止 5.2b artifact role 语义含混时污染最终 type inventory。

## 回归测试建议

在 `test_implementation_plan_stage_candidates.py` 中增加一个覆盖：

- 构造 codec module artifacts，包含 `mqtt_connect_payload_t`、`mqtt_subscribe_payload_t`、`mqtt_publish_payload_t` TYPE artifacts。
- 使用 MQTT message planning IR 构建 `build_type_planning_space()`。
- 执行 `reconcile_type_filling_candidate(space, None)`。
- 断言三个 payload 类型最终 `kind == "struct"`。
- 断言 `validate_type_inventory_candidate()` 不再产生：
  - `buffer_type_missing_size_fields`
  - `missing_payload_struct_type`

## 设计原则

这不是放宽 validator 的问题。validator 的约束是合理的：payload struct 和 owned byte buffer 是不同的 implementation concept。应修正 planning seed/reconciliation 的语义归类，避免把 protocol payload 误降级成 buffer。

---

# 2026-06-04 Wire Mapping Target Path Failure

## 背景

最新 planning run 在 final readiness validation 阶段失败：

- `readiness_wire_mapping_target_not_accessible`

输出目录：

`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_110928_165072`

报错来自：

`_step_logs/007_implementation_plan.json`

## 现象

本次失败不是 wire field 没有 mapping，也不是 `access_path_id` 缺失。两个出错的 mapping 都已经绑定到了正确的 `access_path_id`，但 `wire_mapping_table.target_path` 与 `access_path_table.path` 不一致：

| mapping_id | access_path_id | wire target_path | ACCESS_PATHS path |
| --- | --- | --- | --- |
| `wire:fact:message_model_message_or_command_entries_0_fields_1` | `access:fixed_header:remaining_length` | `remaining_length` | `*remaining_length` |
| `wire:fact:message_model_message_or_command_entries_1_fields_0` | `access:connect:protocol_name` | `str` | `str->data` |

`_step_logs/007_5_4d_function_wire_access_binding_patch.json` 中对应 access entries 已经给出 canonical path：

- `access:fixed_header:remaining_length.path == "*remaining_length"`
- `access:connect:protocol_name.path == "str->data"`

但同一 patch 的 wire mapping entries 仍使用 helper-local output 参数名：

- `target_path == "remaining_length"`
- `target_path == "str"`

## 根因链条

1. 5.4d 的 LLM patch 把 parse helper 的局部输出参数当成 `wire_mapping_entries[*].target_path`。
   - `mqtt_codec_read_remaining_length(..., size_t* remaining_length)` 产生 `target_path: "remaining_length"`。
   - `mqtt_codec_read_string(..., mqtt_bytes_t* str)` 产生 `target_path: "str"`。
2. 同一 patch 的 `access_path_entries` 实际已经表达了更具体、coder-facing 的 canonical storage path：
   - `*remaining_length`
   - `str->data`
3. `merge_wire_access_binding()` 按 `(message_id, field_id)` 聚合 `wire_mapping_table` 时，会从 `(function_id, field_id)` 找到 access entry 并设置 `access_path_id`，但没有同步用 access entry 的 `path` 覆盖 mapping 的 `target_path`。
4. `validate_wire_access_binding_patch()` 没有检查同一 `(function_id, field_id)` 下 `wire_mapping_entries[*].target_path` 与 `access_path_entries[*].path` 是否一致，因此 5.4d validation report 仍然 passed。
5. final readiness gate 在 `validate_full_implementation_plan()` 中检查 `target_path` 必须是 `ACCESS_PATHS` 中的 path 或特殊值 `buffer`，于是拦截了这两个跨表不一致。

## 推荐修法

优先修正 deterministic merge，而不是放宽 readiness validator：

- 在 `implementation_plan_merger.merge_wire_access_binding()` 中，当 wire mapping 可以通过 `(function_id, field_id)` 找到对应 access entry 时：
  - parse / `store_in_field` mapping 的 `target_path` 应归一化为 access entry 的 `path`；
  - serialize mapping 如果目标是 output buffer，可继续保留特殊值 `buffer`；
  - 聚合后的 global `wire_mapping_table` 和 function-level `function["wire_mapping"]` 应使用同一条 canonical target path。

同时增强 5.4d patch validation：

- 在 `validate_wire_access_binding_patch()` 中为每个 `(function_id, field_id)` 建立 access entry index。
- 对非 `buffer` 的 `target_path`，若存在 access entry，则要求 mapping target 与 access path 一致，或至少在 validation/repair hint 中指出会被 deterministic merge 归一化。
- 这样可以把 LLM 输出的局部变量名错误提前暴露在 5.4d，而不是拖到 final readiness。

不建议的修法：

- 不应移除 `readiness_wire_mapping_target_not_accessible`。
- 不应让 final readiness 接受任意 helper-local variable name，因为 downstream specs/coder 需要的是可落到 `ACCESS_PATHS` 的 canonical field storage path。

## 回归测试建议

在 `test_implementation_plan_stage_candidates.py` 中增加覆盖：

- 构造一个 `wire_access_binding_patch`，其中：
  - `wire_mapping_entries[*].target_path == "remaining_length"`；
  - 对应 `access_path_entries[*].path == "*remaining_length"`。
- 执行 `merge_wire_access_binding()`。
- 断言最终 `wire_mapping_table` 中该 mapping 的 `target_path == "*remaining_length"`。
- 断言对应 function-level `wire_mapping` 中该 mapping 的 `target_path` 也被同步为 `*remaining_length`。
- 再运行 `validate_full_implementation_plan()` 或 readiness 相关最小 validator，确认不再产生 `readiness_wire_mapping_target_not_accessible`。

另一个测试可以覆盖 string helper：

- `wire_mapping_entries[*].target_path == "str"`；
- `access_path_entries[*].path == "str->data"`；
- merge 后 target path 应为 `str->data`。

## 设计原则

`wire_mapping_table.target_path` 应表示 decoded protocol field 的 canonical storage path，而不是 parser helper 的局部输出参数名。局部变量名属于 function signature、call binding 或 `source_expr` 层；跨阶段、跨模块传递给 coder 的 mapping target 必须能追溯到 `access_path_table`。

# 2026-06-04 Packet Enum Kind Merge Failure

输出目录：

`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_131404_132244`

控制台报错：

```text
ERROR implementation_plan_5_3_reconciliation_failed [5.3_type_data:mqtt_codec]: 5.3_type_data:mqtt_codec deterministic reconciliation produced invalid final inventory.
ERROR missing_packet_enum_type: module 'mqtt_codec' lacks protocol packet enum target 'target:mqtt_codec:packet_enum'
```

## 现象

5.3 `type_data` 阶段为 `mqtt_codec` 构造 type inventory 时，最终 candidate 中存在 `mqtt_packet_type_t`，也保留了 MQTT control packet values：

- `RESERVED = 0`
- `CONNECT = 1`
- `CONNACK = 2`
- `PUBLISH = 3`
- `SUBSCRIBE = 8`
- `SUBACK = 9`
- `PINGREQ = 12`
- `PINGRESP = 13`
- `DISCONNECT = 14`

但该 type item 的 `kind` 是 `struct`，不是 `enum`。因此 validator 在检查 `target:mqtt_codec:packet_enum` 时，不认为当前 inventory 已经满足 protocol packet enum target，最终报出 `missing_packet_enum_type`。

从本次运行的 5.3 planning space 可以看到同一个概念被生成了两次：

| 来源 | type_id | name | kind |
| --- | --- | --- | --- |
| `module_artifact` mandatory slot | `type:mqtt_codec:mqtt_packet_type_t` | `mqtt_packet_type_t` | `struct` |
| `type_generation_target` derived slot | `type:mqtt_codec:mqtt_packet_type_t` | `mqtt_packet_type_t` | `enum` |

最终 reconciliation 合并同名同 `type_id` slot 时，合并了 `enum_values`，但保留了先进入 inventory 的 `struct` kind，于是形成了一个语义矛盾的结果：`struct` 类型携带 enum values。

## 根因链条

1. `inventory_planning_space._type_artifact_kind()` 对 module-level `TYPE` artifact 的 kind 推断过粗。
   - 当名称或 role 中包含 `packet` 时，会优先归类为 `struct`。
   - `mqtt_packet_type_t` 因为包含 `packet`，被 mandatory slot 误判为 `struct`。
2. 同一 planning space 后续又从 `target:mqtt_codec:packet_enum` 派生出正确的 derived slot。
   - `_type_kind_for_target("packet_enum", ...)` 能正确返回 `enum`。
3. `inventory_reconciliation._merge_type_items()` 合并 duplicate type slot 时，只合并 fields、enum values、dependencies、trace refs、ownership 等内容，不会用更精确的 incoming kind 修正 existing kind。
4. mandatory slot 先进入 inventory，derived slot 后进入；因此最终保留了错误的 `struct` kind。
5. `validate_type_inventory_candidate()` 对 packet enum target 的检查要求 inventory 中存在 `kind == enum` 或等价 enum-like 类型；`struct` with `enum_values` 不满足目标，因此报 `missing_packet_enum_type`。

这次错误和前一次 `payload_struct` 被误归类为 `owned_buffer` 属于同一类问题：generic `module_artifact` slot 的早期粗粒度 kind 覆盖了 derived `type_generation_target` slot 的精确语义。

## 推荐修法

优先修复 kind 推断和 duplicate merge 规则，不建议放宽 validator。

1. 在 `inventory_planning_space._type_artifact_kind()` 中优先识别 packet type enum。
   - 将 `packet_type`、`packet type`、`control packet type`、`type enum`、`*_packet_type_t` 等模式归类为 `enum`。
   - 这个判断必须放在普通 `packet` / `container` / `decoded message` 的 `struct` 判断之前。
   - 可以借鉴已有 `_type_seed_kind()` 中 `packet_type -> enum` 的判断规则，避免两套推断逻辑继续漂移。

2. 在 `inventory_reconciliation._merge_type_items()` 中增加 kind specialization precedence。
   - 当 incoming item 来自 derived `type_generation_target(packet_enum)`，或者携带 packet enum target trace refs 且有 `enum_values` 时，应允许 `enum` 覆盖 existing `struct`。
   - 当 incoming item 来自 `payload_struct` target 时，应允许 `struct` 覆盖 existing 的错误 `owned_buffer`。
   - 总原则是：`type_generation_target` derived slot 表示 planning agent 已经做过目标化推导，其 semantic kind 应优先于 generic `module_artifact` 的启发式 kind。

3. 为了让 merge 层判断更干净，可以在 `_type_from_slot()` 生成 type item 时保留少量来源元数据。
   - 例如 `slot_class`、`source_kind`、`source_id` 或 `target_kind`。
   - 如果暂时不想扩大 schema，可以先用 `trace_refs` 中的 `decision:type_slot:*:packet_enum:*` / `payload_struct` 作为最小判断依据，但长期看显式 metadata 更稳。

不建议的修法：

- 不应让 `struct` with `enum_values` 通过 `packet_enum` validator。
- 不应移除 `missing_packet_enum_type` readiness/validation 检查。
- 不应在 coder-facing specs 中把 protocol packet type 表达成普通 struct，因为 packet type enum 会影响 switch、dispatch、validation 和 conformance tests 的生成质量。

## 回归测试建议

在 `agent/planning/tests/test_implementation_plan_stage_candidates.py` 或更靠近 inventory reconciliation 的测试文件中增加最小覆盖：

- 构造 module artifact：`mqtt_packet_type_t`，role 不显式包含 `enum`，但名称包含 `packet_type`。
- 构造 planning IR，使 `derive_type_generation_targets()` 能产生 `target:mqtt_codec:packet_enum`。
- 执行 `build_type_planning_space()` 和 `reconcile_type_filling_candidate(space, None)`。
- 断言最终 `type:mqtt_codec:mqtt_packet_type_t.kind == "enum"`。
- 断言 `enum_values` 包含 MQTT control packet values。
- 运行 `validate_type_inventory_candidate()`，断言不再出现 `missing_packet_enum_type`。

建议再补一个 generalized duplicate precedence 测试：

- mandatory `struct` + derived `enum` 共享同一个 `type_id` 时，最终应为 `enum`。
- mandatory `owned_buffer` + derived `payload_struct` 共享同一个 `type_id` 时，最终应为 `struct`。

## 设计原则

module artifact 的 role/name 启发式只适合作为初始猜测；derived target 是 planning agent 根据协议事实和实现目标生成的更强约束。两者冲突时，reconciliation 应保留 derived target 的 semantic kind，否则 final inventory 会出现表面信息齐全、核心类型类别错误的不可用状态。

# 2026-06-04 Blocking Private State Lifecycle Question Failure

输出目录：

`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_154257_695173`

控制台报错：

```text
ERROR blocking_unresolved_questions [/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_154257_695173/_step_logs/007_implementation_plan.json]: final implementation_plan still contains blocking unresolved questions
```

## 现象

本次 final readiness 只有一个 error：`blocking_unresolved_questions`。`007_implementation_plan.json` 中残留的 blocking unresolved question 也只有一个：

```json
{
  "question_id": "question:codec:private_state_lifecycle",
  "target_kind": "type",
  "target_id": "struct mqtt_codec",
  "question": "What functions create, initialize, destroy, and free the private mqtt_codec state?",
  "unresolved_reason": "No function artifacts are provided that manage this internal state",
  "blocking": true,
  "trace_ref_keys": [
    "decision:type_slot:codec:internal_state:target_codec_private_state"
  ]
}
```

对应的 type item 已进入 final plan：

- `type_id`: `type:codec:mqtt_codec`
- `name`: `struct mqtt_codec`
- `kind`: `internal_state`
- `visibility`: `private`
- `defined_in`: `source_file`
- `fields`: `[]`
- `lifecycle`: create/init/destroy/free 全为空
- `status`: `unresolved`

但 final plan 中没有任何 function contract 真正引用 `struct mqtt_codec`。`codec` 模块已经有 `mqtt_decode`、`mqtt_encode`、内部 decode/encode helpers、`mqtt_codec_cursor_t`、`mqtt_codec_decode_result_t`、`mqtt_codec_encode_buffer_t` 以及 payload/packet/buffer release functions。也就是说，当前 coder-facing surface 并不依赖这个空的 private module state。

## 根因链条

1. `implementation_plan_context.derive_type_generation_targets()` 对 private state target 的生成条件过宽。
   - 当前逻辑只要 `module_artifact.state_owned` 或 `module_artifact.owned_capabilities` 非空，就会生成 `target:{module}:private_state`。
   - 本次 `codec` 的 `state_owned` 是 `packet type definitions`、`parser state machine definitions`，更接近静态定义或算法状态机描述，不是 runtime heap/resource owner。
   - `codec` 的 `owned_capabilities` 是 `message_decode`、`message_encode`、`incremental_message_framing`、`canonical_type_ownership`，这些能力需要 packet/result/buffer 类型和 parser cursor，但不必然需要一个 module-level `struct mqtt_codec`。
2. 5.3 planning space 因此生成了 derived slot：
   - `source_id`: `target:codec:private_state`
   - `kind`: `internal_state`
   - `name`: `struct mqtt_codec`
   - `source_reason`: `Resource-owning modules need private implementation state.`
3. LLM type filling candidate 将该 slot 标记为 `status: unresolved`，并添加 blocking question `question:codec:private_state_lifecycle`。
4. `reconcile_type_filling_candidate()` 把 `filling_candidate.unresolved_questions` 原样复制到 type inventory candidate。
5. `merge_type_inventory()` 又直接执行：
   - `result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))`
   - 它不像 5.4b function signature 或 5.4e call contracts 那样使用 `_nonblocking_questions()`。
6. 后续阶段没有重新判断该 type 是否仍是 coder-facing 必需目标，也没有把已经无用的 private-state blocker 清理或降级。
7. final readiness 在 `validate_full_implementation_plan()` 中拒绝任何 `blocking: true` 的 unresolved question，于是整次 planning 失败。

额外观察：

- 5.3 type inventory validation report 是 `passed: true`，说明 stage-local validation 没有把 blocking unresolved question 当作错误。
- `_lifecycle_for_type()` 的 internal state heuristic 只检查 `name kind purpose`，不检查 `ownership_lifetime`。本次 LLM 给了 `ownership_lifetime: "module-owned"`，但 lifecycle helper 没有据此补 `mqtt_codec_destroy`。这不是唯一根因，但会让 lifecycle question 更容易保留为空。

## 推荐修法

优先修正 private state target 的生成条件，而不是移除 final readiness gate。

1. 收紧 `derive_type_generation_targets()` 中 `private_state` target 的触发条件。
   - 只有模块确实拥有 runtime mutable resource 时才生成 `internal_state`。
   - 正向信号可以包括：`connection`、`session`、`socket`、`fd`、`timer`、`heap`、`registry`、`tree`、`store`、`map`、`buffer`、`runtime`、`context`、`lifecycle` 等。
   - 对 `packet type definitions`、`parser state machine definitions`、`protocol constants`、`enum definitions`、`stateless codec algorithms` 这类 definition-only state，应避免生成 module-level private state。
   - 对 `codec`，应优先使用已有的 `parser_cursor`、`decode_result`、`encode_buffer` 目标表达实现需要，而不是强制引入 `struct mqtt_codec`。

2. 增加 final merge 前的 unresolved question resolution pass。
   - 对 type-stage unresolved question，若其 `target_id` 指向 private/internal type，且该 type 没有 fields、dependencies、related_functions，也没有任何 function signature/access path/file layout 引用，应降级为 `blocking: false` 或直接移到 assumption/note。
   - 若目标 type 已被后续 lifecycle repair、function inventory 或 dependency repair 满足，应删除对应 stale question。
   - 这一步比简单地在 `merge_type_inventory()` 中全部 `_nonblocking_questions()` 更精确，可以保留真正阻断 coder 的 unresolved facts。

3. 对确实需要 private state 的模块补 deterministic lifecycle。
   - `_lifecycle_for_type()` 应把 `ownership_lifetime` 纳入判断，让 `module-owned` internal state 能产生合理的 destroy/cleanup obligation。
   - function inventory 或 lifecycle repair 应根据 `internal_state` obligation 生成 `*_init` / `*_cleanup` / `*_destroy` function seed。
   - 当这些 function contracts 已存在后，应同步更新 type lifecycle 并清理对应 unresolved question。

不建议的修法：

- 不应移除 `blocking_unresolved_questions` final readiness 检查。final implementation plan 带着 blocking question 交给 coder，会降低 downstream code generation 的确定性。
- 不应把所有 5.3 unresolved questions 无条件改成 nonblocking。协议事实缺失、公共 API 生命周期不明、wire format 不完整这类问题仍然可能是真 blocker。
- 不应为了消除错误而给 `codec` 硬塞无字段、无引用的 `mqtt_codec_init/destroy` API；这会污染模块边界，并给 coder agent 制造没有实际用途的 lifecycle surface。

## 回归测试建议

增加一个 `derive_type_generation_targets()` 覆盖：

- 构造 `codec` module：
  - `state_owned`: `["packet type definitions", "parser state machine definitions"]`
  - `owned_capabilities`: `["message_decode", "message_encode", "incremental_message_framing"]`
- 断言不会生成 `target:codec:private_state`。
- 同时断言仍然可以生成或保留 `parser_cursor`、`decode_result`、`owned_buffer` 等 codec helper targets。

增加一个正向 runtime resource 覆盖：

- 构造 `network` module：
  - `state_owned`: `["socket descriptors", "per-connection I/O buffers", "parser progress state"]`
  - `owned_capabilities`: `["transport_io", "connection_lifecycle"]`
- 断言仍然生成 `target:network:private_state` 或等价 internal state target。

增加一个 final unresolved cleanup 覆盖：

- 构造 plan，其中 `type_inventory` 含 private empty `internal_state`，且 `unresolved_questions` 含对应 `blocking: true` lifecycle question。
- 若该 type 无 fields、无 dependencies、无 function signature 引用，cleanup 后 question 应变为 nonblocking 或被移除。
- 再运行 `validate_full_implementation_plan()`，确认不再报 `blocking_unresolved_questions`。

另一个测试覆盖真实 blocker：

- public type 或 runtime internal state 有 owned fields / dependencies，但没有 lifecycle functions。
- cleanup pass 不应误删该 question；应保留 blocking 或触发 deterministic lifecycle repair。

## 设计原则

`unresolved_questions` 是 planning agent 对事实缺口和实现风险的显式记录，但 final implementation plan 不能携带会阻断 coder 的 stale blocker。private/internal helper target 尤其要区分“真实 runtime resource state”和“为描述算法或定义而产生的静态 state”。前者需要 lifecycle contract；后者应被表达为 helper type、enum、cursor/result/buffer，或作为 nonblocking implementation note。

# 2026-06-04 Blocking Codec Private State Question Recurrence

输出目录：

`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_181442_648931`

控制台报错：

```text
ERROR blocking_unresolved_questions [/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260604_181442_648931/_step_logs/007_implementation_plan.json]: final implementation_plan still contains blocking unresolved questions
```

## 现象

final readiness 仍然只报一个 error：`blocking_unresolved_questions`。本次 final plan 中唯一 `blocking: true` 的 unresolved question 是：

```json
{
  "question_id": "question:codec:mqtt_codec:lifecycle",
  "target_kind": "type",
  "target_id": "slot:type:codec:derived:mqtt_codec",
  "question": "What functions create, initialize, destroy, and free the private codec state?",
  "unresolved_reason": "No function artifacts specify lifecycle management for internal state",
  "blocking": true,
  "trace_ref_keys": [
    "decision:type_slot:codec:internal_state:target_codec_private_state"
  ]
}
```

对应 final type item：

- `type_id`: `type:codec:mqtt_codec`
- `name`: `struct mqtt_codec`
- `kind`: `internal_state`
- `visibility`: `private`
- `defined_in`: `source_file`
- `fields`: `[]`
- `dependencies`: `[]`
- `related_functions`: `[]`
- `lifecycle`: create/init/destroy/free 全为空
- `status`: `unresolved`

这次额外核对了引用面：

- function signatures 中没有参数精确引用 `type:codec:mqtt_codec`。
- access path table 中没有引用 `type:codec:mqtt_codec`。
- file layout 没有导出或声明 `type:codec:mqtt_codec`。
- codec 已经有实际需要的 stateless API 和 helper：`mqtt_decode_packet`、`mqtt_encode_packet`、`mqtt_packet_free`、`mqtt_bytes_free`、`mqtt_codec_cursor_t`、`mqtt_codec_decode_result_t`、`mqtt_codec_encode_buffer_t`。

因此，这个 blocker 不是 coder-facing 必需 surface 的真实缺口，而是一个空的 private state target 残留。

## 与上次的差异

这次比 `20260604_154257_695173` 更能说明 private state target 的生成条件过宽：

- 上次 `codec.state_owned` 里还有 `packet type definitions`、`parser state machine definitions`，可以怀疑是这些静态 definition 文本触发了 private state。
- 本次 `codec.state_owned == []`。
- 但 `codec.owned_capabilities` 仍然包含：
  - `message_decode`
  - `message_encode`
  - `incremental_message_framing`
  - `canonical_type_ownership`
- `derive_type_generation_targets()` 仍然生成了 `target:codec:private_state`。

也就是说，当前逻辑只要 `owned_capabilities` 非空，就可能把纯算法/定义拥有者误判成 runtime resource owner。

## 根因链条

1. selected architecture 将 `codec` 描述为 stateless codec：
   - responsibilities 包含 `Stateless incremental decode/encode of protocol units`。
   - `state_owned` 为空。
2. `implementation_plan_context.derive_type_generation_targets()` 的 private state 条件仍是：
   - `module_artifact.get("state_owned") or module_artifact.get("owned_capabilities")`
3. 因为 `codec.owned_capabilities` 非空，5.3 planning space 生成了：
   - `source_id`: `target:codec:private_state`
   - `slot_id`: `slot:type:codec:derived:mqtt_codec`
   - `kind`: `internal_state`
   - `name`: `struct mqtt_codec`
   - `required_fields`: `[]`
   - `source_reason`: `Resource-owning modules need private implementation state.`
4. LLM type filling candidate 没法为这个空 private state 找到 lifecycle functions，于是输出 blocking question `question:codec:mqtt_codec:lifecycle`。
5. 5.3 validation 仍然 passed，因为 type inventory validator 不把 `blocking` unresolved question 当作 stage-local error。
6. `merge_type_inventory()` 将 5.3 unresolved questions 原样并入 final plan，没有做 `_nonblocking_questions()`，也没有做基于后续 artifacts 的 stale question resolution。
7. 后续 function inventory/signature 阶段生成了真正需要的 codec helper functions，但没有任何阶段删除或降级这个 private-state lifecycle blocker。
8. final readiness gate 拒绝任意 `blocking: true` unresolved question，于是整次 planning 失败。

还有一个旁证：5.3 中同时存在非阻塞问题 `question:codec:mqtt_bytes_t:free_function_existence`，询问是否有 `mqtt_bytes_free`；但 final function contracts 已经生成了 `fn:codec:mqtt_bytes_free`。这说明 unresolved question cleanup 不只影响 blocker，也会让已经解决的问题作为 stale note 残留。

## 推荐修法

1. 不要仅凭 `owned_capabilities` 生成 `private_state` target。
   - `message_decode`、`message_encode`、`canonical_type_ownership` 应推导 packet enum、payload struct、packet container、owned buffer、cursor/result/helper types。
   - 它们不应默认推导 module-level `struct mqtt_codec`。
   - `incremental_message_framing` 可以推导 parser cursor 或 decode result，但仍不必然需要 module private state。

2. 为 private state target 增加 runtime-resource 判定。
   - 可以要求 `state_owned` 或 responsibilities 中包含明确 runtime mutable resource 信号：`connection`、`socket`、`fd`、`session`、`timer`、`heap`、`registry`、`tree`、`store`、`map`、`buffer pool`、`event loop`、`runtime context` 等。
   - 对 `stateless`、`definition`、`packet type definitions`、`parser state machine definitions`、`protocol constants` 等文本，应禁止生成 private state。
   - 如果 module role/responsibility 明确出现 `stateless`，应作为强负向信号。

3. 对空 private state target 做 deterministic pruning。
   - 如果 derived `internal_state` slot 的 `required_fields == []`，且 module 没有 runtime resource 信号，可以不加入 `derived_type_slots`，或设置 `default_include: false`。
   - 如果 LLM 已经把它输出到 type inventory，但最终没有 fields/dependencies/function refs/file refs，应在 final cleanup 中删除该 private type，或至少删除/降级其 lifecycle blocker。

4. 增加 unresolved question resolution pass。
   - 建立 `slot_id -> type_id` 映射，避免 question `target_id` 指向 slot id 时无法和 final type inventory 对齐。
   - 对生命周期类 question，检查后续 function contracts 是否已经生成对应 release/create 函数。
   - 已解决的问题删除；不影响 coder-facing surface 的 private/internal helper 问题降级为 `blocking: false`；仍缺少公共 API 或真实 runtime resource lifecycle 的问题继续 blocking。

5. 保留 final readiness gate。
   - `blocking_unresolved_questions` 是有价值的 final 防线。
   - 修法应保证不生成或不保留 stale blocker，而不是让 final plan 带着 blocking question 继续输出给 coder。

## 回归测试建议

增加一个 stateless codec 覆盖：

- module:
  - `module_id: codec`
  - `state_owned: []`
  - `owned_capabilities: ["message_decode", "message_encode", "incremental_message_framing", "canonical_type_ownership"]`
  - responsibilities 含 `Stateless incremental decode/encode`
- 断言 `derive_type_generation_targets()` 不生成 `target:codec:private_state`。
- 断言仍生成 packet/container/buffer/cursor/result 等 codec 相关 targets。

增加一个 runtime resource 正向覆盖：

- module:
  - `module_id: network`
  - `state_owned` 含 `socket descriptors`、`per-connection I/O buffers`
  - `owned_capabilities` 含 `transport_io`、`connection_lifecycle`
- 断言仍生成 `target:network:private_state`。

增加一个 stale unresolved cleanup 覆盖：

- plan 中包含：
  - private empty `type:codec:mqtt_codec`
  - blocking question target 为 `slot:type:codec:derived:mqtt_codec`
  - final function signatures/access paths/file layout 均未引用 `type:codec:mqtt_codec`
- cleanup 后应删除或降级该 question。
- `validate_full_implementation_plan()` 不应再报 `blocking_unresolved_questions`。

增加一个已解决 lifecycle question 覆盖：

- 5.3 unresolved question 询问 `mqtt_bytes_free` 是否存在。
- 后续 `function_contracts` 中存在 `fn:codec:mqtt_bytes_free`，且参数引用 `type:codec:mqtt_bytes_t`。
- cleanup 后应删除该 stale question，至少不应继续保留为 unresolved planning risk。

## 设计原则

`owned_capabilities` 表示模块承担某类实现职责，不等价于拥有 runtime mutable state。planning agent 在从 capabilities 推导 type targets 时，应区分“协议表示/算法 helper 类型”和“需要 lifecycle contract 的 runtime resource state”。否则会把 stateless codec 这类模块错误地变成需要 create/destroy 的对象，造成空 private type 与 stale blocking question。
