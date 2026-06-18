# SpecForge Planning Pipeline Invariant Repair 五轮计划书

更新时间：2026-06-18

## 1. 文档定位

本文档是独立于 `docs/PLANNING_STABILIZATION_TASKS.md` 的新计划。

旧计划关注：

```text
让 planning 输出能稳定驱动 coder compile/smoke，并推进 MQTT、CoAP、SMTP minimum 复现。
```

本计划关注：

```text
基于近 8 次 MQTT planning run 的失败归因，修复 planning pipeline 中跨阶段不变量缺失的问题。
```

本计划与旧计划的区别：

- 旧计划是中期稳定化路线。
- 本计划是 pipeline invariant repair 路线。
- 本计划不按单次 error code 做补丁，而按跨阶段 invariant 做闭包修复。
- 本计划从原先 8 个细 Step 收敛为 5 个递进 Round，降低切换成本和上下文碎片。

## 2. 为什么改成五轮

原先 Step 0-8 太细，容易让每次会话只修一个 error surface。更合理的节奏是：

```text
第 1 轮：先修 type closure，因为它是 signature、header lowering、coder surface 的基础。
第 2 轮：收口 LLM 输出结构化问题，包括 call value_ref 与 wire mapping coverage。
第 3 轮：修 function inventory 附近的问题，包括 runtime lifecycle 和 function symbol collision。
第 4 轮：单独修 C public header surface，因为它跨 planning/coder 边界，耦合较高。
第 5 轮：集中固化 regression/golden，确认前四轮不是局部补丁。
```

每轮都可以加局部 regression tests，但第 5 轮负责把 7 个历史失败和 MQTT minimum golden 串成完整防线。

## 3. 背景与根因摘要

分析范围：

```text
agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1 最近 8 次运行目录
```

其中 1 次为空运行或中断，7 次为有效失败。主要失败类型包括：

```text
1. wire field coverage 不完整。
2. function name collision。
3. runtime lifecycle API 缺失。
4. call_contract param_bindings 使用 prose value_ref。
5. string_view / buffer_view / byte_buffer 等 type alias 未 canonicalize。
6. C public header dependency cycle。
7. coder compile 才暴露 header surface closure failure。
```

根本原因：

```text
LLM 输出仍然带有局部瑕疵；
schema 允许自由字符串或 abstract alias 泄漏；
deterministic repair 只做局部 normalize；
stage validator 与 final readiness / coder compile 的规则不一致；
很多 coder-facing invariant 到最后才被发现。
```

核心原则：

```text
先定义 invariant，再让每个 stage 维护 invariant。
LLM 可以不完美，但不能让不完美输出穿过 deterministic closure。
final readiness 不应首次发现同类错误，只应确认前面已经证明过的闭包。
```

## 4. 总体验收标准

完成五轮后应满足：

```text
1. 7 个历史失败样例都有 regression fixture 或 replay check。
2. 5.3 type inventory 后不再出现 raw abstract view alias。
3. 5.4d 后所有 planning_ir wire fields 都有 parser/serializer coverage 或 blocking unresolved diagnostic。
4. 5.4e 后 call_contracts 的 param_bindings 不包含 prose value_ref。
5. 5.4a function inventory 后不再出现 global function name collision。
6. deployable broker/server/client target 在 5.4a 后具备完整 runtime lifecycle API。
7. specs_compile 前可以证明 public header dependency graph acyclic。
8. final planning success 不再在 coder loader/rendered header compile 阶段暴露 deterministic closure failure。
9. MQTT minimum fresh planning 至少一次进入 specs_compile，并产出可通过 coder validate 的 spec_bundle。
```

## 5. 历史失败到五轮的映射

| 失败类别 | 代表 run | 目标轮次 |
|---|---|---|
| `string_view` / `buffer_view` unknown type | `20260617_172331_047723` | 第 1 轮 |
| `byte_buffer` raw alias 残留 | `20260617_205742_685499` | 第 1 轮 |
| prose `value_ref` | `20260617_103648_329047` | 第 2 轮 |
| fixed-header-only wire mapping | `20260610_093743_377786` | 第 2 轮 |
| runtime lifecycle missing | `20260616_194800_796436` | 第 3 轮 |
| duplicate function name | `20260612_092317_251284` | 第 3 轮 |
| `codec.h` / `protocol_codec.h` circular include | `20260616_231307_313900` | 第 4 轮 |
| 7 个失败集中 replay/golden | 全部有效失败 | 第 5 轮 |

---

# 第 1 轮：高收益低耦合

## 6.1 目标

完成 type canonicalization + 5.3 regression fixtures。

本轮直接覆盖：

```text
20260617_172331_047723: string_view / buffer_view 未声明 type ref。
20260617_205742_685499: byte_buffer raw alias 残留。
```

这是最适合先做的一轮，因为 type closure 是后续 function signature、header lowering、coder surface 的基础。

## 6.2 主要任务

1. 建立集中 alias registry：
   - string aliases：
     ```text
     string_view
     utf8_string_view
     utf8_string
     UTF-8 string
     topic name
     topic filter
     client id
     protocol name
     ```
   - buffer aliases：
     ```text
     buffer_view
     bytes_view
     payload_view
     byte_buffer
     bytes
     binary payload
     opaque payload
     remaining bytes
     ```
2. 修改 field type normalization：
   - 优先根据 `field_name + field_type + summary + syntax + entry_summary` 归一。
   - MQTT string view 统一为 `mqtt_string_view_t`。
   - MQTT buffer view 统一为 `mqtt_buffer_view_t`。
3. 修改 reconciliation：
   - 同步更新 `field_type` 和 `type_ref`。
   - 禁止出现 `type_ref=mqtt_buffer_view_t` 但 `field_type=byte_buffer`。
4. 确保 type planning space 稳定生成 protocol-specific view type slots。
5. 加 5.3 regression fixtures：
   - `string_view`。
   - `buffer_view`。
   - `byte_buffer`。

## 6.3 涉及文件候选

```text
agent/planning/stages/implementation_plan_context.py
agent/planning/stages/inventory_reconciliation.py
agent/planning/stages/inventory_planning_space.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
agent/planning/tests/test_coder_schema_lowering.py
```

## 6.4 完成标准

```text
[ ] string_view / buffer_view / byte_buffer fixture 均生成 declared protocol view type。
[ ] final type inventory 不含 raw string_view / buffer_view / byte_buffer public field type。
[ ] unknown_type_ref 不再由 abstract view alias 触发。
[ ] declared view structs 可 lower 到 coder header data。
[ ] 相关 unittest 通过。
```

## 6.5 本轮执行提示词

```text
请执行 PIPELINE_INVARIANT_REPAIR_PLAN.md 的第 1 轮：高收益低耦合。
目标是完成 type canonicalization + 5.3 regression fixtures，
覆盖 string_view、buffer_view、byte_buffer 三类 alias 泄漏问题。

请先阅读 implementation_plan_context.py、inventory_reconciliation.py、inventory_planning_space.py 中现有 normalize / alias / target generation 逻辑。
要求同时修复 field_type 和 type_ref，不能只修一个字段。
不要引入新的 wrapper layer；用最小 surgical patch 集中修 canonicalization。
新增或更新 regression tests，覆盖 20260617_172331_047723 和 20260617_205742_685499 对应失败。
运行相关 unittest，检查 git diff，并更新 PIPELINE_INVARIANT_REPAIR_STATUS.md。
```

---

# 第 2 轮：calls_allowed 与 wire mapping

## 7.1 目标

完成 value_ref 规则统一 + wire coverage deterministic closure。

本轮直接覆盖：

```text
20260617_103648_329047: prose value_ref。
20260610_093743_377786: fixed-header-only wire mapping，payload fields 未覆盖。
```

这两个问题都属于：

```text
LLM 输出必须被结构化收口，不能把自然语言或局部 coverage 拖到 final readiness。
```

## 7.2 主要任务

### 7.2.1 Structured call binding

1. 统一 value_ref grammar：
   - caller params。
   - declared access paths。
   - local return bindings。
   - simple C literals。
   - validated C expressions。
2. 对齐 normalizer 与 final readiness：
   - `_structured_call_value_ref` 与 `_allowed_call_value_ref` 不能再出现规则漂移。
3. 优先使用 `allowed_value_bindings`：
   - natural language 如 `client ID`、`decoded packet`、`connection handle` 必须 blocking 或转 unresolved。
4. 明确 ternary expression 策略：
   - 要么支持 `?:` 并加 test。
   - 要么要求先绑定 local symbol，再传 local symbol。

### 7.2.2 Wire mapping coverage closure

1. 建立 `field_id -> coverage requirement`。
2. 对 LLM patch 做 deterministic completion：
   - 有明确 access path：`store_in_field`。
   - 最小实现暂不支持但可忽略：`parse_and_skip`，必须写 reason。
   - 必须拒绝：`reject_if_present`，必须写 rule。
   - 无法判断：产生 blocking unresolved question，stage 不得 pass。
3. 5.4d validator 与 final readiness 保持一致：
   - fixed-header-only patch 必须在 5.4d blocking。
   - final readiness 不应首次发现 `uncovered_wire_field`。

## 7.3 风险点

本轮需要小心：

```text
不要为了去掉 invalid prose value_ref 而误删有效 cross-module call edge。
不要用 empty calls_allowed 或 empty wire_mapping_table 制造 false pass。
```

## 7.4 涉及文件候选

```text
agent/planning/stages/implementation_plan_context.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/prompts/templates.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
```

## 7.5 完成标准

```text
[ ] client ID / decoded PUBLISH packet / connection handle 等 prose value_ref 在 5.4e blocking。
[ ] caller param / access path / local return binding 可以通过。
[ ] 5.4e normalizer 与 final readiness 对同一 value_ref 给出一致结果。
[ ] fixed-header-only patch 在 5.4d blocking。
[ ] CONNECT/SUBSCRIBE/PUBLISH payload fields 均有 mapping 或 unresolved。
[ ] skip/reject mappings 必须包含 reason/rule。
[ ] 相关 unittest 通过。
```

## 7.6 本轮执行提示词

```text
请执行 PIPELINE_INVARIANT_REPAIR_PLAN.md 的第 2 轮：calls_allowed 与 wire mapping。
目标是完成 value_ref 规则统一 + wire coverage deterministic closure。

请先对比 implementation_plan_merger.py 中 _structured_call_value_ref
和 implementation_plan_stages.py 中 _allowed_call_value_ref 的规则差异，
确保 5.4e normalizer、stage validator 和 final readiness 使用一致 contract。
自然语言如 client ID、decoded packet、connection handle 必须 blocking 或转 unresolved，不能 silent pass。

同时读取 planning_ir 的 message_model fields，建立 wire coverage requirement。
fixed-header-only patch 必须在 5.4d 阶段失败，不能拖到 final readiness。
新增 regression tests 覆盖 20260617_103648_329047 和 20260610_093743_377786。
运行相关 unittest，检查不要误删有效 call edge，并更新状态文档。
```

---

# 第 3 轮：runtime lifecycle + function name allocator

## 8.1 目标

完成 deployable target lifecycle obligations 前移，并加入 global function name collision repair。

本轮直接覆盖：

```text
20260616_194800_796436: runtime create/start/destroy lifecycle ids 缺失。
20260612_092317_251284: mqtt_transport_close duplicate function name。
```

这两个问题都在 function inventory 附近，适合放在同一轮。但它们会影响 file layout、runtime entrypoint、call contracts 的引用同步，因此本轮需要比前两轮更谨慎。

## 8.2 主要任务

### 8.2.1 Runtime lifecycle obligations 前移

1. 对 deployable target 的 key flow module 强制生成 lifecycle obligations：
   ```text
   runtime_create
   runtime_start
   runtime_run
   runtime_destroy
   ```
2. 在 5.4a function inventory 阶段补齐或 fail-closed。
3. `fallback_runtime_entrypoint()` 只复用已有 lifecycle API，不负责创造 lifecycle functions。
4. final readiness 继续检查 main entrypoint：
   - 只能调用 public lifecycle APIs。
   - source dependency 必须包含 lifecycle provider file。

### 8.2.2 FunctionNameAllocator

1. 建立 global function symbol table。
2. 检测 duplicate C symbol。
3. 保留稳定命名：
   - public API。
   - runtime lifecycle。
   - already canonical function。
4. 对 internal/helper/obligation-generated functions 使用 module prefix 或 deterministic suffix。
5. 同步更新引用：
   - `call_contracts`
   - `calls_allowed`
   - `resource_lifecycle`
   - runtime lifecycle refs
   - file layout refs

## 8.3 涉及文件候选

```text
agent/planning/stages/function_inventory_decomposition.py
agent/planning/stages/inventory_planning_space.py
agent/planning/stages/inventory_reconciliation.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
```

## 8.4 完成标准

```text
[ ] broker target 缺 create/start/destroy 时，5.4a 补齐或 blocking。
[ ] 5.5b runtime entrypoint candidate 不再出现 empty lifecycle ids。
[ ] main.c 只编排 existing public lifecycle APIs。
[ ] 两个 module 同时生成 mqtt_transport_close 时，repair 后 C symbol 唯一。
[ ] function_id、function name、call_contracts、calls_allowed、file layout refs 同步一致。
[ ] 相关 unittest 通过。
```

## 8.5 本轮执行提示词

```text
请执行 PIPELINE_INVARIANT_REPAIR_PLAN.md 的第 3 轮：runtime lifecycle + function name allocator。
目标是在 function inventory 附近一次性处理 deployable lifecycle obligations 和 global function symbol collision。

请先定位 function inventory planning space、decomposition、reconciliation、implementation_plan_merger 中的 lifecycle 与 symbol repair 逻辑。
broker/server/client key flow module 必须在 5.4a 后拥有 create/start/run/destroy public lifecycle APIs。
不要让 fallback_runtime_entrypoint 隐式创建 lifecycle functions；它只应选择和编排已有 API。

同时实现 global FunctionNameAllocator。
优先保持 public API 和 runtime lifecycle names 稳定；
对 internal/helper/obligation-generated functions 使用 module prefix 或 deterministic suffix。
必须同步更新所有引用，不能只改 function.name。
新增 regression tests 覆盖 20260616_194800_796436 和 20260612_092317_251284。
运行相关 unittest，并更新状态文档。
```

---

# 第 4 轮：C header surface closure

## 9.1 目标

完成 header dependency cycle 检查、public type owner closure、完整 compile diagnostics。

本轮直接覆盖：

```text
20260616_231307_313900: codec.h 与 protocol_codec.h 互相 include，
public type 分裂导致 rendered header compile failure。
```

这是独立大块，原因是它跨：

```text
planning coder_semantics
specs_compiler / coder_spec_lowering
coder specs.py / generation.py
```

repair 策略可能涉及 header 拆分或 common types header，不能和前几轮混在一起做。

## 9.2 主要任务

1. 检查 `HEADER.DEPENDENCY` graph：
   - 不允许 cycle。
   - 不允许 `codec.h <-> protocol_codec.h` 互相 include。
2. 检查 public type owner closure：
   - public signature 引用的 type 必须在当前 header 或 acyclic dependency 中按预处理顺序可见。
   - 不能只按集合可见性判断。
3. 设计 deterministic repair：
   - 合并同 module public types 到 single owner header。
   - 或生成 common types header，例如 `protocol_codec_types.h`。
   - 其他 public headers 单向 include common types header。
4. 完整 compile diagnostics：
   - 保存完整 stderr。
   - 保存 rendered header snapshot。
   - diagnostic 指出 cycle path、header path、signature/type ref。

## 9.3 涉及文件候选

```text
agent/planning/validators/coder_semantics.py
agent/planning/stages/specs_compiler.py
agent/planning/stages/coder_spec_lowering.py
agent/coder/specs.py
agent/coder/generation.py
agent/planning/tests/test_coder_schema_lowering.py
```

## 9.4 完成标准

```text
[ ] circular header fixture 在 planning/coder compatibility validator 阶段 blocking。
[ ] rendered header compile failure 不再首次发现 header cycle。
[ ] codec/protocol_codec public type split 可被 deterministic repair 消除，或以明确 blocking diagnostic 暴露。
[ ] 每个 generated header 可单独 #include 编译。
[ ] header compile diagnostic 保留完整 stderr。
[ ] 相关 unittest 通过。
```

## 9.5 本轮执行提示词

```text
请执行 PIPELINE_INVARIANT_REPAIR_PLAN.md 的第 4 轮：C header surface closure。
目标是在 specs_compile 前后证明 C public header surface closure，
尤其禁止 HEADER.DEPENDENCY cycle 和 public type owner split。

请复现 20260616_231307_313900 的 codec.h <-> protocol_codec.h circular include 问题，
并把它转为 regression fixture。
修改 coder_semantics/specs_compiler/coder_spec_lowering/coder specs 的最小必要逻辑，
让 planning compatibility 阶段就能 blocking 或 deterministic repair。
repair 策略可以选择 single owner header 或 common types header，但必须保持 public type refs 可解释。
保留完整 rendered header stderr，运行 coder schema lowering tests，并更新状态文档。
```

---

# 第 5 轮：端到端 regression/golden

## 10.1 目标

集中固化 7 个历史失败 fixtures，并增加 MQTT minimum golden。

前四轮可以边做边加小测试，但第 5 轮必须把 pipeline 的 invariant 防线串起来，证明：

```text
这些修复不是一组局部补丁，而是一条从 planning_ir 到 spec_bundle / coder validate 的稳定闭包。
```

## 10.2 主要任务

1. 整理 7 个历史失败 fixtures：
   - type alias closure。
   - byte_buffer raw alias。
   - prose value_ref。
   - fixed-header-only wire mapping。
   - runtime lifecycle missing。
   - duplicate function name。
   - circular header dependency。
2. 对每个 fixture 定义 invariant assert：
   - 不只 assert 旧 error 消失。
   - 还要 assert 新 artifact 满足目标闭包。
3. 增加 MQTT minimum golden：
   - fresh planning。
   - planning verify。
   - coder validate。
   - coder generate/compile。
   - minimum smoke，如当前环境允许。
4. 如果时间允许，扩展 CoAP/SMTP minimum planning compatibility run。
5. 输出失败分类统计：
   - prompt/LLM output。
   - validator rule。
   - deterministic repair。
   - missing fact/open assumption。
   - coder generation gap。

## 10.3 涉及文件候选

```text
agent/planning/tests/
agent/planning/out/
agent/out/
docs/PIPELINE_INVARIANT_REPAIR_STATUS.md
```

## 10.4 完成标准

```text
[ ] 7 个历史 failure replay tests 全部通过。
[ ] MQTT minimum fresh planning 至少 1 次通过 specs_compile。
[ ] 通过 final readiness 的 spec_bundle 不在 coder loader/header compile 阶段失败。
[ ] MQTT minimum coder compile/smoke 有明确结果。
[ ] 所有失败都有明确分类 diagnostic。
[ ] 状态文档记录 run directory、commands、diagnostics、结论。
```

## 10.5 本轮执行提示词

```text
请执行 PIPELINE_INVARIANT_REPAIR_PLAN.md 的第 5 轮：端到端 regression/golden。
目标是把前四轮修复集中固化为 regression fixtures，并增加 MQTT minimum golden。

请整理 7 个历史有效失败，每个失败至少有一个 replay fixture 或 invariant check。
不要只 assert 旧 error 消失，还要 assert 新 artifact 满足对应 invariant。
随后运行 MQTT minimum fresh planning、planning verify、coder validate、coder generate/compile 和 minimum smoke。
如果 MQTT 通过，再扩展 CoAP minimum 和 SMTP minimum planning compatibility run。
所有失败都必须分类到 prompt/LLM output、validator rule、deterministic repair、missing fact/open assumption 或 coder generation gap。
记录 run directories、commands、diagnostics 和结论，更新 PIPELINE_INVARIANT_REPAIR_STATUS.md。
```

## 11. 执行顺序与记录要求

固定顺序：

```text
第 1 轮 -> 第 2 轮 -> 第 3 轮 -> 第 4 轮 -> 第 5 轮
```

每轮结束必须更新：

```text
docs/PIPELINE_INVARIANT_REPAIR_STATUS.md
```

记录内容至少包括：

```text
1. 执行日期。
2. 当前轮次。
3. 修改文件。
4. 运行命令。
5. 通过测试。
6. 失败测试。
7. 新增 diagnostics。
8. 未解决风险。
9. 下一轮入口提示词。
```

## 12. 不做事项

本计划期间避免：

```text
1. 为单个 run 增加 protocol-specific hardcode，除非它是明确的 protocol fact lowering rule。
2. 通过清空 calls_allowed / wire_mapping / dependency edges 制造 false pass。
3. 关闭 strict validator、rendered header validation 或 coder compatibility gate。
4. 把旧计划 CURRENT_TASK_STATUS.md 的 Step 1-6 与本五轮计划混写。
5. 把 targeted resume 成功冒充为 fresh-run 成功。
```

