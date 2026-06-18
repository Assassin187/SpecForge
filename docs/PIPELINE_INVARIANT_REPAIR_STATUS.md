# SpecForge Planning Pipeline Invariant Repair 任务状态书

更新时间：2026-06-18

## 1. 当前状态

当前计划：

```text
PIPELINE_INVARIANT_REPAIR_PLAN.md
```

当前阶段：

```text
第 1 轮尚未执行。
```

当前目标：

```text
把近 8 次 MQTT planning run 暴露的问题，从逐个 error 修补转为 5 轮 pipeline invariant repair。
```

与旧状态文件的区别：

```text
docs/CURRENT_TASK_STATUS.md 记录旧 planning stabilization 路线。
本文档只记录 pipeline invariant repair 五轮新计划。
不要把本计划第 1-5 轮与旧计划 Step 1-6 混写。
```

## 2. 五轮任务看板

| 轮次 | 名称 | 状态 | 当前结论 |
|---|---|---|---|
| 第 1 轮 | 高收益低耦合：type canonicalization + 5.3 fixtures | not_started | 等待修复 string/buffer/byte_buffer alias closure |
| 第 2 轮 | calls_allowed 与 wire mapping | not_started | 等待统一 value_ref contract 与 wire coverage closure |
| 第 3 轮 | runtime lifecycle + function name allocator | not_started | 等待前移 lifecycle obligations 并修复 symbol collision |
| 第 4 轮 | C header surface closure | not_started | 等待修复 header cycle / public type owner split |
| 第 5 轮 | 端到端 regression/golden | not_started | 等待集中固化 7 个历史失败 fixtures 与 MQTT golden |

状态枚举：

```text
not_started
in_progress
blocked
completed
deferred
```

## 3. 历史失败类别映射

| 类别 | 代表 run | 说明 | 目标轮次 |
|---|---|---|---|
| type alias closure | `20260617_172331_047723`, `20260617_205742_685499` | `string_view` / `buffer_view` / `byte_buffer` 未 canonicalize | 第 1 轮 |
| prose value_ref | `20260617_103648_329047` | `client ID` 等自然语言绑定穿过 5.4e | 第 2 轮 |
| wire coverage missing | `20260610_093743_377786` | payload fields 未进入 wire mapping | 第 2 轮 |
| runtime lifecycle missing | `20260616_194800_796436` | create/start/destroy lifecycle ids 缺失 | 第 3 轮 |
| function symbol collision | `20260612_092317_251284` | `mqtt_transport_close` duplicate name | 第 3 轮 |
| header dependency cycle | `20260616_231307_313900` | `codec.h` 与 `protocol_codec.h` 互相 include | 第 4 轮 |
| full invariant replay | 全部 7 个有效失败 | 集中 replay/golden | 第 5 轮 |

## 4. 第 1 轮状态：高收益低耦合

状态：

```text
not_started
```

目标：

```text
完成 type canonicalization + 5.3 regression fixtures。
```

任务清单：

```text
[ ] 建立集中 alias registry。
[ ] 修改 normalize_protocol_field_type。
[ ] 修改 inventory reconciliation alias 处理。
[ ] 确保 field_type 与 type_ref 同步 canonicalize。
[ ] 确保 protocol-specific view_struct slots 稳定生成。
[ ] 增加 string_view / buffer_view / byte_buffer regression tests。
[ ] 运行相关 unittest。
```

完成标准：

```text
[ ] string_view / buffer_view / byte_buffer fixture 均生成 declared protocol view type。
[ ] final type inventory 不含 raw string_view / buffer_view / byte_buffer public field type。
[ ] unknown_type_ref 不再由 abstract view alias 触发。
[ ] declared view structs 可 lower 到 coder header data。
```

本轮入口提示词：

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

## 5. 第 2 轮状态：calls_allowed 与 wire mapping

状态：

```text
not_started
```

目标：

```text
完成 value_ref 规则统一 + wire coverage deterministic closure。
```

任务清单：

```text
[ ] 对齐 _structured_call_value_ref 与 _allowed_call_value_ref。
[ ] allowed_value_bindings 成为主要 binding 来源。
[ ] prose value_ref 在 5.4e blocking 或转 unresolved。
[ ] 明确 ternary expression 支持或改写策略。
[ ] 建立 planning_ir field coverage requirement。
[ ] fixed-header-only patch 在 5.4d blocking。
[ ] deterministic conservative mapping completion。
[ ] unresolved question 不得与 stage pass 同时出现。
[ ] 增加 prose value_ref 与 uncovered_wire_field replay tests。
[ ] 运行相关 unittest。
```

完成标准：

```text
[ ] client ID / decoded PUBLISH packet / connection handle 等 prose value_ref 在 5.4e blocking。
[ ] caller param / access path / local return binding 可以通过。
[ ] 5.4e normalizer 与 final readiness 对同一 value_ref 给出一致结果。
[ ] fixed-header-only patch 在 5.4d blocking。
[ ] CONNECT/SUBSCRIBE/PUBLISH payload fields 均有 mapping 或 unresolved。
[ ] skip/reject mappings 必须包含 reason/rule。
```

本轮入口提示词：

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

## 6. 第 3 轮状态：runtime lifecycle + function name allocator

状态：

```text
not_started
```

目标：

```text
完成 deployable target lifecycle obligations 前移，并加入 global function name collision repair。
```

任务清单：

```text
[ ] key flow module 识别逻辑复核。
[ ] broker/server/client deployable target lifecycle obligations 生成。
[ ] 5.4a deterministic seed 或 fail-closed。
[ ] runtime_entrypoint 只复用 existing lifecycle API。
[ ] 设计 global function symbol table。
[ ] 对 duplicate names 做 deterministic rename。
[ ] 保留 public API / runtime lifecycle stable names。
[ ] 同步更新 call_contracts / calls_allowed / lifecycle / file layout refs。
[ ] 增加 missing lifecycle 与 duplicate function name regression tests。
[ ] 运行相关 unittest。
```

完成标准：

```text
[ ] broker target 缺 create/start/destroy 时，5.4a 补齐或 blocking。
[ ] 5.5b runtime entrypoint candidate 不再出现 empty lifecycle ids。
[ ] main.c 只编排 existing public lifecycle APIs。
[ ] 两个 module 同时生成 mqtt_transport_close 时，repair 后 C symbol 唯一。
[ ] function_id、function name、call_contracts、calls_allowed、file layout refs 同步一致。
```

本轮入口提示词：

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

## 7. 第 4 轮状态：C header surface closure

状态：

```text
not_started
```

目标：

```text
完成 header dependency cycle 检查、public type owner closure、完整 compile diagnostics。
```

任务清单：

```text
[ ] HEADER.DEPENDENCY graph cycle 检查。
[ ] public signature type visibility 按预处理顺序检查。
[ ] 设计 common types header 或 single owner header repair。
[ ] circular include diagnostics 指出 cycle path。
[ ] 保留 rendered header compile 完整 stderr。
[ ] 增加 codec.h <-> protocol_codec.h regression fixture。
[ ] 运行 coder schema lowering tests。
```

完成标准：

```text
[ ] circular header fixture 在 planning/coder compatibility validator 阶段 blocking。
[ ] rendered header compile failure 不再首次发现 header cycle。
[ ] codec/protocol_codec public type split 可被 deterministic repair 消除，或以明确 blocking diagnostic 暴露。
[ ] 每个 generated header 可单独 #include 编译。
[ ] header compile diagnostic 保留完整 stderr。
```

本轮入口提示词：

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

## 8. 第 5 轮状态：端到端 regression/golden

状态：

```text
not_started
```

目标：

```text
集中固化 7 个历史失败 fixtures，并增加 MQTT minimum golden。
```

任务清单：

```text
[ ] 整理 7 个历史 failure replay tests。
[ ] 每个 fixture assert 对应 invariant，而不只是旧 error 消失。
[ ] MQTT minimum fresh planning。
[ ] planning verify。
[ ] coder validate。
[ ] coder generate/compile。
[ ] minimum smoke，如当前环境允许。
[ ] CoAP/SMTP minimum planning compatibility run，如时间允许。
[ ] 失败分类统计。
```

完成标准：

```text
[ ] 7 个历史 failure replay tests 全部通过。
[ ] MQTT minimum fresh planning 至少 1 次通过 specs_compile。
[ ] 通过 final readiness 的 spec_bundle 不在 coder loader/header compile 阶段失败。
[ ] MQTT minimum coder compile/smoke 有明确结果。
[ ] 所有失败都有明确分类 diagnostic。
[ ] 状态文档记录 run directory、commands、diagnostics、结论。
```

本轮入口提示词：

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

## 9. 状态更新规则

每轮执行后必须追加：

```text
执行日期：
执行轮次：
修改文件：
运行命令：
通过结果：
失败结果：
新增 diagnostics：
未解决风险：
下一轮建议：
```

不要把旧计划 `CURRENT_TASK_STATUS.md` 的 Step 1-6 历史复制到本文档。这里只记录 invariant repair 五轮新计划。

## 10. 最近执行记录

### 2026-06-18：五轮计划重排

执行内容：

```text
将原 Step 0-8 计划重排为 5 个递进轮次。
尚未执行代码修改。
尚未运行测试。
```

修改文件：

```text
docs/PIPELINE_INVARIANT_REPAIR_PLAN.md
docs/PIPELINE_INVARIANT_REPAIR_STATUS.md
```

下一步：

```text
从第 1 轮：高收益低耦合开始。
```

