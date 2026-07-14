# SpecForge Planning RQ1 Semantic Utility Goal 任务书

> 制定日期：2026-07-13
> 执行对象：Codex Goal mode
> 核心对象：planning agent
> 当前状态：`IN_PROGRESS`
> 状态标记：`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED` / `DEFERRED`

## 0. 使用方式

本文件是下一轮 planning semantic utility 优化的可执行任务书。它不是开放式建议清单，也不是仅用于阅读的分析报告。Codex 应持续执行本文件中的阶段、门禁、验证和记录要求，直到满足成功停止条件、严格的阻塞停止条件，或者 fresh planning 10-run 硬上限停止条件。

建议向 Codex Goal mode 提交以下 objective；不指定 `token_budget`，除非用户另行明确给出预算：

```text
持续执行 /home/ljf/SpecForge/agent/planning/PLANNING_RQ1_SEMANTIC_UTILITY_GOAL.md。
把当前 planning artifact stability 作为不可退让的硬约束：除本任务书严格定义的 fatal error
外，任何 semantic、qualification 或 engineering error 都必须继续产出 schema-conformant、
coder-loadable 的 protocol specs 及完整 diagnostics，不得以 fail-fast 或不写 specs 代替诊断。
在此前提下，优先提升 planning agent 将 protocol facts 转换为 implementation-oriented
protocol specs 的 semantic utility：消除 type/wire/ABI、
dependency/access-provider、entrypoint/lifecycle 和 implementation completeness 缺口，建立
可复现的 fresh planning -> coder -> compile/repair -> behavior 反馈闭环，并通过预声明的
MQTT RQ1 实验验证 M2 Full-SpecForge 在主要下游指标上优于 M0 FS-Direct-Coder 与
M1 NL-Plan-Code。严格遵守任务书的修改边界、阶段门禁、失败分母规则和停止条件。
```

Goal 启动后必须遵守：

1. 每次 continuation 先读取本文件、当前 Goal 状态、最近一个 Step 执行记录和最新 run manifest，不从头重复已完成工作。
2. 同一时刻只允许一个 Step 为 `IN_PROGRESS`。
3. 每完成一个 Step，立即更新本文件中的状态与执行记录，不在 Goal 结束时一次性补写。
4. 测试失败、fresh run 失败、semantic qualification 失败或 compile 失败都属于待解决结果，不是 Goal blocker。
5. 只有成功条件全部满足时才能将 Goal 标记为 `complete`。
6. 只有同一外部或事实输入阻塞连续至少三个 Goal turns、且已穷尽安全的范围内方案后，才能将 Goal 标记为 `blocked`。
7. token 使用接近上限、任务较难或实验结果为负，均不得作为伪完成或伪阻塞理由。

### 0.1 全程硬约束：非致命错误仍须产出 specs

本约束高于各 Step 的 semantic qualification、compile 和 behavior 门禁，并在每一次修改、测试、fresh run、resume 和正式实验中持续生效：

1. `qualification_passed=false` 与“没有 specs”是两个不同结果。qualification gate 决定 artifact 能否被声明为 ready，不决定是否 materialize specs。
2. type/wire/ABI、dependency/provider、entrypoint/lifecycle、traceability、test vector、unresolved partition、bounded correction exhausted 等问题默认都是 **non-fatal error**。它们必须写入 diagnostics，同时基于已提交的 schema-valid state 继续生成 candidate-only specs。
3. non-fatal run 必须保留 `_planning/run_manifest.json`、machine-readable diagnostics 和实际 `specs_root`；`specs_root` 必须包含与已承诺 required inventory 对应的 module/file/function specs，并通过 schema validation 与 coder loader。不得通过清空 inventory、跳过 compiler 或 suppress output 保持表面稳定。
4. **fatal error** 仅限于使 serialization 在技术上不可能继续的输入/基础设施/进程级失败，例如 authoritative facts 无法读取或解析、外部 model service 在取得任何有效 stage artifact 前持续不可用、文件系统无法写入，或不可恢复的内部异常。semantic 不合格、required field 缺失或 correction budget 用尽本身不构成 fatal。
5. 每个 fatal run 仍须尽可能写出 manifest、`fatal_reason_code`、失败 stage、原始 diagnostics 和已安全提交的 partial artifacts。fatal 分类必须有 machine-readable 证据，不得在实验后人工改类。
6. 新增 semantic hard gate 时必须同时验证“正确拒绝 qualification”和“仍然 materialize candidate specs”。任何让既有 non-fatal case 从 coder-loadable specs 退化为 early abort/no specs 的修改均视为 stability regression，不得合入或进入下一 Step。

## 1. 权威依据与研究定位

执行时按以下优先级使用依据：

1. 本任务书：执行范围、阶段顺序、开发门禁和停止条件；
2. `agent/planning/PLANNING_RQ1_SEMANTIC_OPTIMIZATION_REPORT.md`：当前失败证据、根因假设和历史指标；
3. `evaluation/planning_utility/README.md`：RQ1 方法定义、正式实验分母、primary metrics 和统计协议；
4. `agent/planning/PLANNING_CURRENT_STATUS_REPORT.md`：当前 planning 架构与 artifact-stability freeze point；
5. 当前代码、tests、machine-readable manifests 和原始运行 artifacts：实现事实。

如果文档与当前代码不一致，以可复现的代码、CLI `--help`、tests 和 machine-readable artifact 为准，并在本任务书记录差异。不得静默选择有利于 M2 的解释。

SpecForge 的核心研究主张必须保持为：

> Existing work has explored extracting protocol facts from technical documents and generating code from engineering specifications, but there remains a gap between these two stages. SpecForge addresses this gap by designing a planning agent that transforms protocol facts into actionable protocol specifications and implementation plans.

本 Goal 的研究对象不是普通 summarization。planning agent 必须把 protocol facts 转换为 coder agent 可消费、可验证、可追踪的 module/file/function-level protocol specs，并让这些 engineering contracts 对 compile、repair 和 behavior 产生可观察的正向效用。

## 2. 当前基线与已知事实

### 2.1 已完成的稳定性基础

当前 planning 已达到：

- 最终验收序列连续 3 次独立 fresh planning 完成 11/11 stages；
- 3/3 生成 coder-loadable specs；
- 3/3 coder loader validation 通过；
- 当前 planning tests 为 94/94 passed。

这些结果只证明 artifact stability。最终三次 fresh 均为 candidate-only，`qualification_passed=false`，不能表述为 semantic utility 或 implementation readiness。

### 2.2 当前 M0/M1/M2 历史结果

| Metric | M0 FS-Direct-Coder | M1 NL-Plan-Code | 当前 M2 candidate |
| --- | ---: | ---: | ---: |
| 样本数 | 10 | 10 | 6，其中 5 个可确认 fresh |
| Generation completion | 10/10 | 10/10 | 6/6 |
| Initial compile | 0/10 | 0/10 | 0/6 |
| E2E success | 0/10 | 0/10 | 0/6 |
| Root causes median | 48 | 90 | 115 |
| Roots/source kLoC median | 45.5 | 63.0 | 61.2 |
| C2 median | 1 | 6.5 | 39 |
| C4 median | 1 | 37 | 5 |
| C5 median | 3 | 10 | 15 |
| Definition coverage | 不适用 | 不适用 | median 66.7% |

当前 M2 的关键语义缺口：

- `wire_mapping` 只有 17/141 非空，所有 plan enum values 总数为 0；
- 4 份 specs 产生 empty packet enum，另 1 份把 packet type 表示为 OPAQUE；
- 21 次 OPAQUE type 按值出现在 function signature；
- 53/177 cross-file call edges 缺 source dependency；
- 5/32 foreign custom type uses 缺 header dependency；
- `access_service_missing`、`callee_dependency_missing`、`cross_file_private_function` 占 planning diagnostics 的 78.6%；
- 103/141 specified functions 有 definition，缺失 38 个；
- 7 个 source file 有 placeholder，10 个 required source 基本为空；
- generic logic template 为 100/141；
- PRECONDITION、POSTCONDITION、INVARIANTS_USED、function ACCESS_PATHS 和 function FORBIDDEN_SYMBOLS 密度不足；
- 141/141 functions 有 `trace_refs`，但 `decision_refs` 和 `rule_refs` 全为空。

### 2.3 RQ1 baseline 与工程参照的边界

RQ1 的两条 baseline 只有：

- M0 `FS-Direct-Coder`：compact facts-derived context 直接生成代码，不产生 planning artifact；
- M1 `NL-Plan-Code`：自然语言 engineering plan 经压缩 brief 指导代码，不产生 structured specs。

`evaluation/spec_ablation` 的 S1/S2/S3/S4 不是 RQ1 baseline。S4 reference specs 可作为 coder/evaluator 的工程健康参照，但不能证明 planning agent 能自动生成同等质量的 specs，也不得被复制为 planning inventory。

### 2.4 启动审计已发现的 evaluation harness 漂移

在本任务书制定时，以下问题已被只读复现，必须先修复：

1. `python3 -m unittest discover -s evaluation/planning_utility/tests -v` 因缺少 `agent.planning.adapters.target_profile` 导入失败；
2. `evaluation/planning_utility/configs.py` 引用的 MQTT/CoAP/SMTP target profile 文件不存在；
3. `full_specforge_adapter.py` 与 generate-only shell script 仍调用旧 planning CLI：`--target-profile`、`--output-dir`、`verify` 和 `spec_bundle`；
4. 当前 planning CLI 实际提供 `plan --facts --out`、`validate --run-dir` 和 `stages`，没有 `verify`；
5. 当前 planning output 的 coder-facing path 来自 `_planning/run_manifest.json.specs_root`，不固定为 `planning_run/spec_bundle`；
6. 当前 M2 adapter 因此不能作为可信的正式 RQ1 执行入口。

不得在该闭环修通前消耗正式 publication runs。

### 2.5 当前 worktree 保护

制定本任务书时已存在以下用户改动：

```text
D  agent/planning/PLANNING_EVALUATION_STABILITY_PLAN.md
M  evaluation/planning_utility/README.md
?? agent/planning/PLANNING_RQ1_SEMANTIC_OPTIMIZATION_REPORT.md
```

它们均属于用户现有工作。Goal 不得恢复被删除的旧稳定性方案、覆盖未跟踪报告或丢弃 README 修改。每一步都必须先查看 `git status --short`，只处理与当前 Step 直接相关的文件。

## 3. 本 Goal 的唯一总体目标

在不牺牲现有 artifact stability、不修改 protocol facts 来制造成功、不向 compiler 注入 MQTT 专属行为的前提下：

> 让 planning agent 在所有 non-fatal 情况下稳定产出可诊断、coder-loadable 的 protocol specs，并稳定生成 semantic-qualified、implementation-ready 的 MQTT minimum broker protocol specs，使冻结的 coder agent 能直接生成可编译代码，或在预声明的 bounded repair 后生成可编译且通过 required behavior verifier 的代码；随后以无 post-treatment selection 的 RQ1 实验验证 M2 的下游效用高于 M0 和 M1。

目标分为四层，后层不能替代前层：

| 层次 | 目标 | 主要证据 |
| --- | --- | --- |
| L0 Artifact stability | 保持现有 3/3 fresh coder-loadable；所有 non-fatal run 均产出 specs | planning manifest、fatal classification、schema validation、coder loader |
| L1 Semantic readiness | type/wire/ABI、dependency/provider、lifecycle/entrypoint 全部闭合 | union diagnostics、semantic metrics、standalone headers |
| L2 Implementation utility | definition 100%、无空任务、initial compile 稳定非零 | no-repair code、clean diagnostic snapshot |
| L3 RQ1 utility | final compile、runtime、required behavior 和 E2E 优于 M0/M1 | 预声明 30-run matrix、统计报告 |

降低 diagnostics 数量、增加 function 数量、增加 test vector 数量、降低 token 或保持 loader pass 均不是独立成功条件。

## 4. 修改范围与 change control

### 4.1 默认允许修改

优先只修改：

```text
agent/planning/
evaluation/planning_utility/
```

允许更新：

- planning prompts、stage contracts、typed validation、implementability analysis、semantic recovery、compiler 的 schema dialect lowering；
- planning tests；
- planning utility adapter、只读 metrics、analysis CLI、tests 和 README；
- 本任务书与 `PLANNING_CURRENT_STATUS_REPORT.md`；
- RQ1 运行调度与结果聚合，但不得改变预注册方法含义。

### 4.2 默认只读

以下内容默认只读：

```text
agent/facts/
agent/coder/
specs-example/
specs-example/specs_schema/
protocol-example/
既有 generated C/H 与历史 run artifacts
```

可以执行 coder validate/generate/repair/test，可以读取 coder source、schema 和 prompts 来理解 contract，但不得为 M2 成功而直接修改 coder behavior。

### 4.3 允许 coder 修改的唯一例外

只有同时满足以下条件，才允许对 `agent/coder/` 做最小 generic bug fix：

1. planning artifact 已通过本任务书所有对应 hard gates；
2. bug 可由一个 protocol-agnostic、schema-valid 的最小 spec fixture 独立复现；
3. 问题属于 coder 对其公开 contract 的实现错误，而不是 specs 信息不足；
4. 修改不增加 MQTT 名称判断、不改变 coder prompt 以偏向 M2、不放宽 schema/validator；
5. 先新增失败 regression test，再做最小修复；
6. 修复后重新运行 M0/M1/M2，而不是只重跑 M2；
7. 在执行记录中单独声明这会改变 RQ1 system revision。

不满足任一条件时，保持 coder 只读，并把失败回溯到 planning 的最早 authoritative stage。

### 4.4 禁止行为

严禁：

- 修改 `protocol_facts.json` 来补足当前 planning 没有提取出的协议行为；
- 从 `specs-example/mqtt_specs` 或 `protocol-example` 复制 module/function/type inventory、signature、dependency、wire value 或 behavior；
- 在 compiler 中发明 packet constants、fields、ownership、call direction、test oracle 或 lifecycle；
- 通过删除 required member/dependency、过滤 forward edge、降低 error severity 或关闭 validator 制造 qualification；
- 为 MQTT 添加硬编码 fallback、wrapper、adapter registry、第二套 strict/evaluation pipeline 或协议专属配置开关；
- 人工编辑 fresh/resume run 的 stage artifacts、specs 或 generated source；
- 用 0 FUNCTION_SPEC、近空 source 或低 source LoC 获得虚假的低 root-cause count；
- 把 callback binding、lifecycle pairing、state prerequisite 强行伪装成 direct call；
- 把 resume、replay、fixed-planning coder run 混入 fresh end-to-end success 分母；
- 静默 rerun 或删除 planning/compile/behavior 失败的正式实验样本。

## 5. 实现原则

1. Stability 是所有 semantic 优化的前置不变量：validator 可以把 artifact 标为 candidate-only，但不得因 non-fatal diagnostic 阻断 compiler/specs materialization。
2. 每项修改前后都运行受影响的 artifact-stability regression；若 non-fatal specs 产出、schema validation 或 coder loader 任一回归，立即缩小或撤销本轮自身修改，不通过新增 fallback/兼容分支掩盖。
3. 每一项修复先定位最早 authoritative writer stage，再修改 prompt/validation/recovery；compiler 只做无损、确定性的 dialect lowering。
4. 先写能捕获已知失败的 deterministic test/metric，再修改生成逻辑。
5. 优先替换 obsolete logic；删除死代码、旧 CLI contract、重复 branch 和失效文档，不保留新旧两套实现。
6. 不新增 abstraction，除非 typed relation、可复现 analyzer 或 target-profile contract 是完成本 Goal 所必需；新增时必须说明为什么现有结构不能表达。
7. 对 protocol facts、inferred engineering decisions 和 open assumptions 保持显式区分。
8. 任何 protocol value 必须有 `TRACE_REFS`、`decision_refs` 或 `rule_refs`；无法 grounding 时阻断 qualification，不猜值，但仍按 0.1 节产出 candidate specs。
9. Stage-local 可判断的问题必须在 partition commit 前失败并触发一次 bounded correction，不能全部推迟到末端 semantic patch；correction 失败不得丢弃此前已提交的有效 state。
10. Semantic correction 必须修复 source artifact，不得只删除由 source relation 派生出的 dependency。
11. 保留 canonical registry、typed overlays、partition transaction、bounded recovery、provenance、resume、token metrics 和 deterministic compiler。
12. P2 token optimization 只能在 semantic/readiness/compile gates 稳定后进行。

## 6. 统一指标定义

### 6.1 Planning 层

- `artifact_success`：stages 正常完成、specs materialized、coder loader 可加载；不等于 qualified。
- `nonfatal_spec_materialization_rate`：产出 schema-valid、coder-loadable specs 的 non-fatal attempts / 全部 non-fatal attempts；开发与正式实验均要求 100%。
- `nonfatal_no_specs_count`：被分类为 non-fatal、但 manifest 缺少有效 `specs_root` 或 coder loader 无法加载的 attempts；硬门禁为 0。
- `fatal_abort_count`：按预声明 `fatal_reason_code` 分类的 attempts；必须独立报告，不得与 semantic/qualification failure 混淆。
- `semantic_qualified`：`qualification_passed=true`，union error diagnostics 为 0，unresolved required partition 为 0。
- `union diagnostics`：semantic closure initial/final、unresolved ledger、post-planning validation 和 coder loader diagnostics 的去重并集；不能只读取 `semantic_closure.final_diagnostics`。
- `implementation_ready`：本节所有 hard semantic metrics 为 0，且 required inventory 非空。
- `distinct unresolved partitions`：按稳定 diagnostic/partition identity 去重；attempt count 与 distinct count 分开。

Planning hard semantic metrics：

```text
empty_enum_count
opaque_by_value_count
unresolved_type_count
callback_signature_mismatch_count
opaque_constructor_missing_count
opaque_destructor_missing_count
runtime_entrypoint_missing_or_ambiguous_count
cross_file_private_symbol_count
cross_file_call_dependency_missing_count
foreign_type_dependency_missing_count
access_service_missing_count
callback_provider_missing_count
module_dependency_or_generation_order_conflict_count
required_wire_mapping_missing_count
required_inventory_empty_count
```

这些 metrics 必须来自 machine-readable plan/spec/diagnostics，不从 Markdown 文本人工推断。

### 6.2 Generated-code 层

- `specified function`：进入 coder 的 required FUNCTION_SPEC 中声明的 function。
- `definition coverage`：具有 exactly one matching non-stub definition 的 specified functions / specified functions；重复 definition 也失败。
- `placeholder`：TODO/FIXME/not implemented/placeholder/whatever、注释化 case label、固定 dummy return 或等价未实现标记；analyzer 必须避免只按单一字符串产生结论。
- `near-empty required source`：required source 没有实现其 FILE_SPEC 所属的任何 specified function，或仅含 includes/comments/stub；不能只用总 LoC 阈值。
- `initial compile`：coder generation 后、任何 repair 前，在 source hash 保持不变的临时副本中 clean build。
- `pre-repair diagnostics`：同一冻结 classifier 对所有三种方法运行 header self-check、translation-unit syntax check、clean build 和 C1-C5 分类。
- `roots/source kLoC`：仅在 required inventory 非空且 definition coverage 可计算时比较，防止 task evaporation。

### 6.3 Behavior 与 E2E 层

MQTT minimum profile 有 5 个 required self-contained scenarios 和 1 个 optional interop scenario：

- required scenario 未执行时，run-level 结果记为失败，并在 scenario 明细标记 `not_run_due_to_upstream_failure`；
- optional interop dependency 缺失记为 `skipped`，不改变 required denominator；
- `E2E_success=1` 当且仅当 planning/guards、final compile、runtime startup 和全部 required scenarios 均通过。

### 6.4 Fresh、resume 与 fixed-planning

- development fresh acceptance：`fresh=true`、`resume=false`、`replay=false`、独立 output directory；
- resume：只用于定位和验证最早受影响 stage，不计连续 fresh；
- fixed-planning coder runs：只测 coder variance，单独报告；
- publication fresh attempts：按预声明顺序全部进入分母，除非属于预定义 infrastructure exclusion。

## 7. 分阶段执行计划

## Step 0：冻结起点与建立可复核基线

**状态：DONE**

### 目标

在任何实现修改前，固定当前代码、用户改动、测试结果、输入 hashes、toolchain 和历史失败证据。

### 任务

1. 完整读取本任务书、RQ1 semantic report、current status report、planning utility README 和相关 tests。
2. 记录：
   - `git rev-parse HEAD`；
   - `git status --short`；
   - `git diff --stat` 与相关 diff；
   - facts SHA-256；
   - target profile path/hash 或缺失状态；
   - model identifier/config；
   - Python/GCC/Make/Clang 版本；
   - optional interop tools availability。
3. 运行并记录：

```bash
python3 -m compileall -q agent/planning
python3 -m unittest discover -s agent/planning/tests -p 'test_*.py' -v
python3 -m unittest discover -s evaluation/planning_utility/tests -v
python3 -m unittest agent.coder.tests.test_minimum_matrix_runner -v
git diff --check -- agent/planning evaluation/planning_utility
```

4. 将 planning 94/94 pass 和 planning utility ImportError 作为起点事实，不通过恢复旧 planning 代码来掩盖。
5. 冻结历史 6 个 M2 artifacts 和 20 个 M0/M1 no-repair projects为只读诊断证据；记录路径，不修改内容。
6. 为本轮新输出创建独立、带日期和 sequence 的目录命名约定。
7. 冻结至少一个会产生 semantic diagnostics 的 non-fatal fixture，记录其 specs tree、schema validation 和 coder loader 结果，作为后续每个 Step 都必须通过的 stability sentinel。

### 完成标准

- 当前 baseline 和已知 harness failure 可由命令复现；
- 用户现有改动没有被覆盖；
- 输入与工具版本均有 machine-readable 记录；
- non-fatal stability sentinel 能稳定产出 coder-loadable specs；
- 没有修改实现代码。

### Step 0 执行记录

- 完成时间：2026-07-13T17:50:48+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：本任务书、RQ1 semantic report、current status report、planning utility README、planning/pipeline/compiler/tests、历史 manifests/summaries
- 修改文件：仅新增 ignored machine-readable baseline；更新本任务书状态与记录
- 删除内容：无
- 新增内容：`agent/planning/out/rq1_semantic_utility_goal_20260713/step0_baseline.json`
- 新增/修改 tests：无
- Targeted tests：3 个历史 candidate specs 使用独立输出目录顺序执行真实 coder validation，3/3 passed
- 全量 tests：planning 94/94 passed；planning utility 与 coder minimum matrix 均因旧 `agent.planning.adapters` import 在 collection 失败
- Non-fatal stability sentinel（specs/schema/loader）：semantic-gap fixture passed；历史连续 3 个 fresh candidate specs 独立 loader 3/3 passed
- Fatal/non-fatal 分类及 `fatal_reason_code`：发现 `RecoverablePlanningError` 仍形成 non-fatal + no specs；现实现使用 `hard_failure_code`，尚无统一 `fatal_reason_code`
- Validation/metrics：当前 `nonfatal_no_specs_count` 至少为 1；这是 Step 1 必须先修的 invariant gap，不通过重分类为 fatal 规避
- Planning runs：未启动新 LLM planning；历史 artifacts 只读
- Coder runs：仅 validate；一次并发默认日志目录碰撞已记录为 infrastructure observation，显式独立目录顺序复核全部通过
- Input/model/tool hashes：见 `step0_baseline.json`
- 本步 Goal token：92,805
- 累计 Goal token：125,551
- 新发现问题：candidate/qualified specs-preservation 覆盖充分，但 early recoverable path 与 0.1 节硬约束冲突；CLI returncode 不能代表 qualification
- 假设与证据：无新增协议假设；全部结论来自 tests、manifests、CLI 和 source
- 是否阻塞下一步：否；作为 Step 1 首个 regression 修复
- 后续调整：先建立 non-fatal 全路径 sentinel，再删除 evaluation 的旧 adapter/CLI/path 假设

## Step 1：修通并冻结 RQ1 测量闭环

**状态：DONE**

### 目标

让当前重构后的 planning、planning utility 和 coder 通过一条真实、无旧接口残留的执行链工作。

### 任务

1. 以当前 planning 架构为准，删除 evaluation 中失效的旧 CLI/path 假设，不恢复被删除的旧 planning architecture。
2. 明确并实现唯一的 target-profile policy：
   - 当前 RQ1 harness 收缩为 MQTT-only；不得为本 Goal 恢复尚不可运行的 CoAP/SMTP compatibility；
   - 在 `evaluation/planning_utility/target_profiles/` 冻结 MQTT evaluation profile，供三种方法的 orchestration、runtime contract 和 input hash 记录；
   - 当前 planning 的权威输入仍是 structured protocol facts；删除 tests 对已不存在旧 `agent.planning.adapters` tree 的依赖；
   - 如果 M2 planner 不直接读取 target profile，manifest/README 必须明确 `target_profile_visible_to_planner=false`，不得声称三种方法的 model-visible input 完全相同；
   - 只有审计证明 facts 中的 target role/runtime/scope 信息不足且 RQ1 contract 必须让 planner读取 profile 时，才在当前 `facts.py`/`models.py`/CLI 中做最小接入；不得恢复整套旧 adapters 或增加兼容层。
3. 更新 `full_specforge_adapter.py`：
   - 调用真实 planning CLI；
   - 从 `_planning/run_manifest.json` 读取 status、qualification、`specs_root`、tokens 和 diagnostics；
   - 使用真实 `validate --run-dir`；
   - 不假设 `verify` 或固定 `spec_bundle`；
   - formal M2 在 planning/qualification failure 时记为方法失败；
   - development candidate diagnosis 可另行保留，但不得伪装成 publication M2 success。
4. 更新 generate-only script 和 README 中的命令，保证所有示例实际可运行。
5. 建立统一的 Track A no-repair analysis：
   - 复用 `repair_diagnostics.create_diagnostic_snapshot`，不复制 classifier；
   - 增加 definition coverage、placeholder、near-empty、source hash preservation 和 semantic metrics；
   - 输出 machine-readable JSON；
   - 如果现有模块没有可复现入口，最多新增一个职责单一的 analysis CLI。
6. 修复 planning utility tests，并增加 current CLI/manifest/specs-root integration tests。
7. 对一个小型 fixture 执行 planning manifest -> coder validate -> coder generate-only -> diagnostic snapshot，禁止真实大规模实验。

### 必须新增或修正的测试

- M2 summary 写入 target profile path/hash 和 `target_profile_visible_to_planner`；若标记为 true，planning context/manifest 必须有对应证据；
- 缺失/非法 target profile 明确失败；
- adapter 使用 manifest `specs_root`；
- qualified 与 candidate-only 状态不混淆；
- candidate-only/non-fatal fixture 仍生成 schema-valid、coder-loadable specs，semantic gate 不变成 output gate；
- fatal/non-fatal classification、`fatal_reason_code` 和 `nonfatal_no_specs_count` 可审计；
- planning failure 保留在 run summary；
- no-repair analyzer 不修改 source hashes；
- definition coverage、placeholder、near-empty 检测有正负 fixtures；
- stale `verify`/`spec_bundle`/旧参数不再存在。

### 完成标准

- planning、planning utility 和相关 coder tests 全部通过；
- 单个 fixture 的完整测量链可复现；
- README 命令与 `--help` 一致；
- 没有新旧两套 adapter 或 fallback 路径。

### Step 1 执行记录

- 完成时间：2026-07-13T18:43:34+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：planning CLI/pipeline/manifest、coder CLI/loader、planning utility adapter/config/requirements/tests、generate-only script、历史 MQTT candidate manifests 与 no-repair coder artifacts
- 修改文件：`agent/planning/pipeline.py`、`agent/planning/validation_layers.py` 及对应 tests；`evaluation/planning_utility/` 的 MQTT config/profile、adapter、requirements、README、script、tests；删除 coder minimum test 对旧 planning adapter 的依赖
- 删除内容：旧 `planning verify`、`--target-profile`、`--output-dir`、固定 `spec_bundle` 假设；RQ1 harness 中 HTTP/CoAP/SMTP configs；失效 HTTP profile；coder test 中已删除 `agent.planning.adapters.target_profile` 的 11 行兼容测试
- 新增内容：evaluation-owned MQTT target profile；manifest 的 `fatal`、`fatal_reason_code`、`nonfatal_no_specs_count`；职责单一的 `no_repair_analysis.py`，输出 diagnostic snapshot、definition coverage、placeholder、near-empty 和 source-hash preservation
- 新增/修改 tests：planning non-fatal specs sentinel 与 pre-serializable fatal manifest；adapter current CLI/manifest path/qualification/fatal classification；target profile failures；no-repair analyzer 正负与 source-mutation fixtures
- Targeted tests：adapter 真实历史 candidate copy 正确停在 `planning_qualification`；历史 candidate specs 真实 coder validate 输出 `No diagnostics.`；generate-only artifact analyzer 成功生成 machine-readable result
- 全量 tests：planning 94/94 passed；planning utility 27/27 passed；coder minimum matrix 2/2 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Non-fatal stability sentinel（specs/schema/loader）：历史 `mqtt_evaluation_acceptance_r2_fresh_01_20260713` 保持 candidate-only，`specs_generated=true`、`coder_loader_passed=true`、`nonfatal_no_specs_count=0`；formal adapter 未调用 coder generation
- Fatal/non-fatal 分类及 `fatal_reason_code`：semantic/qualification/coder-loader diagnostics 均保持 non-fatal 并 materialize specs；仅在尚无 fact-grounded module/file/function inventory、无法无发明地 serialization 时记录 `failed_internal/candidate_serialization_impossible`
- Validation/metrics：真实 no-repair artifact 为 30 required functions、19 covered、coverage 63.3%、placeholder 1、near-empty required source 1、initial build return code 2、source hashes preserved
- Planning runs：未启动新 LLM planning；仅复制历史 run 到临时目录执行真实 `validate --run-dir`，plan-time manifest/diagnostics 未覆盖
- Coder runs：真实 coder validate 1 次；未新增 LLM generate，复用同一历史 run 的 generate-only artifact 执行只读 snapshot
- Input/model/tool hashes：沿用 Step 0 冻结值；新增 MQTT profile hash 由每个 M0/M1/M2 `allowed_inputs/input_hashes.json` 与 M2 summary 记录，且 `target_profile_visible_to_planner=false`
- 本步 Goal token：156,042（按 Step 0 累计 125,551 到本步记录时 281,593 的差值）
- 累计 Goal token：281,593
- 新发现问题：第一次手工 adapter 调用漏传 required keyword arguments；补齐后暴露 `_stored_planning_diagnostic` 缺少 `json` import，已最小修复并由真实 adapter run 与全量 tests 复核
- 假设与证据：M2 planner 只看 facts，target profile 仅属 evaluation-controlled input；证据为 current CLI、adapter command、manifest summary 字段及 README contract
- 是否阻塞下一步：否
- 后续调整：Step 2 先用 deterministic preservation tests 固化 enum/type/callback/logic/wire/test-vector/constants/trace 丢失，再修改 compiler lowering；不得用 prompt 增量补偿 compiler 丢失
- 净增超过 50 行说明：新增 `no_repair_analysis.py` 299 行及 tests 182 行，是任务书要求的可复现、只读、machine-readable Track A 测量入口；adapter/tests 的增量用于替换已失效 CLI/path contract 并审计 fatal/non-fatal 分母，不是兼容层或第二套 pipeline

## Step 2：固化历史失败并先修复 plan -> specs 确定性语义丢失

**状态：DONE**

### 目标

确保下一轮 prompt 或 stage 修改不能再次生成报告中已知的 empty enum、OPAQUE by-value、错误 call edge、空任务和漏 definition，而 validator 又未发现；同时先证明已有 plan 语义能够被 compiler 无损 materialize，避免用更多 LLM 输出补偿 deterministic lowering 丢失。

### 任务

1. 建立 plan -> normalized plan -> FILE/FUNCTION/MODULE_SPEC 的 field-preservation matrix，至少覆盖：
   - Stage 5 `values`/`ENUM_VALUES`；
   - callback `c_type`/`CALLBACK_SIGNATURE`；
   - structured 和 string LOGIC/EVENT；
   - structured 和 string `wire_mapping`；
   - function/file/runtime test-vector inputs、expected output/state 和 trace refs；
   - function ACCESS_PATHS/FORBIDDEN_SYMBOLS；
   - schema 支持的 trace refs，以及 schema 不支持时保存在 planning sidecar mapping 中的 decision/rule refs；不得为此优先修改 coder schema。
2. 在改变 prompts 前修复已证实的 deterministic loss：
   - `compiler._lower_type_spec()` 不得只读 `fields` 而丢弃 Stage 5 `values`，也不得把已有 string `type_spec` 静默降级为 OPAQUE；
   - callback lowering 不得忽略已有 `c_type`；
   - `_normalize_logic()`/`_normalize_event()` 不得把非空已有语义替换成 generic template；
   - `_normalize_wire_mapping()` 不得静默丢弃已有 mapping；
   - `_normalize_vectors()` 不得因 key dialect 狭窄丢弃 concrete input/oracle；
   - Stage 4 `constants_or_macros` 不得在 final plan/compiler 中消失；
   - unresolved STRUCT/UNION member 不得为 loader compatibility 静默删除；
   - 对无法无损转换的内容返回 blocking diagnostic，不静默输出空值或 generic default。
3. 为以下缺陷建立最小 synthetic fixtures 和明确 diagnostic code：
   - ENUM 无 `ENUM_VALUES`；
   - enum value 无 fact/rule/decision grounding；
   - OPAQUE type 按值用于 RETURN、PARAMS 或 callback signature；
   - required wire function 无 wire mapping；
   - `condition=never`、`no call occurs` 或无 behavior evidence 的 placeholder call edge；
   - callback binding 被错误表示为 direct call；
   - cross-file private callee/type；
   - missing source/header dependency；
   - missing argument/access provider；
   - missing/ambiguous main；
   - executable target 的 function/type/test inventory 为空；
   - required FUNCTION_SPEC 未物化；
   - duplicate unresolved partition 计数。
4. 在现有 6 个历史 artifacts 上只读 replay validator，证明新 gates 能捕获已知问题；当前 `validate_existing_run()` 会改写 diagnostics/manifest，因此必须先复制到临时目录，绝不能直接验证或改写历史原件。
5. 对历史 implementation plans 做 compile-only 临时副本 round-trip，对比 before/after semantic field counts；该结果只验证 compiler preservation，不把旧 run 改成 fresh success。
6. 统一 union diagnostic 聚合，manifest count 必须与 `diagnostics.json` 一致。
7. 每个 diagnostic 必须声明 owner layer、authoritative stage 和 recovery action。
8. 对 task evaporation 增加独立 failure code，root-cause count 不得掩盖 0 function/0 type。

### 完成标准

- 所有已知缺陷均有 deterministic test；
- field-preservation matrix 中已有非空语义的 silent drop 为 0；
- closed fixture 保持通过；
- 历史 artifacts 的问题被检测但文件 hash 不变；
- diagnostics 可定位到最早 source stage。

### Step 2 执行记录

- 完成时间：2026-07-13T19:12:26+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：6 个历史 planning run 的临时副本、对应 `implementation_plan.json`、compiler/validator/semantic closure/pipeline、coder loader 与 schemas；machine evidence 为 `agent/planning/out/rq1_semantic_utility_goal_20260713/step2_evidence.json`
- 修改文件：`agent/planning/compiler.py`、`planner.py`、`implementability.py`、`validation.py`、`pipeline.py`、`models.py` 及 planning tests；adapter 仅增加 copied manifest 的 `semantic_mapping` path rewrite
- 删除内容：删除 unresolved STRUCT/UNION member 的 silent omission 行为、constant/type/function 无 owner 时绑定首个 file 的推测 fallback、unstructured wire mapping 的 generic packet/wire/default strategy 发明、以及一个只断言静默删除的旧测试；保留 coder-loadable omission 仅限伴随 blocking diagnostic 和 sidecar 原值
- 新增内容：11 项 plan→spec field-preservation matrix；`planning_semantic_mapping.json` sidecar；constants/macros deterministic lowering；LOGIC/EVENT、wire mapping、test vector、ACCESS_PATHS/FORBIDDEN_SYMBOLS 与 trace/decision/rule preservation；union diagnostic 去重及 distinct unresolved partition 计数；diagnostic owner/authoritative stage/recovery action
- 新增/修改 tests：empty/ungrounded enum、OPAQUE by-value function/callback ABI、required wire mapping、placeholder/direct-callback call edge、task evaporation、required FUNCTION_SPEC、duplicate unresolved count、enum/callback dialect、structured/string behavior、vectors、constraints、constants、traceability、unresolved member 与 non-fatal lowering sentinel
- Targeted tests：Step 2 synthetic suites 86/86 passed；新增 authoritative-stage、ACCESS_PATH sidecar 与 non-fatal RELY/CALL_CONTRACT drift tests passed
- 全量 tests：planning 105/105 passed；planning utility 27/27 passed；coder minimum matrix 2/2 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Non-fatal stability sentinel（specs/schema/loader）：unresolved member、unstructured wire 或 incomplete access path 均形成 candidate-only blocking diagnostic，同时实际 module/file/function specs 保留且 schema/coder loader passed；`validate_existing_run()` 不能把该 candidate 重新判 qualified
- Fatal/non-fatal 分类及 `fatal_reason_code`：本步新增的 semantic/compiler/validation diagnostics 全为 non-fatal，`fatal_reason_code=null`、`specs_generated=true`、`nonfatal_no_specs_count=0`；未触发 pre-serializable `candidate_serialization_impossible`
- Validation/metrics：6/6 历史 run 仅在临时副本 replay，均捕获新增 semantic errors、保持 candidate-only、coder loader 6/6 passed、原始全树 hash 不变；compile-only 临时 round-trip 6/6 无 field-count decrease、0 lowering error、0 loader error；历史 plan 中 enum values/constants/decision/rule refs 原本已为 0，明确记为 pre-existing upstream loss，未伪装成修复成功
- Planning runs：未启动新 LLM planning；仅历史 plan 临时 compile-only 与 validator replay
- Coder runs：未启动 coder LLM generation；仅真实 loader validation
- Input/model/tool hashes：沿用 Step 0 冻结值；6 个历史 source tree/plan hash replay 前后保持，明细见 `step2_evidence.json`
- 本步 Goal token：不可取得增量；Goal tracker 已进入 `usageLimited`，最后报告累计 281,593，未将不可见增量记为 0
- 累计 Goal token：281,593（tracker 最后可报告值）
- 新发现问题：旧 implementation plans 已在 compiler 前丢失 enum values/constants/decision/rule refs；compiler preservation 只能阻止新丢失，不能恢复不存在的事实；这把最早修复点定位到 Stage 4/5 和 final reconciliation
- 假设与证据：未新增协议行为假设；diagnostic stage 归属按最早 authoritative writer 固定，历史结论仅来自 frozen artifacts 的只读临时 replay
- 是否阻塞下一步：否
- 后续调整：Step 3 在 Stage 4 形成 generic、facts/rules-grounded obligations/coverage matrix，先闭合 inventory、runtime entrypoint、lifecycle 和 constants identity，再进入 Stage 5/6 ABI；继续禁止以 empty inventory 或跳过 compiler 维持表面稳定
- 净增超过 50 行说明：compiler/validation/implementability 与 tests 的增量用于 schema-safe deterministic lowering、machine-readable loss diagnostics、历史缺陷 hard gates 和 non-fatal specs sentinel；`planning_semantic_mapping.json` 是 schema 暂不支持 decision/rule refs 时的最小 sidecar，没有修改 coder schema或增加第二套 compiler。测试增量覆盖每类已知 failure，避免依赖新 LLM run 才发现回归

## Step 3：闭合 Stage 4 implementation obligations 与 runtime inventory

**状态：DONE**

### 目标

在 detailed type/function design 前，形成由 facts、target profile 和 engineering rules grounding 的完整 implementation obligations。

### 任务

1. 强化 `public_artifact_inventory` contract，必须显式覆盖：
   - minimum profile packet/message constants；
   - message/packet representation identities；
   - exactly one runtime `main`；
   - startup/run/cleanup services；
   - owned resource create/use/destroy lifecycle；
   - callback types/providers；
   - state registry、lookup 或 access services；
   - required parser/encoder/dispatcher/transport/session/routing functions。
2. 以 `minimum_v1`、normalized characteristics、activated engineering rules，以及按 Step 1 policy 确认为 planner-visible 的 target directives 形成 coverage matrix；`target_profile_visible_to_planner=false` 时不得把 profile 内容偷渡进 prompt。不得按 MQTT symbol 名单硬编码。
3. `runtime_entrypoint`、`lifecycle_matrix`、`constants_or_macros` 必须与 canonical registry inventory 双向一致。
4. executable target 的 required function/type/test obligation 为空时，Stage 4 不得 commit。
5. Controlled amendment 对 constant/function lifecycle identity 的处理必须完整且 bounded；删除文档与 validator 对 `proposed_name` 的漂移。
6. 所有 high-risk inventory decision 写入 fact/rule/decision refs。

### 完成标准

- Stage 4 fixture 对 minimum profile obligations 100% coverage；
- exactly one main identity；
- lifecycle create/use/destroy 不缺项；
- required packet/message constant identities 非空；
- registry、coverage matrix、runtime entrypoint 和 lifecycle matrix 无漂移。

### Step 3 执行记录

- 完成时间：2026-07-13T19:24:40+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：Stage 4 prompt/commit validation、canonical registry、controlled amendment、MQTT minimum gold facts、normalized characteristics、activated engineering rules；machine evidence 为 `agent/planning/out/rq1_semantic_utility_goal_20260713/step3_evidence.json`
- 修改文件：`agent/planning/planner.py`、`prompts.py`、`amendment.py`、planning README 与对应 tests
- 删除内容：删除 ArtifactRequest 中 `proposed_name`“文档可选、validator 实际必需”的漂移；删除 later-stage constant amendment 会注册 identity 却无法提供 grounded value/Stage 4 inventory 的 pending 路径，改为显式回到 authoritative Stage 4
- 新增内容：由 `minimum_v1`、normalized characteristics 与 activated engineering rules deterministic 派生的 protocol-agnostic obligations；Stage 4 `implementation_coverage_matrix`、`test_obligations`、严格 runtime/lifecycle contracts；coverage/registry/provenance commit validator；final plan 对 obligations/coverage/runtime/lifecycle 的保留
- 新增/修改 tests：MQTT minimum Stage 4 fixture 覆盖全部 obligations；missing coverage、duplicate main、lifecycle drift、constant amendment source-stage recovery、required `proposed_name`、target profile input policy
- Targeted tests：Stage 4 synthetic fixture 26/26 obligation coverage、23/23 required test obligations、exactly one main、startup/run/cleanup 与 create/use/destroy 均通过；负 fixtures 均在 Stage 4 拒绝
- 全量 tests：planning 108/108 passed；planning utility 27/27 passed；coder minimum matrix 2/2 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Non-fatal stability sentinel（specs/schema/loader）：Step 2 candidate-only lowering sentinel 与全量 pipeline tests 继续通过；本步未改变 compiler output gate，Stage 4 后发生的 non-fatal semantic/lowering error 仍 materialize schema-valid、coder-loadable specs
- Fatal/non-fatal 分类及 `fatal_reason_code`：Stage 4 无法形成任何 grounded type/function/file inventory 时仍是既有 `candidate_serialization_impossible` fatal；已有可序列化 inventory 的后续 semantic error 不升级 fatal，不允许以清空 inventory 通过门禁
- Validation/metrics：MQTT minimum 从 facts/rules 派生 26 个 obligations（11 foundation + 15 minimum_v1），其中 23 个要求 Stage 9 test realization；coverage fixture=100%，constant identities 非空，registry drift=0
- Planning runs：0；未启动新 LLM planning，避免在 ABI gate 完成前消耗 fresh attempt
- Coder runs：0；仅全量 coder loader/test regression
- Input/model/tool hashes：沿用 Step 0；`target_profile_visible_to_planner=false`、`planner_visible_target_directives=[]`，未把 evaluation MQTT profile 内容注入 prompt
- 本步 Goal token：不可取得增量；Goal tracker 为 `usageLimited`，最后可报告累计 281,593
- 累计 Goal token：281,593（tracker 最后可报告值）
- 新发现问题：later-stage constant request 缺少 wire value contract，接受后无法无发明地完整物化；现明确要求返回 Stage 4，而不是注册半成品 constant identity
- 假设与证据：foundation obligations 是 generic executable network-protocol engineering rules，不包含 MQTT symbol 清单；minimum obligations 的名称/refs 逐项来自当前 facts，target profile 保持 evaluation-only
- 是否阻塞下一步：否
- 后续调整：Step 4 将 Stage 5/6 的 enum value、custom member resolution、callback ABI、OPAQUE pointer policy、wire obligation 和 evidence projection 与本步 canonical inventory 联合闭合
- 净增超过 50 行说明：主要增量是 Stage 4 machine-checkable obligation validator与完整正负 fixture；它替代“仅靠 prompt 列举 focus”的软约束，使 task evaporation、main/lifecycle/constants drift 在最早 source stage 被确定性拒绝，没有新增 fallback pipeline

## Step 4：闭合 Stage 5/6 type、wire constant 与 ABI

**状态：DONE**

### 目标

在 function behavior 和 call graph 之前形成可编译、可引用、pointer/value 一致且有 provenance 的 canonical type model。

### 任务

1. Stage 5 partition commit 前强制：
   - ENUM values 非空；
   - NAME/VALUE 唯一且 schema 合法；
   - normative value 来自 protocol fact/evidence；representation 选择才可由显式 engineering decision/rule 支持；
   - STRUCT/UNION member custom types 全部可解析；
   - CALLBACK signature 完整；
   - OPAQUE ownership boundary 明确。
2. 建立单一 OPAQUE ABI policy：
   - OPAQUE 只能通过 pointer/handle 传递；
   - 只有 concrete STRUCT/UNION definition 才能按值使用；
   - RETURN、PARAMS、callback slots 和 nested declarations 一致检查；
   - 禁止 compiler 把错误 type kind 改写成可加载形式。
3. Stage 5/6 joint validation 检查：
   - signature name、normalized RETURN/PARAMS 与 raw declaration 一致；
   - custom type header visibility；
   - parameter ownership/nullability；
   - callback exact ABI；
   - constructor 返回 pointer/handle；
   - destructor 接受匹配 handle 并进入 cleanup obligation。
4. 对 facts/rules 判定为 codec/parser/encoder/packet dispatcher 的 function 建立 wire obligation；Stage 7 必须兑现，不能按函数名字符串猜测。
5. 修复 fact/evidence projection：不能只保留带 `fact:` 前缀的引用而丢弃 facts 中有效的 evidence ID；Stage 5/7 必须能看到其决策所引用的精确事实/evidence slice。
6. Wire mapping 至少覆盖 packet type、fixed header、remaining length 和当前 minimum behavior 所需 fields/constraints；无法 grounding 时产生明确的 `fact_gap_*` blocking diagnostic。
7. Normative numeric wire constant 必须来自 protocol fact/evidence，不能由 engineering decision、open assumption、model 常识或 compiler 猜测。若当前 facts 缺少唯一映射，保持 facts 只读并形成 upstream facts-agent blocker 证据；只有用户另行授权输入 revision 后，才更新 facts hash 并让 M0/M1/M2 全部重跑。
8. Compiler 只保留/规范化已确定 values，不补造、注释化或省略 required constant/member。

### Hard gate

```text
empty_enum_count = 0
opaque_by_value_count = 0
unresolved_type_count = 0
callback_signature_mismatch_count = 0
opaque_constructor_missing_count = 0
opaque_destructor_missing_count = 0
required_wire_mapping_missing_count = 0
generated_header_standalone_compile_rate = 100%
```

### 完成标准

- targeted tests 和全量 tests 通过；
- 历史失败 fixtures 被阻断在正确 stage；
- closed fixture 可被真实 coder loader 和 header renderer 接受；
- 没有通过删 member、改 OPAQUE 或 compiler fallback 制造通过。

### Step 4 执行记录

- 完成时间：2026-07-13T19:55:00+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：Stage 4 constants/runtime/lifecycle、Stage 5 type partitions、Stage 6 interfaces、Stage 7 behavior partitions、compiler lowering、implementability analyzer、MQTT minimum facts；machine evidence 为 `agent/planning/out/rq1_semantic_utility_goal_20260713/step4_evidence.json`
- 修改文件：`agent/planning/planner.py`、`prompts.py`、`compiler.py`、`implementability.py`、`tests/test_pipeline.py`、`tests/test_implementability.py` 与本任务书
- 删除内容：删除 required wire function 的 name/role/signature 字符串猜测；合并重复 Stage 5 type validator；不增加 compiler 自动改 type kind、补 wire value 或静默删 member 的 fallback
- 新增内容：Stage 5 ENUM/custom type/CALLBACK/OPAQUE commit gate；Stage 6 raw/normalized signature、parameter contract、foreign private type、callback provider、constructor/destructor ABI gate；Stage 4 cleanup/constant grounding gate；从 facts deterministic 派生 wire targets 并在 Stage 6 分配、Stage 7 materialize 的显式链路；保留 `wire_obligation` 到 semantic gate
- 新增/修改 tests：empty/duplicate/schema-invalid/ungrounded ENUM，unresolved member，renamed/incomplete callback，callback/nested/function OPAQUE by-value，signature drift，ownership/nullability，private foreign type，callback provider mismatch，constructor/destructor，constant value/grounding，cleanup drift，wire allocation/materialization，evidence projection，compiler no-rewrite，以及 non-fatal Stage 6 candidate specs sentinel
- Targeted tests：MQTT minimum 从 `message_model` 派生 18 个 wire targets（14 field + 4 constraint），包含 `fixed_header.packet_type`、`fixed_header.remaining_length` 和全部当前 minimum fields/constraints；所有负 fixture 在 Stage 4/5/6/7 的最早 authoritative stage 被拒绝
- 全量 tests：planning 116/116 passed；planning utility 27/27 passed；coder minimum matrix 2/2 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Non-fatal stability sentinel（specs/schema/loader）：Stage 6 ABI correction exhausted 时保留 canonical-shaped candidate、登记 unresolved diagnostic，并以 `allow_incomplete` 跳过 qualification-only semantic gate；实际 candidate module/file/function specs、schema validation、rendered header validation 与 coder loader 均通过。Stage 4/6 只有可保留 candidate 时才降级，结构损坏或无法序列化不伪装为成功
- Fatal/non-fatal 分类及 `fatal_reason_code`：type/wire/ABI、constant fact gap、wire target fact gap 和 bounded correction exhausted 均保持 non-fatal，不设置 fatal code；facts read、registry/canonical corruption、pipeline state corruption 和 `candidate_serialization_impossible` 仍是 fatal
- Validation/metrics：closed fixtures 的 `empty_enum_count`、`opaque_by_value_count`、`unresolved_type_count`、`callback_signature_mismatch_count`、constructor/destructor missing 与 `required_wire_mapping_missing_count` 均为 0；standalone rendered-header acceptance=100%；compiler 保留错误 OPAQUE kind 供 semantic gate 阻断，不把错误改写为可通过表示
- Planning runs：0；ABI/wire gate 完成前未消耗新的 LLM planning attempt
- Coder runs：0；只执行 coder loader/header renderer 与 minimum matrix regression
- Input/model/tool hashes：facts hash=`2842e52800f85da836eb9c4d2f0768757e376315da0a6b608d619088df5905be`；evaluation-only MQTT target profile hash=`4b482108aabc50ea3bdec8ac28d0f6869a5e81253a9bc6886ff3147fb67e1131`，仍未注入 planner prompt；model/tool 沿用 Step 0
- 本步 Goal token：不可取得增量；Goal tracker 为 `usageLimited`，最后可报告累计 281,593
- 累计 Goal token：281,593（tracker 最后可报告值）
- 新发现问题：whole-stage Stage 6 原先在 semantic correction exhausted 后没有可恢复 candidate 路径，会违反“non-fatal 必须产出 specs”；现只对已具备 canonical shape 的 Stage 4/6 semantic candidate 做保留式降级，未放宽 structural/serialization fatal 边界
- 假设与证据：wire target 的 packet/field/constraint 全部来自当前 `message_model` path 与 evidence refs；不使用 MQTT symbol 模板、target profile 或 model 常识；constraint target 通过同 packet/field mapping 的非空 `RULE` materialize
- 是否阻塞下一步：否
- 后续调整：Step 5 进入 typed direct-call、callback binding、provider data flow 与 deterministic dependency closure；继续保持 Stage 4/6 candidate-preservation 和 non-fatal specs invariant
- 净增超过 50 行说明：主要增量是 Stage 5/6/7 machine-checkable semantic gates、facts-derived 18-target wire contract、正负 fixtures 和 non-fatal candidate specs sentinel；这些检查替代 name heuristic 和 compiler 猜测，没有引入第二套 planner、adapter 或兼容分支

## Step 5：闭合 Stage 8 relation、dependency 与 access-provider data flow

**状态：DONE**

### 目标

让每一条 coder-facing call edge 都是方向正确、实际发生、参数可供给、结果有用途、跨文件可见的 direct call。

### 任务

1. 保留 `call_edges` 只表达 direct call；明确排除 callback binding、lifecycle pairing 和 state prerequisite。
2. 如果现有字段无法 typed 表达 callback provider，增加最小的 typed `callback_bindings` relation；不得用 prose 或虚假 direct edge代替。
3. Lifecycle pairing 由 Stage 4 lifecycle matrix 和 Stage 7 behavior materialize；state prerequisite 由 PRECONDITION/invariant materialize。
4. Stage 8 partition validation 对每条 direct call 检查：
   - caller/callee canonical kind；
   - direction 与 behavior evidence；
   - condition 可达且非 placeholder；
   - argument semantics 对应 callee 参数；
   - provider 来自 caller PARAMS、owned state/access path、constructor/provider result、accessor result 或已证明的 prior result；
   - result usage 与 RETURN 一致；
   - visibility 与 owner file；
   - callback function 与 callback type exact match。
5. Dependency 只能从 validated source relations 派生：
   - cross-file direct call -> source dependency；
   - public foreign signature type -> header dependency；
   - private implementation-only foreign type -> source dependency；
   - cross-module dependency 与 generation order 一致。
6. 如果 derived graph 有 cycle，回到错误的 source relation、ownership 或 module decomposition 修复；compiler 不得通过过滤 necessary edge 伪造无环。
7. Semantic patch 必须更新最早 source stage artifact；禁止只删除派生 dependency，因为 deterministic completion 会重新生成。
8. Access-provider analysis 必须使用 typed data flow，不以函数名或 role prose 作为唯一证据。

### Hard gate

```text
callee_dependency_missing = 0
foreign_type_dependency_missing = 0
cross_file_private_function_or_type = 0
access_service_missing = 0
callback_provider_missing = 0
module_dependency_conflict = 0
module_generation_order_conflict = 0
cross_file_call_dependency_missing_rate = 0%
cross_file_type_dependency_missing_rate = 0%
placeholder_or_non_direct_call_edge_count = 0
```

### 完成标准

- direct/callback/lifecycle/state relations 不再混淆；
- 每个 edge 有 typed provider 与 provenance；
- dependency graph 与 relation graph 可双向审计；
- full tests 与 closed multi-file fixture 通过。

### Step 5 执行记录

- 完成时间：2026-07-13T20:12:50+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- 审计文件：Stage 8 prompt/partition validator、function merge、deterministic dependency completion、implementability analyzer、compiler module lowering、semantic patch validator；machine evidence 为 `agent/planning/out/rq1_semantic_utility_goal_20260713/step5_evidence.json`
- 修改文件：`agent/planning/planner.py`、`prompts.py`、`implementability.py`、`compiler.py`、`tests/test_pipeline.py`、`tests/test_implementability.py` 与本任务书
- 删除内容：删除 callback provider 的 function-name/behavior-prose 猜测；删除把 callback binding 当 direct CALL 的 purpose-string heuristic；删除 compiler 为迁就 generation order 而过滤 necessary module dependency 的逻辑；禁止 semantic patch 直接编辑 derived file/module dependencies
- 新增内容：strict typed direct-call contract；独立 `callback_bindings` relation；逐 parameter provider binding 与 prior-result 顺序验证；RETURN/result usage 验证；callback exact ABI；callback provider 到 coder-facing argument contract 的 deterministic projection；direct call/type/callback 到 file/module dependency 的 deterministic completion
- 新增/修改 tests：reachable condition、typed caller-param provider、missing provider、typed callback binding/missing binding、callback provider coder projection、callback-derived source/module dependency、derived dependency patch rejection、cyclic dependency preservation，以及更新 closed multi-file typed provider fixture
- Targeted tests：strict Stage 8 正 fixture通过；unreachable、placeholder、missing provider、callback missing/mismatch、private cross-file 与 cycle fixtures 在 authoritative validator/analyzer 被阻断；closed multi-file fixture dependency completion 后 diagnostics=0
- 全量 tests：planning 119/119 passed；planning utility 27/27 passed；coder minimum matrix 2/2 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Non-fatal stability sentinel（specs/schema/loader）：Stage 8 继续沿用 per-partition correction/rollback；失败 partition 形成 unresolved diagnostic 而不撤销已提交 registry/spec candidate。Cycle candidate 保留全部 necessary dependency 并由 loader 明确报告 `generation_order_violation`，不再静默伪造无环图
- Fatal/non-fatal 分类及 `fatal_reason_code`：relation/provider/dependency/cycle closure 均为 non-fatal semantic failure，不设置 fatal code；只有既定 facts/registry/pipeline/serialization fatal 边界保持 fatal
- Validation/metrics：closed fixture 的 callee/foreign-type dependency missing、cross-file private、access service、callback provider、module conflict、generation-order conflict 与 placeholder/non-direct edge 均为 0；cross-file call/type missing rate=0%
- Planning runs：0；未在 Step 6 readiness gate 前启动新 LLM planning
- Coder runs：0；仅执行 coder minimum matrix 与 loader regression
- Input/model/tool hashes：沿用 Step 4 frozen facts/target hashes；未修改 facts、target profile、model 或 tool policy
- 本步 Goal token：不可取得增量；Goal tracker 为 `usageLimited`，最后可报告累计 281,593
- 累计 Goal token：281,593（tracker 最后可报告值）
- 新发现问题：独立 callback relation 若只留在 planning top-level，coder 看不到 provider symbol；现将 provider function/type/user-data deterministic 投影进对应 `CALL_CONTRACTS.argument_semantics`，但不把 provider伪造成 direct RELY.CALL
- 假设与证据：direct call 的可达性、provider 与 result usage 由 typed fields和 trace refs证明；不以 function name、role 或 behavior prose 作为唯一 provider 证据；lifecycle/state relation 保持各自 authoritative source
- 是否阻塞下一步：否
- 后续调整：Step 6 验证 exactly-one-main 的完整 create/start/run/stop/destroy 与失败 cleanup call chain，并把 readiness 与 required FUNCTION_SPEC/spec preservation 收紧
- 净增超过 50 行说明：增量主要来自 typed relation/provider validator、callback binding projection 与正负 multi-file fixtures；同时删除 name heuristics 和 dependency filtering，没有新增 parallel dependency engine，继续复用既有 deterministic completion

## Step 6：闭合 entrypoint、lifecycle、readiness 与 specs preservation

**状态：DONE**

### 目标

杜绝任务空化、漏 main、漏 lifecycle、漏 FUNCTION_SPEC 和 loader-oriented information loss。

### 任务

1. 验证 exactly one `int main(...)`，并要求其 call chain 覆盖 create/start/run/stop/destroy 与失败 cleanup。
2. Stage 6 必须为所有 registered required functions生成 exactly one complete interface；缺失不能用 empty delta 进入 qualified path。
3. Plan -> specs preservation 必须检查：
   - required type/member/enum value；
   - function/signature/visibility；
   - wire mapping；
   - call contracts；
   - access paths；
   - forbidden symbols；
   - test vectors；
   - schema 支持的 trace refs与可回查的 planning-sidecar decision/rule refs。
4. 删除 compiler 中为 loader compatibility 省略 required member/dependency 的最终 utility 路径；无法安全 lower 时返回 blocking diagnostic 并回到 source stage。
5. Manifest 分别报告：
   - `artifact_success`；
   - `semantic_qualified`；
   - `implementation_ready`；
   - closure/post-validation/union diagnostics；
   - attempt 与 distinct unresolved counts。
6. `implementation_ready=true` 必须意味着固定 no-repair smoke 中满足全部 planning hard gates；在有足够样本后验证其对 initial compile 的预测性。

### Hard gate

```text
runtime_entrypoint_missing_or_ambiguous = 0
required FUNCTION_SPEC materialization = 100%
required type materialization = 100%
required wire/test obligations materialization = 100%
plan_to_spec_required_field_drop = 0
union error diagnostics = 0
qualification_passed = true
```

### 完成标准

- closed end-to-end fixture qualified；
- candidate-only 仍保留完整诊断，但不能被报告为 ready；
- manifest、diagnostics 和实际 specs 一致；
- current artifact-stability tests 不回归。

### Step 6 执行记录

- 完成时间：2026-07-13T20:30:00+08:00
- 状态：DONE
- 起始/结束 revision：`8e65a4a34d18996c5c9e28bd4bebbdb6ebf4ed37` / 同 revision（dirty worktree 保持）
- Machine evidence：`agent/planning/out/rq1_semantic_utility_goal_20260713/step6_evidence.json`
- 修改文件：`agent/planning/planner.py`、`prompts.py`、`implementability.py`、`compiler.py`、`validation.py`、`pipeline.py`、`tests/test_pipeline.py` 与本任务书
- 删除/替换内容：删除 unresolved required type member 的静默省略；改为保留 member identity 的 coder-loadable `void *` sentinel，并同时保存 `source_value` blocking diagnostic，由 preservation gate 阻止 qualified/ready；没有恢复或复制旧 lowering 分支
- Entrypoint/lifecycle：Stage 6 对 registered function interface 做 exactly-once coverage；唯一 `main` 必须具有完整 `int main(void)` 或 `int main(int, char**)` ABI；Stage 8 main partition 必须提交 typed `runtime_flow`，覆盖 startup/create/run/use/cleanup/destroy 的可达性与顺序，并为每个 fallible step 提供 checked result 与 typed failure cleanup mapping
- Test materialization：Stage 9 在 required-obligation 模式下要求每个 canonical function 具有非空 test vector，并要求非空 runtime vectors；empty/missing mapping 不能进入 qualified path
- Plan -> specs preservation：新增 module/file dependency、function `SIGNATURE`、visibility、`RELY`、`CALL_CONTRACTS`、protocol/file forbidden symbols 与 consistency rules 检查；已有完整 `TYPE_SPEC`、wire mapping、access paths、test vectors、sidecar trace/decision/rule refs 检查继续生效
- Manifest：独立输出 `artifact_success`、`semantic_qualified`、`implementation_ready`、`readiness_metrics`、closure/post-validation/union diagnostic counts，以及 attempt/distinct unresolved counts；candidate-only 可为 artifact success，但不能伪装为 semantic qualified 或 implementation ready
- Closed no-repair smoke：固定完整 fixture 得到 `completed_with_qualified_specs`，且 `artifact_success=true`、`semantic_qualified=true`、`implementation_ready=true`、required FUNCTION_SPEC materialization=100%、runtime contract materialized=true
- Candidate stability smoke：unresolved member 仍生成 schema-valid、coder-loadable specs，member identity 未丢失；blocking compiler/preservation diagnostics 保留，`qualification_passed=false`、`implementation_ready=false`、`nonfatal_no_specs_count=0`
- 全量 tests：planning 122/122 passed；planning utility 27/27 passed；coder 26/26 passed；`compileall`、shell `bash -n`、`git diff --check` passed
- Planning runs：0；按任务书要求在 Step 6 readiness gate 完成前未启动 fresh LLM planning
- Coder runs：0；仅执行 deterministic no-repair fixture、loader/header 与 coder unit regression
- 本步 Goal token：不可取得增量；Goal tracker 为 `usageLimited`，最后可报告累计 281,593
- 是否阻塞下一步：否
- 后续调整：进入 Step 7，先执行一个真实 fresh bootstrap；若 non-fatal failure，必须先证明 candidate specs 存在、schema-valid、coder-loadable，再决定最早 authoritative stage 的 resume；不得直接扩大实验
- 净增超过 50 行说明：增量来自 typed runtime-flow schema/validator、readiness materialization metrics、完整 preservation gates，以及 closed/candidate 正负 fixtures；这些都是 Step 6 hard gates 的显式 machine-checkable 实现，未新增 parallel compiler、loader 或 dependency engine

## Step 7：bootstrap fresh 与 Track A compile feedback loop

**状态：IN_PROGRESS**

### 目标

用真实 fresh planning 和冻结 coder 验证 P0 semantic gates 是否转化为 implementation utility。

### 运行顺序

1. 全部静态/单元/integration tests 通过后，运行一个全新 bootstrap fresh planning。
2. 检查 planning hard gates、qualification、loader 和 header self-check。
3. 无论成功或失败，保存完整 prompt/response/manifest/metrics。
   - non-fatal failure 必须额外验证 `specs_root` 存在、schema-valid 且 coder-loadable；否则立即判为 stability regression，停止扩大实验并修复本轮修改。
   - fatal failure 必须验证 `fatal_reason_code` 与证据，不得用 fatal 标签规避 specs 产出要求。
4. 对 specs 执行 coder generation-only，禁止 repair。
5. 在 source 副本上执行统一 pre-repair snapshot、definition coverage、placeholder、near-empty 和 clean compile。
6. 把最早 root cause 映射回 planning authoritative stage：
   - type kind/value/ABI -> Stage 4/5/6；
   - missing behavior/wire -> Stage 7；
   - call/provider/dependency -> Stage 8/10；
   - missing inventory -> Stage 4；
   - lowering loss -> compiler；
   - valid complete specs 上的 generic coder defect -> coder exception gate。
7. 失败 run 可从最早 stage resume 验证最小修复，但 resume 不计 fresh acceptance。
8. 修复后重新跑全套 tests，再启动新的 fresh；不覆盖旧目录。

使用 `run_matrix` 做 Track A 时，将 M2 `--max-repair-rounds` 设为 0，使 runner 仍执行 initial compile；直接使用 coder `--skip-repair` 时，必须紧接统一 diagnostic snapshot 的 clean compile，不能把“只生成未编译”记录成 initial-compile outcome。

Step 1 修通 harness 前，当前 planning/coder CLI 的诊断命令基线为：

```bash
RUN="/home/ljf/SpecForge/agent/planning/out/mqtt_semantic_bootstrap_$(date +%Y%m%d_%H%M%S)"
python3 -m agent.planning plan \
  --facts /home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out "$RUN" \
  --api-key-env ALI_API

python3 -m agent.planning validate --run-dir "$RUN"

SPEC_ROOT="$(jq -r '.specs_root' "$RUN/_planning/run_manifest.json")"
python3 -m agent.coder --spec-root "$SPEC_ROOT" validate
python3 -m agent.coder \
  --spec-root "$SPEC_ROOT" \
  --output-dir "$RUN/coder_no_repair" \
  --max-repair-rounds 0 \
  generate
```

命令返回码不能代替 manifest gate；每次仍必须显式读取 `qualification_passed`、union diagnostics 和 implementation-completeness outputs。

### Development Gate A：首次打通

至少一个真实 fresh run 同时满足：

```text
11/11 stages and all required partitions
qualification_passed=true
union errors=0
all planning hard semantic metrics pass
coder loader and header checks pass
definition coverage=100%
placeholder/near-empty=0
exactly one linkable main
initial clean compile=passed
```

### Development Gate B：稳定打通

首次打通后，重新开始连续 sequence，要求 3 次独立 fresh 均满足 Development Gate A。任一失败，连续计数清零；失败 run 保留为开发证据。

### 完成标准

- 连续 3/3 fresh qualified + implementation complete + initial compile；
- fixed-planning coder variance 与 fresh-planning variance 已分开；
- 没有手工修改 artifacts/source；
- M2 不再靠 task evaporation 获得低 diagnostics。

### Step 7 阶段执行记录（2026-07-14，持续更新）

- 状态：`IN_PROGRESS`；Development Gate A/B 尚未通过，不能把当前 candidate-only runs 记为成功。
- Fresh planning 计数：`2/10`。计数对象是 `mqtt_semantic_bootstrap_20260713_203116` 与 `mqtt_semantic_fresh02_20260714_current`；semantic1–39 均为 resume 或同 artifact compile replay，不增加 fresh 计数。
- 稳定性硬约束继续有效：任何 non-fatal failure 都必须产出 specs；schema/header/coder loader 失败立即停止扩大语义实验并优先修复；空 function/type/test inventory 只能作为带 `task_evaporation_*` error 的失败证据，不能视为稳定性或质量成功。
- semantic25：11/11、specs=true、loader=true、union errors=34；Stage4 callback 与 Stage8 provider 闭包仍失败。
- semantic26：初次 loader=false，原因是 function-pointer `RAW` 与 structured params 漂移；同 artifact `semantic26_loaderfix` 重放后 loader=true，union errors=20。
- semantic27–29：均为 11/11、specs=true、loader=true；union errors 依次为 11、11、10；修复了 callback correction、bare wire target、discriminant-guarded payload cast 与 `mqtt_encode_suback` caller-field 来源。
- semantic30：11/11、specs=true、loader=true、union errors=6，但 Stage6 correction 将 interface inventory 清空，触发 task evaporation；不得作为质量提升证据。
- semantic31：11/11、specs=true、loader=true、union errors=37；证明 Stage6 可保留 36 个 interfaces，但 Stage4 一次 correction 只能看到部分 deficit。
- semantic32：原 run specs=true 但 loader=false，违反 sentinel；compiler 现从精确 `*_create/*_destroy` ABI pair 物化缺失 opaque handle，并保留 blocking diagnostic；同 artifact `semantic32_loaderfix` 已恢复 loader=true，union errors=32。
- semantic33/34：Stage6 的未注册 `fn:<name>` dialect 曾导致 `failed_internal`；现改为已注册 alias canonicalize、未注册发明接口 prune+diagnostic。`semantic33_stage6fix2` 已恢复 11/11、specs=true、loader=true，但因全部 Stage6 候选均未注册而明确触发 task evaporation，仍是失败样本。
- semantic35：Stage4 首次完整通过且保留 43 个 interfaces；最终 11/11、specs=true、loader=true、union errors=38。最早剩余根因是错误 connection lifecycle、缺失 TCP callback binding 与 codec wire partition，后续修复均回到 Stage4/6 authoritative source。
- semantic36–38：用于隔离 primitive accessor false positive、遗漏 callback coverage 与 runtime run-role；中止的 run 保留 prompt/response/manifest，不计 Gate，也不覆盖旧目录。
- semantic39：基于 semantic35 的 Stage4 checkpoint 从 Stage5 resume；resume 时只运行现有 deterministic normalization，未手工修改 artifact。Stage4 validator 已通过，错误 `mqtt_connection_t <- tcp_server_start/stop` lifecycle 已删除，packet/TCP callback groups 已闭合，Stage5 13/13 overlays 无 diagnostics；当前继续执行 Stage6–11。
- fresh02：独立 output directory、`fresh=true`、`resume=false`、`replay=false`；11/11 stages，required partitions 16/17，生成 1/7/30 个 module/file/function specs，30/30 required functions、12/12 required types、30/30 required wire items 已物化，schema/coder loader 通过且 `nonfatal_no_specs_count=0`。但 callback closure 有 2 个 semantic diagnostics，另有 20 个 implementability closure errors；test surface 仅 20/31，故 `artifact_success=false`、`semantic_qualified=false`、`implementation_ready=false`，不计 Development Gate 成功。总 token 977561。
- 当前回归：planning 139/139、planning utility 27/27、coder 26/26、`git diff --check` passed。
- 当前稳定性证据：semantic31、semantic32_loaderfix、semantic33_stage6fix2、semantic35 均满足 `specs_generated=true` 与 `coder_loader_passed=true`；`nonfatal_no_specs_count` 未被允许作为通过条件，`fn:` 同类 internal-failure 已由 semantic33 的相同 artifact 最早 stage resume 验证修复路径。
- 删除内容：未恢复用户删除的 `PLANNING_EVALUATION_STABILITY_PLAN.md`；Stage4 normalization 删除不可能表示 direct owned handle 的 lifecycle relation；Stage6 删除未注册的 `fn:` 发明接口并记录 diagnostic。
- 新增内容：聚合 Stage4 callback/kind/role/accessor deficits、grounded manager-handle recovery、visibility dialect normalization、multi-callback owner-group closure、Stage8 buffer-size/local-value 与 payload/provider normalization、compiler opaque-handle materialization。
- 是否阻塞下一步：否；但在 semantic39 达到 qualified + initial compile 前不得启动新的 fresh Gate A attempt。

## Step 8：提升 behavior contracts、executable vectors 与 decision traceability

**状态：TODO**

### 启动条件

只有 Step 7 Development Gate B 完成后才启动。若 compile gate 未稳定，不提前进行 token optimization 或大规模 behavior prose 扩充。

### 目标

让 coder 不再依赖 generic prose 猜测 state transition、wire behavior、failure path 和 ownership，并让高风险 engineering decisions 可解释、可回退。

### 任务

1. Stage 7 对 behavior-bearing functions 生成类型化 LOGIC/EVENT：
   - concrete INPUT；
   - PRECONDITION；
   - ACTION/STATE_CHANGE；
   - POSTCONDITION/RESPONSE；
   - INVARIANTS_USED；
   - failure behavior；
   - ownership/lifetime effect。
2. Behavior-bearing function 集合由 minimum obligations、statefulness、wire relevance 和 engineering rules 决定，不由固定 MQTT function names 决定。
3. 将关键 state/buffer/handle 的 read/write 路径 materialize 到 function `ACCESS_PATHS`。
4. 将禁止调用、禁止 wire encoding、禁止状态转换和 out-of-scope feature materialize 到 function/file/module `FORBIDDEN_SYMBOLS` 或等价 negative constraints。
5. Stage 9 vectors 必须包含 concrete bytes/state/input、expected return/output/state、error path 和 trace refs；空 inputs 不计 coverage。
6. 每个 MQTT minimum required behavior 至少映射到一个 planning-linked executable vector；optional interop 单独标记。
7. 对 type representation、pointer/value、ownership、wire constant、call direction、lifecycle 和 error behavior 写入 `decision_refs`/`rule_refs`。
8. Validator 检查引用不仅存在，而且引用要求已经在 artifact 中物化；orphan refs 失败。
9. Compiler 将已有 function ACCESS_PATHS/FORBIDDEN_SYMBOLS/trace fields 无损写入 FUNCTION_SPEC，禁止继续固定输出空数组。

### Hard gate

```text
generic_logic_template_rate <= 10%
behavior-bearing PRECONDITION coverage = 100%
behavior-bearing POSTCONDITION coverage = 100%
stateful/wire INVARIANTS_USED coverage = 100%
required function ACCESS_PATHS coverage = 100%
required negative constraints coverage = 100%
empty_test_input_count = 0
minimum required behavior vector coverage = 100%
high-risk decision/rule grounding coverage = 100%
orphan decision/rule refs = 0
```

### 完成标准

- semantic density metrics 全部过门；
- plan -> specs preservation 过门；
- 连续 3-run compile stability 不回归；
- test vectors 能驱动或映射到统一 behavior verifier，而不是仅增加数量。

## Step 9：Track B 小样本工程验收

**状态：TODO**

### 目标

在正式 30-run 实验前，证明 M2 的 compile success 能继续转化为 runtime 与 required behavior，而不是只改善 C 语法。

### 任务

1. 使用冻结 coder、repair budget 和 verifier 对 S4 reference specs 做一次工程 sentinel；若 S4 同时回归，先诊断 coder/evaluator，不把失败归因于 planning。
2. 预声明 5 个 fresh M2 pilot attempts 和执行顺序；每个失败都保留，不静默 rerun。
3. 每个 attempt 完整执行：

```text
fresh planning
-> qualification/readiness
-> coder generation
-> initial compile snapshot
-> bounded native coder repair
-> final compile
-> runtime start
-> 5 required behavior scenarios
-> optional interop separately
```

4. 保存 planning/coder/repair tokens、wall time、source hashes、C1-C5 before/after、definition coverage 和 scenario-level outcomes。
5. 若代码修改后重新开始 pilot，新 sequence 使用新 ID；旧失败 sequence 仍保留在开发报告，不与新 revision 混合。

### Pilot gate

- final compile 至少 4/5；
- required-behavior E2E 至少 3/5，推荐 4/5；
- compile-success run 的 verifier 实际执行率 100%；
- successful run 的 planning-dependent remaining defects 为 0；
- fixed-planning 与 fresh-planning variance 分开报告。

### 完成标准

- Pilot gate 全部满足；
- 固定失败 scenario 已定位并回到 planning contract；
- 已确定 publication revision、inputs、models、budgets 和调度协议。

## Step 10：冻结 revision 并执行正式 RQ1 实验

**状态：TODO**

### 启动条件

Step 0-9 全部 `DONE`。正式 run 开始后不得继续修改 planning/coder/evaluator；任何代码变化都使当前 publication sequence 作废并需要新 revision 重跑。

### Revision 冻结

Codex 不得自行 commit 或 push。使用以下组合冻结工作树：

```text
HEAD commit hash
git diff --binary hash
untracked task/report artifact hashes
facts hash
target profile hash
planning/coder/evaluator source hashes
model snapshot and sampling config
compiler/toolchain versions
behavior verifier revision
repair budgets
optional interop availability
```

如果用户之后授权并创建 clean commit，revision 改变后必须重新确认正式 sequence 的一致性。

### 正式实验设计

```text
M0 FS-Direct-Coder × 10 fresh attempts
M1 NL-Plan-Code × 10 fresh attempts
M2 Full-SpecForge × 10 fresh attempts
```

要求：

1. 同一日期窗口；
2. 每个 replicate 内三种 method 使用相同 inputs、revision、model snapshot、toolchain、verifier 和 repair budgets；
3. 外层随机化或轮换 method order，不能固定 M0 -> M1 -> M2；
4. 每个 attempt 独立 output directory；
5. M2 必须 fresh planning；
6. Track A no-repair 与 Track B native repair 分开；
7. fixed-planning coder variance 单独分析；
8. 原始 prompts、responses、usage、specs、code、compile logs、repair logs 和 behavior logs全部保存。

### 失败与分母规则

以下正式结果均保留在预声明分母并记为 method failure：

- planning generation/validation/qualification failure；
- JSON retry exhausted；
- source/static/header failure；
- compile failure；
- runtime startup failure；
- required behavior failure。

只有 API authentication outage、network transport outage、disk failure 或 experiment harness crash 等预先定义的 infrastructure failure 可排除。排除 run 仍需保存并报告 exclusion reason/count。optional interop 缺失不是 infrastructure exclusion。

特别注意：RQ1 semantic report 中的“单 run semantic gate”只能作为正式实验前的 development gate，不能作为 publication sample inclusion 条件。不得因 M2 未 qualified、未 compile 或未执行 behavior 而从正式分母删除该 run。

### 统计分析

对 primary metrics 报告：

- raw numerator/denominator；
- exact binomial 95% CI；
- absolute risk difference；
- relative risk 或 odds ratio；
- M0 vs M1、M1 vs M2、M0 vs M2 的 two-sided Fisher exact test；
- 三组 planned comparisons 的 Holm correction。

对 token/time/defect counts 报告 median、IQR 和 bootstrap CI。C1-C5 必须由同一冻结的 read-only classifier 对三种方法的 pre-repair project 统一计算。

### RQ1 正向主张最低门槛

必须同时满足：

1. M2 final compile raw rate 严格高于 M0 和 M1；
2. M2 required-behavior E2E raw rate 严格高于 M0 和 M1；
3. M2 E2E 至少 6/10；当 M0/M1 都是 0/10 时，6/10 对 0/10 的 two-sided Fisher 约为 0.0108，三比较 Holm 后约为 0.0325；
4. M2 initial compile 至少 5/10，推荐 6/10；
5. M2 runtime start 至少 6/10；
6. required scenario aggregate 推荐至少 40/50，且每个固定 scenario 单独报告；
7. M2 generated project 不出现 task evaporation，所有进入 coder 的 required specs definition coverage 为 100%；
8. M2 roots/source kLoC median 低于历史 M0 的 45.5，并报告同日相对结果；
9. 任何“C2/C4/C5 也严格优于两 baseline”的文字主张都要求相应同日 median 严格更低；历史参考目标为 C2=0、C4=0、C5<=2；
10. effect size、raw outcomes 和失败分类共同支持结论，不能只报告 p-value。

如果 primary gate 未达到，不能写 M2 优于 baseline；应透明报告 negative/mixed result，并继续回到最早失败 Step，而不是选择性删除样本。

### 完成标准

- 30 个预声明 attempts 与 exclusions 完整归档；
- primary/secondary/diagnostic/cost metrics 聚合可重算；
- selection-bias 审计通过；
- 达到 RQ1 正向主张最低门槛。

## Step 11：冻结成果、pruning 与最终报告

**状态：TODO**

### 目标

形成可复核的 planning semantic utility freeze point，并明确研究结论的适用边界。

### 任务

1. 更新本任务书所有 Step 状态与 run ledger。
2. 更新 `PLANNING_CURRENT_STATUS_REPORT.md`，明确区分：
   - protocol facts；
   - inferred engineering decisions；
   - open assumptions；
   - artifact stability；
   - semantic readiness；
   - implementation utility；
   - RQ1 system-level outcome。
3. 更新 RQ1 semantic report 或新增最终报告，不能覆盖原始 negative pilot 证据。
4. 执行完整验证：

```bash
python3 -m compileall -q agent/planning evaluation/planning_utility
python3 -m unittest discover -s agent/planning/tests -p 'test_*.py' -v
python3 -m unittest discover -s evaluation/planning_utility/tests -v
python3 -m unittest agent.coder.tests.test_minimum_matrix_runner -v
git diff --check -- agent/planning evaluation/planning_utility
```

5. 检查 `git diff --stat` 和完整 `git diff`。
6. 进行 pruning pass：删除 dead code、unused imports、stale comments、旧 CLI branch、重复 compatibility path 和本轮新增的冗余 helper。
7. 如果净增超过 50 行，按文件说明新增为何必需；优先说明 tests、typed validation 和 reproducible evaluator 的必要增量。
8. 汇总删除内容与新增内容，不只报告新增。
9. 保存 final revision/hash、input hashes、run paths、statistical outputs 和剩余风险。

### 完成标准

- tests、diff check、pruning 全部完成；
- 文档与 machine-readable results 一致；
- RQ1 claim 不超出 MQTT minimum profile、gold facts 和 system-level comparison；
- 本 Goal 的成功停止条件全部满足。

## 8. 每步执行记录模板

每完成一个 Step，立即追加：

```markdown
### Step N 执行记录

- 完成时间：
- 状态：
- 起始/结束 revision：
- 审计文件：
- 修改文件：
- 删除内容：
- 新增内容：
- 新增/修改 tests：
- Targeted tests：
- 全量 tests：
- Non-fatal stability sentinel（specs/schema/loader）：
- Fatal/non-fatal 分类及 `fatal_reason_code`：
- Validation/metrics：
- Planning runs：
- Coder runs：
- Input/model/tool hashes：
- 本步 Goal token：
- 累计 Goal token：
- 新发现问题：
- 假设与证据：
- 是否阻塞下一步：
- 后续调整：
```

不得省略失败命令和失败 run。不得等 Goal 结束后补造 token、time 或 result。

## 9. Development run ledger

| Sequence | Run | Revision | Fresh/Resume/Fixed | 11/11 | Required partitions | Qualified | Union errors | Semantic hard gates | Specs M/F/Fn | Definition coverage | Placeholder/near-empty | Initial compile | Final compile | Required behavior | Tokens | 是否计入对应门禁 | 结果 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- | --- | --- | ---: | ---: | --- |
| Bootstrap | `mqtt_semantic_bootstrap_20260713_203116` | `8e65a4a...` dirty | Fresh | 10/11 | 1/2 | 否 | 1 | 否：`deterministic_internal_invariant` | 0/0/0 | 0% | 是：无 specs | N/A | N/A | N/A | 163846 | 是，fresh 1/10；不计 Gate 成功 | fatal；`fatal_reason_code=deterministic_internal_invariant`，`specs_generated=false`；随后只用 resume 修复 |
| Fresh 2 | `mqtt_semantic_fresh02_20260714_current` | `8e65a4a...` dirty | Fresh | 11/11 | 16/17 | 否 | 22 | 否：Stage4/8 callback closure、dependency closure 与 runtime call chain 未闭合 | 1/7/30 | N/A | 否；但 test surface 仅 20/31 | N/A | N/A | N/A | 977561 | 是，fresh 2/10；不计 Gate 成功 | non-fatal candidate；specs/schema/coder loader 通过，`artifact_success=false`、`semantic_qualified=false`、`implementation_ready=false` |

Run ledger 必须链接或记录以下 machine-readable paths：

```text
_planning/run_manifest.json
_planning/diagnostics.json
_planning/semantic_closure/implementability_report.json
_planning/implementation_plan.json
specs_root
coder_out/_agent_logs/run_manifest.json
pre_repair_diagnostics.json
semantic_quality.json
behavior result
run summary
```

每个 planning attempt 还必须记录 fatal/non-fatal 分类、`specs_root` 是否存在、schema validation、coder loader 和 `nonfatal_no_specs_count`；失败 attempt 不得留空这些字段。

## 10. Publication attempt ledger

正式实验开始前先写满预声明的 method/replicate/order，不得事后只登记成功 run：

| Replicate | First | Second | Third | M0 output | M1 output | M2 output | Infrastructure exclusion | Exclusion reason | Revision/input hashes |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 2 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 3 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 4 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 5 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 6 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 7 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 8 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 9 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |
| 10 | 待随机化 | 待随机化 | 待随机化 |  |  |  | 否 |  |  |

## 11. 决策与假设记录

每个高风险 engineering decision 至少记录：

| Decision ID | Artifact/field | 选择 | Protocol fact refs | Engineering rule refs | Open assumption | Alternative rejected | Validator/test | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 待记录 |  |  |  |  |  |  |  |  |

必须重点覆盖：

- type kind；
- enum/wire value；
- pointer/value ABI；
- ownership/lifetime；
- callback binding；
- call direction；
- access provider；
- module/file ownership；
- lifecycle；
- runtime entrypoint；
- error behavior；
- required test oracle。

## 12. 优先级与延后项

### P0：必须先完成

1. RQ1 harness/CLI/target-profile integration；
2. reproducible semantic/pre-repair metrics；
3. type/wire/ABI closure；
4. relation/dependency/provider closure；
5. entrypoint/lifecycle/readiness；
6. definition/placeholder/near-empty gates；
7. fresh qualified + initial compile stability。

### P1：P0 稳定后完成

1. typed behavior contracts；
2. executable test vectors；
3. function ACCESS_PATHS/FORBIDDEN_SYMBOLS；
4. decision/rule traceability；
5. runtime/behavior utility；
6. formal RQ1 experiment。

### P2：只有 P0/P1 稳定后考虑

- token/context compression；
- qualified partition replay；
- latency optimization；
- 多协议迁移；
- schema field ablation；
- 非 MQTT generalization。

P2 不得阻塞本 Goal 的 MQTT RQ1 主线，也不得在 successful yield 为 0 时优先优化。

## 13. Goal 停止条件

### 13.1 成功停止

只有以下条件全部满足时，才允许结束 Goal 并标记 `complete`：

```text
Step 0-11 全部 DONE
+ planning artifact stability 无回归
+ nonfatal_spec_materialization_rate=100% 且 nonfatal_no_specs_count=0
+ planning/planning_utility/coder relevant tests 全部通过
+ 连续 3 次 development fresh qualified + definition-complete + initial compile
+ behavior/traceability hard gates 通过
+ 5-run Track B pilot gate 通过
+ 30 个预声明 RQ1 attempts 完整归档
+ publication 分母/exclusion/统计审计通过
+ M2 达到 RQ1 正向主张最低门槛
+ 最终报告、diff、pruning 和 hash freeze 完成
```

达到后，最终结论只能限定为：

> 在冻结的 MQTT minimum broker profile、gold protocol facts、model/toolchain 和 system-level evaluation 条件下，SpecForge planning agent 生成的 implementation-oriented protocol specs 提升了 coder agent 的 downstream implementation utility，并在预声明的主要指标上优于 FS-Direct-Coder 与 NL-Plan-Code。

不得扩大为完整 MQTT compliance、跨协议 generalization 或 facts agent extraction quality 结论。

### 13.2 Fresh planning 10-run 硬上限停止

本 Goal 自 Step 7 启动后，最多允许启动 10 次 planning-agent fresh attempts。计数对象必须满足 fresh=true、resume=false、replay=false 且使用独立 output directory；development、pilot 和 publication M2 fresh attempts 全部累计，成功、semantic/qualification/compile 失败以及 fatal attempt 均计数。Step 7 之前的历史 runs、resume、replay、fixed-planning coder run 和 M0/M1 coder-only attempts 不计数。

第 10 次 fresh attempt 完成或终止并保存当前可获得的 artifacts、manifest 和 diagnostics 后，必须立即停止继续优化，不得再修改 planning/coder/evaluator、执行 resume、启动新的 fresh planning 或扩大实验。随后立即冻结当前 revision 与 run ledger，汇总 10 次 fresh 的逐 run 结果、累计 tokens、失败阶段、semantic gaps、compile/behavior 结果、相对 M0/M1 的结论和未完成项，出具最终总结报告并结束本 Goal。

若此时已满足 13.1，则标记 complete；否则将任务书状态标记为 DEFERRED，记录 stop_reason=fresh_planning_attempt_limit_reached，不得伪标记为 complete 或 blocked。

### 13.3 允许阻塞停止

只有以下情况在连续至少三个 Goal turns 复现、且没有安全范围内进展时，才允许标记 `blocked`：

- API credential 缺失或外部 model service 持续不可用；
- 文件系统、compiler/toolchain 或 experiment environment 持续损坏；
- protocol facts/target profile 存在可证明的矛盾或信息缺口，使 required behavior 无法在“不发明事实”的约束下 specification；
- 冻结 coder 的公开 contract 与现有 schema 在逻辑上无法同时满足，并已有 protocol-agnostic 最小复现；
- 完成目标需要用户授权的 scope expansion，例如不可避免的非 generic coder redesign。

阻塞报告必须包含：

- 已完成 Steps；
- 同一 blocker 的三次连续证据；
- 最小复现命令；
- 最早失败 stage；
- 已尝试的最小修复；
- 为什么 resume、fixture 或 generic alternative 无法继续；
- 需要用户作出的具体决定；
- 当前代码/tests/run artifacts 状态。

### 13.4 不构成阻塞或完成的情况

在尚未触发 13.2 的 fresh planning 10-run 硬上限、也未满足 13.3 的阻塞条件时，以下情况一律继续工作：

- semantic diagnostics 仍非零；
- qualification、compile、runtime 或 behavior 失败；
- 某一轮 fresh 消耗较多 token；
- 统计结果暂时不显著；
- M2 暂时没有超过 baseline；
- implementation patch 净增超过 50 行但尚可继续 pruning；
- 已完成 loader stability 或单次 compile success；
- 已达到 development gate 但尚未完成正式实验。

失败结果应驱动下一轮最小、source-stage、facts/rules-grounded 修复，而不是提前结束 Goal。
