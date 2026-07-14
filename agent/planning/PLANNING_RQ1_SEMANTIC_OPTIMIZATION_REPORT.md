# Planning RQ1 产出统计与语义优化分析报告

> 版本：2026-07-13
> 对象：MQTT minimum broker profile
> 样本：6 份 candidate protocol specs 及其 no-repair coder 输出
> 用途：为下一轮 planning semantic optimization 提供可复核的证据、优先级和验收门槛

## 1. 执行摘要

本轮结果证明了一个清晰但有限的进展：planning 已经实现 evaluation artifact stability，6/6 protocol specs 都可被现有 coder loader 加载，最终验收序列也达到连续 3 次独立 fresh 成功；但是这些结构稳定的 artifacts 尚未转化为 implementation utility。6/6 coder generation 完成，0/6 initial compile 通过，runtime 与 behavior verification 均未执行。

当前最准确的研究结论是：

> SpecForge planning 已经跨过 schema、discovery 和 artifact materialization 门槛，但尚未跨过 type/dependency closure、ownership/access grounding 和 protocol-model grounding 门槛。Coder-loadable 是必要条件，不是 implementation-usable 的充分条件。

对下一步优化最重要的统计信号如下：

- 6 个 run 共生成 141 个 FUNCTION_SPEC，其中 100 个使用 generic logic template，占 70.9%。
- 只有 9/141 个 function 有非空 PRECONDITION，10/141 有非空 POSTCONDITION，0/141 有 INVARIANTS_USED、function-level ACCESS_PATHS 或 function-level FORBIDDEN_SYMBOLS。
- 141 个 function 中只有 17 个在 planning artifact 中具有非空 wire_mapping，占 12.1%；所有 plan enum 的 values 总数为 0。
- 177 条跨文件 call edge 中有 53 条没有对应 source dependency，占 29.9%；32 次 foreign custom type 使用中有 5 次缺少 header dependency，占 15.6%。
- 4 份 specs 生成空 mqtt_packet_type_t enum；另 1 份把该 packet type lower 为 OPAQUE incomplete type。
- 21 个 function signature 按值使用 OPAQUE type。该指标与 root causes/source kLoC 的探索性 Spearman 相关系数为 0.88，但样本只有 6 个，不能当作确定性因果结论。
- Coder 只定义了 103/141 个 specified functions，缺失 38 个，占 27.0%；另有 7 个 source file 含 placeholder，10 个 source file 基本为空。
- 6 个 coder 项目共有 661 个 pre-repair root-cause instances：C2 为 344，占 52.0%；C5 为 188，占 28.4%。两类合计占 80.5%。
- M2 initial compile 为 0/6，与 M0 和 M1 的 0/10 相同；M2 root causes/source kLoC 中位数为 61.2，差于 M0 的 45.5，略好于 M1 的 63.0。

因此，下一轮不应优先继续扩充模块或 function 数量，也不应以降低 diagnostics 数量为主要目标。P0 应集中在：

1. packet constants、wire mapping、type kind 和 pointer/value ABI grounding；
2. cross-file dependency、access provider、callback 与 direct call 关系闭包；
3. runtime entrypoint、constructor/destructor 和 FUNCTION_SPEC 到 definition 的完整性。

只有这些门槛稳定通过后，才值得投入 behavior detail、test vector 和 token efficiency 优化。

## 2. 分析问题与边界

本报告回答四个问题：

1. 6 次 planning 实际产出了什么，样本是否都可视为独立 fresh？
2. protocol specs 的结构完整度、语义密度和 traceability 达到什么水平？
3. 哪些 planning/specs 缺口实际传播成了 coder 的 compile failures？
4. 下一轮应如何设定可检验的优化目标，才能支撑超过 M0 FS-Direct-Coder 和 M1 NL-Plan-Code 的论文主张？

本报告只分析已经保存的 artifacts，不修改：

- protocol facts；
- target profile；
- coder、coder schema；
- specs-example；
- protocol implementation source；
- 6 份 planning artifacts；
- 6 份 generated C/H。

Coder 采用 generation-only、skip_repair=true、max_repair_rounds=0。所有 compile、header self-check、source syntax-check 和 C1–C5 分类均为只读诊断。因此，以下结果测量的是 planning artifacts 的 pre-repair downstream utility，不是 coder repair capability。

## 3. 数据集与可复现证据

### 3.1 六个 planning artifact

| Run | 当前 manifest | Stage survival | Specs M/F/Fn | Qualification | Coder loader |
| --- | --- | ---: | ---: | --- | --- |
| mqtt_evaluation_acceptance_fresh_01_20260713 | fresh | 11/11 | 1/8/0 | Failed | Passed |
| mqtt_evaluation_acceptance_fresh_02_20260713 | fresh | 11/11 | 1/6/24 | Failed | Passed |
| mqtt_evaluation_acceptance_fresh_03_20260713 | resume 覆盖 | 11/11 | 1/7/25 | Failed | Passed |
| mqtt_evaluation_acceptance_r2_fresh_01_20260713 | fresh | 11/11 | 1/6/30 | Failed | Passed |
| mqtt_evaluation_acceptance_r2_fresh_02_20260713 | fresh | 11/11 | 1/6/30 | Failed | Passed |
| mqtt_evaluation_acceptance_r2_fresh_03_20260713 | fresh | 11/11 | 1/8/32 | Failed | Passed |

六份现存 artifacts 中只有 5 份仍有 fresh=true、resume=false 证据。第一组 fresh_03 的目录和 manifest 已被 resume 覆盖，因此它可用于 artifact/content failure analysis，但不能作为独立 fresh 样本进入论文 fresh success-rate 分母。

最终 artifact-stability 验收应只指 r2 的三次连续 fresh：

- r2_fresh_01：11/11 stages，16/16 partitions，coder loader Passed；
- r2_fresh_02：11/11 stages，17/17 partitions，coder loader Passed；
- r2_fresh_03：11/11 stages，18/18 partitions，coder loader Passed。

### 3.2 Coder 与分析证据

无修复 coder 输出位于：

evaluation/planning_utility/out/rq1_planning_artifact_coder_no_repair_20260713

关键证据：

- REPORT.md：上一轮 coder utility pilot 总结；
- summary.json：6 个 M2 run 的 generation 与 pre-repair 聚合；
- analysis_summary.json：confidence interval、逐 run completeness 与统计摘要；
- baseline_comparison.json：M0/M1 历史 no-repair run 的同 classifier 重算；
- planning_semantic_optimization_analysis.json：本报告使用的 planning/spec/code 对齐统计；
- 每个 run 下的 pre_repair_diagnostics.json、behavior.json、summary.json；
- 每个 coder_out/_agent_logs/run_manifest.json。

Planning 原始证据位于每个 run 的：

- _planning/run_manifest.json；
- _planning/diagnostics.json；
- _planning/semantic_closure/implementability_report.json；
- _planning/implementation_plan.json；
- _candidate/specs/。

### 3.3 RQ 编号一致性

当前 evaluation/planning_utility/README.md 标题是 RQ1: MQTT Planning Utility Evaluation。本轮报告沿用 RQ1。论文正文、实验目录和其他历史文档仍应再做一次编号审计，避免 RQ1/RQ3 混用。

## 4. 六个 run 的端到端统计

| Run | Types | Function specs | Defined/spec | Source LoC | Planning tokens | Coder tokens | Initial compile | C1/C2/C3/C4/C5 | Roots |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| fresh_01 | 0 | 0 | 0/0 | 17 | 110,841 | 13,144 | Failed | 0/0/1/0/0 | 1 |
| fresh_02 | 11 | 24 | 15/24 | 1,159 | 308,642 | 39,364 | Failed | 0/32/10/8/14 | 64 |
| fresh_03 resume | 8 | 25 | 17/25 | 1,511 | 368,382 | 42,428 | Failed | 12/46/2/2/34 | 96 |
| r2_fresh_01 | 8 | 30 | 20/30 | 1,505 | 429,947 | 43,980 | Failed | 2/30/14/12/110 | 168 |
| r2_fresh_02 | 13 | 30 | 20/30 | 2,370 | 483,783 | 51,982 | Failed | 24/72/8/14/16 | 134 |
| r2_fresh_03 | 12 | 32 | 31/32 | 1,051 | 455,323 | 54,503 | Failed | 16/164/2/2/14 | 198 |

注：fresh_03 的 planning token 使用当前保存 artifact 的记录；它是 resume 覆盖样本，不与 fresh token 分布混合解释。

### 4.1 不应误读的两个 run

fresh_01 只有 1 个 root cause，不代表它质量最好。该 run 有 8 个 FILE_SPEC、0 个 FUNCTION_SPEC、0 个 type、0 个 test vector；coder 生成 8 个近空 source，共 17 行，最终在 link 阶段缺少 main。低错误数来自任务空化。

r2_fresh_03 定义了 31/32 个 specified functions，coverage 达 96.9%，但有最多的 198 个 root-cause instances。它证明 function inventory 和 implementation coverage 提升不等于 ABI correctness：该 run 大量按值传递 incomplete mqtt_session_t 和 mqtt_connection_t，并缺少跨文件 dependencies。

### 4.2 Artifact stability 与 semantic utility 分离

| 层次 | 本轮结果 | 结论 |
| --- | --- | --- |
| Stage completion | 6/6 为 11/11 | 稳定 |
| Specs materialization | 6/6 | 稳定 |
| Coder loader | 6/6 | 稳定 |
| Semantic qualification | 0/6 | 未达到 |
| Coder generation | 6/6 | 稳定 |
| Initial compile | 0/6 | 未达到 |
| Runtime start | 0/6 | 未执行 |
| Required behavior | 0/30，均 upstream not-run | 无行为证据 |

这一区分必须保留到论文表述中。6/6 loader pass 只能支持 artifact stability，不支持 implementation utility。

## 5. Planning semantic diagnostics

### 5.1 全量 diagnostics 聚合

6 个 _planning/diagnostics.json 共记录 252 项：

| Diagnostic code | Count | 占比 |
| --- | ---: | ---: |
| access_service_missing | 75 | 29.8% |
| callee_dependency_missing | 70 | 27.8% |
| cross_file_private_function | 53 | 21.0% |
| module_generation_order_conflict | 10 | 4.0% |
| opaque_constructor_missing | 10 | 4.0% |
| opaque_destructor_missing | 6 | 2.4% |
| unresolved_partition | 5 | 2.0% |
| unresolved_type | 5 | 2.0% |
| foreign_type_dependency_missing | 5 | 2.0% |
| module_dependency_conflict | 3 | 1.2% |
| semantic_patch_invalid | 3 | 1.2% |
| runtime_entrypoint_missing | 2 | 0.8% |
| callback_signature_mismatch | 2 | 0.8% |
| callback_provider_missing | 2 | 0.8% |
| coder_missing_test_vectors | 1 | 0.4% |

前三类合计 198/252，占 78.6%。这说明主要问题已经不是 JSON schema 或 loader dialect，而是值从哪里来、调用如何跨文件可见、跨文件调用是否真的合法。

### 5.2 Semantic closure 计数不能代表最终质量

各 run 的全量 diagnostics 总数为 3、48、38、23、68、72；中位数为 43。最后一个 r2_fresh_03 的 semantic closure final 只记录 3 个 unresolved partitions，但最终 diagnostics.json 有 72 项。

原因是 unresolved partition 路径可跳过或短路 semantic closure，之后 validate_planning_run 仍会发现大量 implementability 问题。因此：

- semantic_closure.final_diagnostics 只描述 closure 路径的 residual；
- diagnostics.json 才是 publication/readiness 判断应使用的并集；
- semantic diagnostic count 下降不能单独作为优化成功指标。

探索性分析也支持这一点：diagnostic count 与 raw roots 的 Spearman rho 为 0.60，但与 roots/source kLoC 的 rho 只有 0.086。样本量 n=6，数值只用于提出假设。

## 6. Protocol specs 的结构与语义密度

### 6.1 Inventory 稳定性

| Metric | 范围或总量 |
| --- | ---: |
| Modules/run | 5–6 |
| FILE_SPEC/run | 6–8 |
| FUNCTION_SPEC/run | 0–32 |
| Types/run | 0–13 |
| FUNCTION_SPEC total | 141 |
| Function test vectors | 188 |
| RELY.FUNC edges | 204 |
| CALL_CONTRACTS | 204 |

RELY.FUNC 和 CALL_CONTRACTS 数量完全相等，说明结构派生是一致的。但这不表示调用关系真实或可实现：call direction、provider、visibility 和 dependency 仍可能错误。

### 6.2 Behavior contract 密度

| Metric | Count | Rate |
| --- | ---: | ---: |
| Generic logic template | 100/141 | 70.9% |
| Nonempty PRECONDITION | 9/141 | 6.4% |
| Nonempty POSTCONDITION | 10/141 | 7.1% |
| Nonempty INVARIANTS_USED | 0/141 | 0% |
| Function ACCESS_PATHS | 0/141 | 0% |
| Function FORBIDDEN_SYMBOLS | 0/141 | 0% |
| Function test vectors | 188 | 1.33/function |
| Empty test inputs | 43/188 | 22.9% |

43 个 empty-input test vectors 全部来自 r2_fresh_03，占该 run 48 个 function vectors 中的 43 个。数量增长没有转化为可执行输入。

当前 specs 更像 function inventory 加弱 behavior prose，而不是完整 engineering contract。尤其缺少：

- state transition 前置条件和后置条件；
- buffer/length、ownership、lifetime 和 failure-path invariants；
- handle 如何取得的 access path；
- function 层 forbidden behavior；
- 可重放的 packet bytes、state setup 和 expected state/output。

### 6.3 Wire 与 protocol constant grounding

Planning implementation plan 中只有 17/141 个 function 的 wire_mapping 非空，占 12.1%。逐 run 为：

| Run | Nonempty wire_mapping |
| --- | ---: |
| fresh_01 | 0/0 |
| fresh_02 | 2/24 |
| fresh_03 resume | 5/25 |
| r2_fresh_01 | 5/30 |
| r2_fresh_02 | 5/30 |
| r2_fresh_03 | 0/32 |

所有 run 的 plan enum values 总数都是 0。4 份 specs 因而出现空 mqtt_packet_type_t enum；r2_fresh_01 则把 packet type 表示为 OPAQUE incomplete type。

这直接传播到 generated code：

- r2_fresh_02 出现 case /* CONNECT */:、case /* SUBSCRIBE */:、case /* PUBLISH */: 等不可编译 placeholder；
- 代码中出现 whatever the CONNECT packet type is、not implemented 等未 grounded 文本；
- r2_fresh_01 的 callback 按值接收 incomplete mqtt_packet_type_t，转换和比较均无法形成合法 C ABI。

这是最接近 C5 protocol-model drift 的直接 planning 证据。

### 6.4 Type、ownership 与 ABI

| Metric | Count |
| --- | ---: |
| Empty enums | 4 |
| Empty structs | 0 |
| OPAQUE by-value signature uses | 21 |
| Callback void * slots | 12 |
| Measured plan→spec dropped struct fields | 0 |

需要区分已观察事实和机制风险：

- 本次逐字段比较没有发现 struct member 在 plan→spec lowering 中被删除，dropped_fields=0；因此不能把 6 个 run 的失败归因于已实证的 member omission。
- compiler 仍有过滤依赖和 unresolved member 的机制风险，但这批数据的直接主因是上游 type kind、enum value、pointer/value、constructor/destructor 和 access provider 不闭合。

r2_fresh_03 中典型错误是：

- typedef struct mqtt_session mqtt_session_t 后，signature 仍按值传入或返回 mqtt_session_t；
- mqtt_connection_t 同样以 incomplete value 出现在 transport/broker API；
- generated broker code 在 session_id 与 session/connection handle 之间进行 intptr_t cast，暴露 ownership 和 access semantics 缺失。

OPAQUE by-value 次数与 roots/source kLoC 的探索性 Spearman rho 为 0.88，是本轮最值得在更大样本复验的预测指标。

### 6.5 Dependency closure

| Dependency metric | Missing/total | Rate |
| --- | ---: | ---: |
| Cross-file call source dependency | 53/177 | 29.9% |
| Foreign custom type header dependency | 5/32 | 15.6% |

缺失高度集中：

- fresh_02：22/36 cross-file call edges 缺 source dependency；
- r2_fresh_03：31/34 cross-file call edges 缺 source dependency；
- r2_fresh_03：5/7 foreign type uses 缺 header dependency；
- 其他 run 的上述两个 measured rate 为 0，但仍可能存在 private callee、反向 edge 或 access provider 问题。

Compiler 的 module spec 会过滤 generation order 之后的 dependencies，以满足 coder-facing order constraint。该行为实现了 loader stability，但不能修复源 call edge。最终 utility 路径必须回到 Stage 8/10 修复关系，不能把必要 dependency 的消失当作 semantic success。

### 6.6 Traceability

141/141 个 planned functions 都有非空 trace_refs，这是当前优势。所有 functions 的 decision_refs 和 rule_refs 都为空。

Module DOC_REF 只有 r2_fresh_02 为 6/6，其余 run 为 0；FILE_SPEC 的 DOC_REF 则全部非空。

结论是：

- fact-level identity trace 已经存在；
- engineering decision 和 rule grounding 基本缺失；
- 当前 traceability 能回答 function 来自哪些 fact，却不能稳定回答为什么采用该 type、ownership、call direction、wire mapping 或 lifecycle 设计。

下一轮不需要先增加更多 TRACE_ID，而应让高风险 engineering decisions 绑定 decision_refs/rule_refs，并让 validator 检查引用对应的约束是否真的物化。

## 7. Specs 到 generated code 的传播链

| Planning/spec 缺口 | Generated code 表现 | 主要分类 |
| --- | --- | --- |
| 0 FUNCTION_SPEC、缺 main | 近空 source，link 缺 main | C3 |
| Empty packet enum、缺 wire constants | 注释化 case label、占位 packet type | C5/C1 |
| OPAQUE type 按值使用 | incomplete type parameter/return、非法 cast | C4/C3 |
| Callee dependency 缺失 | implicit declaration、unknown type、跨文件不可见 | C2 |
| Cross-file private function | coder 调用不可公开访问的 symbol | C2/C3 |
| Access provider 缺失 | 伪造 handle、integer/pointer cast、错误调用链 | C4/C5 |
| Callback 被表达成 direct call edge | 反向 dependency、signature/provider mismatch | C3/C5 |
| Generic behavior、空 input test | placeholder、默认 return、无可执行语义约束 | C5 |
| FUNCTION_SPEC 未强制 definition | 38 个 specified function 缺 implementation | C3 |

### 7.1 Generated code completeness

| Metric | Aggregate |
| --- | ---: |
| Specified functions | 141 |
| Defined specified functions | 103 |
| Missing definitions | 38，27.0% |
| Source files with placeholder markers | 7 |
| Essentially empty source files | 10 |

除 fresh_01 的空 inventory 外，逐 run definition coverage 为：

- fresh_02：15/24，62.5%；
- fresh_03 resume：17/25，68.0%；
- r2_fresh_01：20/30，66.7%；
- r2_fresh_02：20/30，66.7%；
- r2_fresh_03：31/32，96.9%。

因此，下一轮 evaluation 必须增加 deterministic post-generation completeness gate：每个 required FUNCTION_SPEC 必须有唯一 matching definition；placeholder 和近空 source 必须单独失败。否则 generation_completed 会掩盖任务空化或漏实现。

### 7.2 C1–C5 分布

RQ1 taxonomy：

- C1：local C/build defects；
- C2：header/declaration visibility；
- C3：API contract drift；
- C4：type/ownership drift；
- C5：protocol model drift。

6 个 run 聚合：

| Category | Count | Share |
| --- | ---: | ---: |
| C1 | 54 | 8.2% |
| C2 | 344 | 52.0% |
| C3 | 37 | 5.6% |
| C4 | 38 | 5.7% |
| C5 | 188 | 28.4% |
| Total | 661 | 100% |

C1–C5 是 diagnostic instances，不是人工去重后的独立 semantic bugs。它们适合描述失败形态，不宜解释为精确 bug 数或因果 effect size。

尽管 C3 中位数低于 M0/M1，不能据此断言 structured specs 已经改善 API contracts：fresh_01 的空任务和 r2_fresh_03 的 incomplete type errors都会改变 category 分配。真正的 endpoint 仍是 0/6 compile。

## 8. 与 RQ1 两条基线比较

| Metric | M0 FS-Direct-Coder | M1 NL-Plan-Code | M2 candidate specs |
| --- | ---: | ---: | ---: |
| n | 10 | 10 | 6，其中 5 fresh |
| Generation completion | 10/10 | 10/10 | 6/6 |
| Initial compile | 0/10 | 0/10 | 0/6 |
| E2E success | 0/10 | 0/10 | 0/6 |
| Root causes median | 48 | 90 | 115 |
| Roots/source kLoC median | 45.5 | 63.0 | 61.2 |
| C1 median | 6 | 6 | 7 |
| C2 median | 1 | 6.5 | 39 |
| C3 median | 35 | 24 | 5 |
| C4 median | 1 | 37 | 5 |
| C5 median | 3 | 10 | 15 |
| Coder tokens median | 29,462.5 | 42,958 | 43,204 |
| Source LoC median | 1,011 | 1,218 | 1,332 |

### 8.1 当前能否支撑论文

可以支撑：

- planning artifact stability 的工程结果；
- coder-loadable 与 implementation-usable 之间存在 semantic gap；
- candidate-only pilot 的 negative result 和 failure mechanism；
- 下一轮 semantic qualification 设计动机。

不能支撑：

- M2 提高 compile、behavior 或 E2E success；
- structured protocol specs 优于 NL plan 或 direct coding；
- planning token 开销换来更高 successful implementation yield；
- 这 6 个 candidate-only runs 代表 publication-ready Full-SpecForge。

### 8.2 当前效应方向

M2 相对 M0：

- compile 和 E2E 没有提升；
- normalized root causes 更差，61.2 对 45.5；
- coder token 中位数高约 46.6%；
- C3 较低，但 C2、C4、C5 都更高。

M2 相对 M1：

- compile 和 E2E 没有提升；
- normalized root causes 略好，61.2 对 63.0，但没有成功 endpoint 支撑；
- coder token 接近；
- C3/C4 低，C2/C5 高。

目前最合理的解释不是 structured specs 无效，而是现有 specs 把接口外形结构化了，却没有把 type visibility、ownership、wire constants 和 provider/call semantics 同步结构化。

### 8.3 有效性威胁

- 样本只有 6 个，其中只有 5 个真实 fresh；
- M0/M1 来自 2026-07-03，M2 来自 2026-07-13，不是同日随机化；
- 当前 worktree 非 publication clean revision；
- 6 个 M2 全部 qualification_passed=false；
- 所有方法 initial compile 都为 0，成功率 endpoint 没有变异；
- behavior 全部未执行；
- 单协议 MQTT minimum profile，不能外推到其他协议；
- 相关性分析 n=6，只能生成假设。

## 9. 根因判断与置信度

| 判断 | 证据 | 置信度 |
| --- | --- | --- |
| Artifact lowering 已稳定 | 6/6 loader pass，r2 连续 3 fresh | 高 |
| 当前首要瓶颈是 dependency/access closure | 78.6% planning diagnostics 属于 access、callee dependency、cross-file private；C2 占 coder roots 52.0% | 高 |
| Packet constant/wire grounding 是 C5 核心来源 | enum values 全为 0，wire_mapping 17/141，实际出现注释 case label | 高 |
| OPAQUE by-value 是高风险 ABI smell | 21 次实际出现，rho=0.88，incomplete value compile errors | 中高 |
| Function 数量增加会改善质量 | r2_fresh_03 coverage 96.9% 但 roots 最高 | 反证充分 |
| Diagnostics 数量下降代表 downstream 改善 | normalized rho=0.086，r2_fresh_03 closure 3 但 total 72、roots 198 | 反证充分 |
| Compiler member omission 是本批主因 | measured dropped_fields=0 | 低，不支持 |
| Structured specs 已改善 C3 | C3 median 较低，但 endpoint 失败且分类受空任务影响 | 低到中，需要新实验 |

## 10. 下一轮语义优化路线

### P0-1：Type、wire constant 与 ABI grounding

目标：在 function design 之前形成可编译、可引用、语义有据的 canonical type model。

最小修改方向：

1. Stage 4 public artifact inventory 必须显式列出 packet/message constants、runtime entrypoint、public lifecycle artifacts。
2. Stage 5 对 ENUM 要求非空 values；值必须来自 protocol facts 或可追溯 engineering rule，不能由 compiler 发明。
3. Stage 5 对 OPAQUE 建立单一 pointer policy：OPAQUE 只能通过 pointer/handle 传递，除非有完整 concrete definition。
4. Stage 5/6 联合验证 RETURN、PARAMS、callback signature 的 pointer/value 一致性。
5. 每个 OPAQUE resource 必须有明确 constructor/destructor 或外部 ownership declaration。
6. Codec、packet dispatch、parser/encoder function 必须有 nonempty wire_mapping，覆盖 packet type、fixed header、remaining length 和必要 field constraints。

验收门槛：

- empty enum = 0；
- OPAQUE by-value = 0；
- unresolved_type = 0；
- opaque_constructor_missing = 0；
- opaque_destructor_missing = 0；
- callback_signature_mismatch = 0；
- required codec/packet functions 的 wire_mapping coverage = 100%；
- generated headers 的 standalone compile = 100%。

优先代码区域：

- agent/planning/prompts.py：Stage 4/5/6 contract；
- agent/planning/planner.py：typed overlay validation 和 partition commit；
- agent/planning/implementability.py：OPAQUE value、enum value、callback ABI diagnostics；
- agent/planning/compiler.py：只做 schema dialect lowering，不补协议值。

### P0-2：Call relation、dependency 与 access-provider closure

目标：让每一条 function edge 都是方向正确、类型可供给、跨文件可见的真实调用。

最小修改方向：

1. Stage 8 把 direct call、callback binding、lifecycle pairing、state prerequisite 分开表达；不能全部压成 function-to-function call edge。
2. 拒绝 condition=never、no call occurs 和没有 behavior evidence 的 placeholder edge。
3. 对每条 call edge 验证 caller/callee direction、argument provider、return use、visibility 和 source/header dependency。
4. Access provider 必须来自 caller PARAMS、owned state、constructor result、accessor return 或已证明的 callee result。
5. Cross-file private callee 必须改 owner/visibility 或删除错误 edge，不能靠 compiler 隐藏 dependency。
6. Stage 10 dependency 应从 validated source edges 确定性派生；semantic patch 必须修源 edge，而非只删派生 dependency。

验收门槛：

- callee_dependency_missing = 0；
- foreign_type_dependency_missing = 0；
- cross_file_private_function/type = 0；
- access_service_missing = 0；
- callback_provider_missing = 0；
- module_dependency_conflict = 0；
- module_generation_order_conflict = 0；
- cross-file call/type dependency measured missing rate = 0%。

优先代码区域：

- agent/planning/prompts.py：Stage 8 relation taxonomy；
- agent/planning/planner.py：Stage 8 typed edge validator；
- agent/planning/implementability.py：provider data-flow 与 direction validation；
- agent/planning/amendment.py：按最早受影响 stage 修复 source relation；
- agent/planning/compiler.py：避免把 dependency filtering 误记为 semantic resolution。

### P0-3：Entrypoint、lifecycle 与 implementation completeness

目标：杜绝 empty task、漏 main、漏 constructor/destructor 和 FUNCTION_SPEC 未实现。

最小修改方向：

1. Stage 4 inventory 必须包含与 target profile runtime contract 一致的 exactly-one main。
2. Stage 6 必须覆盖所有 registered required functions，且 signature 完整。
3. Publication/readiness validation 对 0 FUNCTION_SPEC、0 type、0 test vector 的 executable target 直接失败。
4. Coder generation 后增加 deterministic definition coverage、placeholder 和 near-empty source checks。
5. Generation completion 与 implementation completeness 分开记录。

验收门槛：

- runtime_entrypoint_missing/ambiguous = 0；
- executable target 的 FUNCTION_SPEC、type、test vector 均非空；
- specified function definition coverage = 100%；
- placeholder file = 0；
- near-empty required source = 0；
- exactly one linkable main。

### P1-1：Behavior contracts 与 executable test vectors

目标：让 coder 不再依赖 generic prose 猜测 protocol behavior。

最小修改方向：

1. Stage 7 对 parser、encoder、state transition、session、router 和 transport function 使用类型化 behavior fields。
2. State-bearing function 必须提供 PRECONDITION、POSTCONDITION、INVARIANTS_USED。
3. Function ACCESS_PATHS 应描述关键 state/buffer/handle 的读取与写入路径。
4. Function FORBIDDEN_SYMBOLS 或等价 negative constraints 应覆盖禁止调用、禁止状态转换和禁止 wire encoding。
5. Stage 9 test vector 必须给出 concrete bytes/state/input、expected return/output/state 和 trace refs；空 inputs 不能计入 coverage。

验收门槛：

- generic logic template rate ≤ 10%；
- behavior-bearing functions 的 PRECONDITION/POSTCONDITION coverage = 100%；
- stateful/wire functions 的 invariants coverage = 100%；
- empty test inputs = 0；
- minimum profile required behaviors 每项至少有一个 planning-linked executable vector。

### P1-2：Traceability 从 fact identity 提升到 engineering decision

目标：让高风险设计选择可解释、可验证、可回退。

最小修改方向：

1. 保留现有 141/141 function trace_refs。
2. 对 type representation、pointer/value、ownership、call direction、wire constant、lifecycle 和 error behavior 增加 decision_refs/rule_refs。
3. Validator 不只检查引用存在，还检查引用要求是否在 artifact 中物化。
4. Semantic patch 必须记录修复了哪个 decision/rule violation。

验收门槛：

- 高风险 engineering decisions 的 decision_refs/rule_refs coverage = 100%；
- orphan decision/rule = 0；
- fact、decision、artifact、test vector 能形成可追踪链。

### P1-3：Readiness observability

目标：让内部指标能预测 downstream utility，而不是只描述执行完成。

最小修改方向：

1. Manifest 同时记录 closure diagnostics、post-validation diagnostics 和 union diagnostics。
2. artifact_success、semantic_qualified、implementation_ready 三个状态分别报告；这是同一 pipeline 的逐层状态，不新增两套行为模式。
3. Readiness score 只使用可验证 hard gates，不使用 diagnostics 数量简单加总。
4. 对 OPAQUE by-value、empty enum、dependency missing、definition coverage 建立 run-level trend。

验收门槛：

- run manifest 的 total diagnostic count 与 diagnostics.json 一致；
- unresolved partition 不能隐藏未执行的 validation layer；
- readiness pass 的 artifact 必须在固定 coder smoke test 中达到 initial compile。

### P2：Token efficiency

当前 fresh-only planning+coder token 中位数约 473,927，而 successful implementation yield 为 0。此时先压缩 token 可能放大 semantic omission。

只有在 P0/P1 gates 稳定后再优化：

- 用 typed registry slice 减少重复 context；
- 对已经 qualified 的 partitions 使用 deterministic replay；
- 将 semantic correction 限定到最早受影响 stage；
- 报告 tokens/successful compile 和 tokens/behavior-passing implementation，而非只报告 tokens/run。

## 11. 超越基线的量化验收方案

### 11.1 单 run semantic gate

每个进入 RQ1 publication M2 主分析的 run 必须同时满足：

1. fresh=true、resume=false、replay=false；
2. 11/11 stages 和全部 required partitions 完成；
3. specs generated、coder loader passed；
4. qualification_passed=true；
5. union error diagnostics = 0；
6. empty enum、OPAQUE by-value、missing dependency、missing provider = 0；
7. specified function definition coverage = 100%；
8. placeholder/near-empty required source = 0；
9. initial clean compile passed；
10. behavior verifier 实际执行，而非 upstream not-run。

### 11.2 超越 M0/M1 的工程目标

下一轮至少运行 M0/M1/M2 各 10 次独立 fresh，并设以下目标：

| Endpoint | 最低目标 | 推荐目标 |
| --- | ---: | ---: |
| M2 initial compile | ≥5/10 | ≥6/10 |
| M2 runtime start | ≥5/10 | ≥6/10 |
| M2 E2E/minimum behavior | ≥5/10 | ≥6/10 |
| Roots/source kLoC median | <45.5 | <34.1，低于 M0 当前 Q1 |
| C2 median | <6.5 | ≤1，达到 M0 当前中位数 |
| C5 median | <10 | ≤3，达到 M0 当前中位数 |
| Definition coverage | 100% | 100% |

在两个 baseline 都为 0/10 时，M2 达到 5/10 与任一 baseline 的单侧 Fisher exact p 约为 0.016；推荐 6/10 是为了保留工程和多重比较余量，而不是只追求显著性阈值。

成功率必须同时报告 exact confidence interval、effect size 和失败分类。不能用 root-cause count 下降替代 compile/behavior endpoint。

### 11.3 实验控制

- 使用同一个 clean commit；
- 冻结 facts/profile hash、model snapshot、prompt revision、compiler 和 coder；
- 同一日期窗口执行；
- 按 replicate 对 M0/M1/M2 随机化或轮换 method order；
- 每个 output directory 独立；
- resume 只用于诊断，不进入主样本；
- Track A no-repair 与 Track B bounded repair 分开；
- M2 fixed-planning coder variance 与 fresh end-to-end planning variance 分开；
- 保存原始 prompts、responses、usage、specs、code、compile logs 和 behavior logs。

## 12. 推荐的下一轮实施顺序

建议严格按以下顺序推进，避免再次用 inventory growth 掩盖 semantic failure：

1. 增加只读 metrics/tests，固定 empty enum、OPAQUE by-value、dependency closure、provider closure 和 definition coverage 的当前失败样本。
2. 修 Stage 5/6 type、enum、wire 和 ABI gates。
3. 修 Stage 8 relation taxonomy、direction、visibility 和 provider validation。
4. 修 Stage 4 runtime entrypoint/lifecycle inventory。
5. 让 semantic patch 回到最早 source stage，避免只删除派生 dependency。
6. 在现有 6 个 artifacts 上 replay validation，确认 diagnostics 能捕获已知缺陷；不要把它们改造成成功样本。
7. 新跑 1 次 bootstrap fresh，要求 qualified + initial compile。
8. 失败时用该 run 的最早受影响 stage resume 定位，但 resume 结果不计验收。
9. 达到至少 3 次连续 fresh qualified + initial compile 后，再投入 P1 behavior/test-vector density。
10. 最后运行同日随机化 M0/M1/M2 各 10 次 publication experiment。

## 13. 给后续 Codex 的决策摘要

后续优化应遵守以下约束：

- 不复制 MQTT specs-example inventory；
- 不在 compiler 中发明 packet constants、fields、ownership 或 call behavior；
- 不通过关闭 validator、批量 error→warning 或隐藏 dependency 制造 qualification；
- 不把 callback/lifecycle/state relation 强行表示成 direct call；
- 不以 function 数量、diagnostics 数量或 loader pass 代替 compile/behavior utility；
- 保留 canonical registry、typed overlays、partition transaction、bounded recovery、provenance、resume、token metrics 和 deterministic compiler；
- 优先做最小、可验证、facts/rules grounded 的 source-stage correction。

下一轮最值得首先验证的单一假设是：

> 如果 planning 强制消除 empty enum、OPAQUE by-value、missing dependency 和 missing access provider，并保证所有 required FUNCTION_SPEC 有唯一 definition，那么 M2 的 initial compile success 将从 0/6 提升为稳定非零，并首先显著降低 C2/C4/C5。

这比继续扩充 function inventory、增加 generic test vector 数量或降低 semantic diagnostic count 更有证据基础。

## 14. 最终判断

当前 planning 产出已经具备可持续生成 protocol specs 的工程基础，但还不具备支撑 RQ1 正向论文结论的 semantic utility。它的主要价值已经从“能否产出 artifact”转移到“如何把 protocol facts 转化为闭合的 type、wire、ownership、dependency 和 behavior contracts”。

下一阶段的成功标准不应再是 3/3 coder-loader pass，而应是：

> 连续 fresh qualified specs、100% function definition coverage、initial compile 稳定非零，随后在统一 behavior verifier 上超过 M0 和 M1。

在达到这一门槛之前，本轮 6 个样本适合作为 transparent pilot、negative result 和 optimization evidence；达到门槛之后，才适合作为 Full-SpecForge 的 publication M2 主实验。
