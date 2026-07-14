# SpecForge Planning Specs-to-Bounded-Repair Compile Goal 任务书

> 初始制定日期：2026-07-13
> 本次修订日期：2026-07-14
> 核心对象：planning agent
> 当前状态：`IN_PROGRESS`
> 当前步骤：`Step 7`
> 状态标记：`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED` / `DEFERRED`

## 0. 使用方式

本文件只回答一个工程问题：

> planning agent 的独立 fresh 输出，能否形成非空、schema-conformant、coder-loadable 的 protocol specs，并让 coder agent 在最多 3 轮 bounded repair 后完成 clean compile？

后续执行必须遵守：

1. 每次 continuation 先读取本文件、最新 fresh manifest、最近 compile/repair summary 和当前 `git diff`。
2. 同一时刻只允许一个 Step 为 `IN_PROGRESS`。
3. `Step 0–6` 已冻结为完成基础；除非新 fresh 直接证明 regression，否则不重新展开或重复执行。
4. `Step 7–9` 各自只有 **1 次** post-change fresh acceptance attempt；不要求连续多次成功，也不得在同一 Step 内换 revision 后继续 fresh。
5. 只有最终 `Step 10` 允许并要求多次 fresh，用于验证冻结 revision 的稳定性。
6. failed fresh 必须保留，不能静默 rerun、覆盖目录或从记录中删除。
7. resume、replay 和 fixed-spec coder run 只用于定位；不能替代 post-change fresh acceptance。
8. 不人工修改 fresh/resume specs，不人工修改 generated source 来制造 compile success。

本修订版最多预留 6 个新增 fresh slots：Step 7、8、9 各 1 个，Step 10 固定 3 个。§6.1 的三个历史 fresh 只作证据，不占新版 slots；被最终目标优先规则跳过的 slot 直接取消，不能挪作失败替补。任一 slot 失败均不得在本 Goal 内补跑。

## 1. 唯一目标、成功语义与非目标

### 1.1 唯一目标

每个 acceptance run 内保持以下输入和边界冻结；进入 Step 10 后再冻结最终 revision：

- MQTT minimum broker protocol facts；
- specs schema 与 coder loader contract；
- coder generation/repair revision；
- model、sampling config、compiler 和 toolchain；
- bounded repair budget：`max_repair_rounds=3`；

让真实 fresh planning 产出的 protocol specs 经 coder generation 和 bounded repair 后得到可链接 binary。

唯一 outcome endpoint 是 `final clean compile passed`。schema/loader、非空 inventory、禁止人工修改和 token ceiling 只用于证明输入真实可消费、过程未绕过且成本受控，不构成并列的实验质量目标。

### 1.2 有意义的单 run 成功

单次成功必须同时满足：

```text
fresh=true
resume=false
replay=false
独立 output directory
specs materialized
schema validation passed
coder loader passed
required module/file/function inventory 非空
coder generation completed
repair_rounds_used <= 3
final clean compile passed
manual artifact/source edit count = 0
```

required inventory 非空用于防止空项目通过 compile；除此之外，不增加 compile 之外的最终质量否决门禁。

以下指标必须记录，但不是最终成功的独立否决条件：

```text
qualification_passed
union diagnostic count
initial clean compile
compile diagnostic category counts
specified-function definition coverage
placeholder_count
near_empty_required_source_count
repair files/calls/tokens
planning tokens
```

特别地：

- initial compile 失败，但在固定预算内 final compile 成功，仍满足本 Goal 的 compile 目标；
- `qualification_passed=false` 不能隐藏 specs 或阻止 fixed diagnostic，但必须保留 diagnostics；
- diagnostics 数量下降不能替代 final clean compile。

### 1.3 非目标

本 Goal 不包含：

- runtime startup 或协议行为验收；
- 完整 MQTT compliance 或 interoperability；
- 大规模对比运行或统计检验；
- 多协议泛化；
- facts agent extraction quality；
- token/latency optimization 作为独立研究目标；
- 通过扩充 function、test vector 或 prose 数量制造表面质量提升。

## 2. 修改范围与不可退让约束

### 2.1 默认允许修改

优先只修改：

```text
agent/planning/
evaluation/planning_utility/no_repair_analysis.py 及其现有 tests
evaluation/planning_utility/ 中现有 compile/repair summary 的最小必要修复
```

修改必须是最小 bug fix 或现有逻辑替换，不新增第二套 pipeline、adapter、registry、fallback 或协议专属分支，也不调用或扩展旧的大规模运行调度入口。

### 2.2 默认只读

```text
agent/facts/
specs-example/
specs-example/specs_schema/
protocol-example/
既有 run artifacts 与 generated source
```

不得修改 protocol facts 来补足 planning 缺口，不得从 reference specs 或 protocol example 复制 inventory、signature、wire value、call graph 或实现行为。

### 2.3 coder 修改例外

`agent/coder/` 默认只读。只有同时满足以下条件，才允许最小 generic bug fix：

1. 问题可由 protocol-agnostic、schema-valid fixture 独立复现；
2. 问题属于 coder 对公开 specs/repair contract 的实现缺陷，而不是 specs 信息不足；
3. 先增加失败 regression test；
4. 不增加 MQTT 名称判断，不改变 coder prompt 来偏向当前 specs；
5. 不增加宽泛的“repair all files” fallback；
6. 修改后运行完整 coder tests，并在下一次 fresh 前冻结新 revision。

### 2.4 non-fatal specs materialization

semantic、qualification、dependency、callback、entrypoint 或 test-surface 问题默认是 non-fatal：

- 必须写入 machine-readable diagnostics；
- 必须基于最后已提交的 schema-valid state materialize candidate specs；
- candidate specs 必须尽可能保持 required inventory，并通过 schema 与 coder loader；
- 不得用 early abort、清空 inventory、跳过 compiler 或 suppress output 保持表面稳定。

fatal 仅限 facts 无法读取/解析、文件系统不可写、model service 在取得有效 stage artifact 前持续不可用，或确实无法继续 serialization 的内部异常。取得可序列化 inventory 后发生的 stage JSON/retry exhaustion 是 non-fatal unresolved：必须保留最后已提交 state 并 materialize candidate specs。fatal manifest 必须保留失败 stage、原始响应和明确 reason code。

## 3. Prompt 与 token 硬约束

Prompt 修改是最后手段，不是默认修复路径。

### 3.1 修改顺序

任何缺陷按以下顺序处理：

```text
deterministic control flow
-> typed validation/normalization
-> compiler preservation
-> protocol-agnostic coder contract bug
-> 最早 authoritative LLM stage
```

dependency completion、manifest accounting、fatal classification、linker diagnostic routing 等确定性问题，禁止通过 prompt 修复。

### 3.2 Prompt 修改准入

只有同时满足以下条件才允许修改 `prompts.py` 或 stage prompt contract：

1. 同一 semantic deficit 已由 fresh artifact 和最小 fixture 复现；
2. facts 中存在必要信息；
3. schema/typed IR 能表达该信息；
4. deterministic logic 无法唯一推导；
5. 修改只针对一个 authoritative field family；
6. 使用冻结 model tokenizer 计算时，同一 representative fixture 上受影响请求的 input tokens 不增加；同时保留 prompt characters 作为辅助证据。

允许的 prompt 变更形式是删除、替换、投影和去重。禁止：

- 注入完整历史 diagnostics、完整 plan 或长篇执行报告；
- 在每个 partition 重复全部 functions/types/facts；
- 增加泛化 prose 来覆盖确定性 bug；
- 同时修改多个 high-risk stages；
- 为测试 token compression 单独消耗 fresh。

### 3.3 Token ceiling

历史完整 fresh 曾消耗 `977561` planning tokens；该值仅作为过高成本证据，不再作为允许预算。后续所有 fresh（无论成功、失败或 fatal）冻结为：

```text
single request input tokens <= 64000
single response tokens <= 16000
single stage total tokens <= 220000
whole fresh planning total tokens <= 800000
JSON repair <= 1 per failed response
semantic correction <= 1 per partition
additional JSON repair/semantic correction calls <= 4 per fresh
```

`whole fresh planning total tokens` 必须包含原始 stage calls、JSON repair、semantic correction、semantic patch、失败调用以及任何其他 planning model call；不得只读取遗漏 retry 的旧 manifest 聚合值。

- budget 对成功和失败 run 同样生效，且不得因失败而上调；
- 每次 fresh 必须记录 per-call、per-stage 与 total token usage，并写入 `token_accounting_complete`；
- 发起下一次 model call 前，使用冻结 tokenizer 计算 projected usage；预计越界时不得调用；
- 已存在可序列化 inventory 时，budget exhaustion 必须 materialize candidate specs 并记为 non-fatal failure；
- prompt 修改必须在同一 fixture 上提供 tokenizer tokens 与 characters 的 before/after evidence；
- 若必要信息无法在 ceiling 内表达，停止扩大 prompt，形成 blocker 报告。

Token 降低本身不构成 Step success；compile utility 始终是最终 endpoint。

## 4. 固定定义与统一执行链

### 4.1 Fresh、resume 与 fixed

- `fresh`：`fresh=true`、`resume=false`、`replay=false`，独立目录，从 Stage 1 开始。
- `resume`：从最早 authoritative stage 验证一个修复点；同一 failed fresh 最多使用一次，不计 Step acceptance。
- `fixed-spec`：固定同一 specs，测 coder generation/repair variance；不计 fresh acceptance。

### 4.2 Compile/repair

- `initial clean compile`：任何 repair 前，在 source hash 保持不变的副本中 clean build。
- `bounded repair`：最多 3 轮 coder native repair；不得人工补 patch。
- `final clean compile`：最后一次 repair 后重新 clean build，目标 binary 可链接；不能以 generation CLI return code代替。
- repair 若因 diagnostics routing 提前返回，必须记录真实 stop reason，不能把未使用预算写成 repair exhausted。

### 4.3 每个非最终 Step 的固定协议

```text
读取最近 fresh 的 machine diagnostics
-> 选择一个最早 authoritative root-cause family
-> 写最小 regression fixture
-> 修改最小文件集合
-> targeted tests
-> planning + planning utility + coder relevant full tests
-> 恰好 1 次 post-change fresh
-> schema/loader validation
-> coder generation + initial snapshot + <=3 rounds repair
-> final clean compile snapshot
-> 更新本文件和 compact ledger
```

同一 code/diff revision 不得因结果不理想而重复 fresh。每个 `Step 7–9` 只有 ledger 中预留的 1 次 fresh；该 attempt 失败时保存证据、将 Goal 标记为 `DEFERRED` 并停止自动执行，不在同一 Step 内修改后重试。只有用户明确修订任务书后才可新增 attempt。不得再次形成同一 artifact 的无界 resume 链。

最终目标优先：若 Step 7 或 Step 8 的唯一 fresh 已满足第 1.2 节单 run compile success，且相关 generic regressions 全部通过，则不再为 semantic-zero 指标消耗 fresh。尚未执行、且只用于建立 compile feasibility 的 pre-final Step 记为 `DONE（earlier fresh already met goal）`，取消其 fresh slot并直接进入 Step 10；不得把取消的 slot用作其他失败 run 的替补。

## 5. Step 0–6 已完成基础摘要

以下能力保持 `DONE`，详细历史不再留在正文中：

| Step | 已冻结能力 | Machine evidence |
| --- | --- | --- |
| 0 | revision/input/tool hashes、artifact stability baseline 与 non-fatal sentinel | `agent/planning/out/rq1_semantic_utility_goal_20260713/step0_baseline.json` |
| 1 | current planning CLI -> manifest `specs_root` -> coder validate/generate-only -> no-repair snapshot；fatal/non-fatal 分类 | `evaluation/planning_utility/tests/test_planning_utility.py` 的 current-CLI/manifest/candidate tests 与 `tests/test_no_repair_analysis.py` |
| 2 | plan -> specs preservation、union diagnostics、sidecar mapping；历史 artifacts 只读 round-trip 无新增 silent drop | `agent/planning/out/rq1_semantic_utility_goal_20260713/step2_evidence.json` |
| 3 | facts/rules-grounded implementation obligations、required inventory、唯一 `main` 与 lifecycle identity | `agent/planning/out/rq1_semantic_utility_goal_20260713/step3_evidence.json` |
| 4 | Stage 5/6 type、wire 与 ABI gates；non-fatal candidate 仍可加载 | `agent/planning/out/rq1_semantic_utility_goal_20260713/step4_evidence.json` |
| 5 | typed direct calls、独立 `callback_bindings`、argument provider、result usage 与 dependency derivation | `agent/planning/out/rq1_semantic_utility_goal_20260713/step5_evidence.json` |
| 6 | `runtime_flow`、readiness、required artifact materialization 与 plan-to-spec preservation | `agent/planning/out/rq1_semantic_utility_goal_20260713/step6_evidence.json` |

当前回归基线（2026-07-14 本次复核）：

```text
planning tests: 141/141 passed
planning utility tests: 27/27 passed
coder tests: 26/26 passed
compileall: passed
git diff --check: passed
```

Step 0–6 只在新 fresh 出现直接 regression 时回到对应最早 Step；不得继续为它们扩充抽象或 prompt。

上述包含 `rq1_` 的目录名是 legacy read-only evidence path，仅用于复核已完成基础；不得据此恢复旧运行计划。

## 6. 当前证据与失败归因

### 6.1 Fresh ledger

| Run | Planning | Specs/loader | Coder/repair | Tokens | 结论 |
| --- | --- | --- | --- | ---: | --- |
| `mqtt_semantic_bootstrap_20260713_203116` | 10/11，fatal | 无 specs | 未运行 | 163846 | 内部 identity/invariant failure；不是 utility success |
| `mqtt_semantic_fresh02_20260714_current` | 11/11，16/17，22 union errors | 1/7/30 specs，loader passed | initial 29/30 definitions、12 roots；repair 1/3 后剩 2 linker roots，final compile failed | 977561 | artifact quality 明显提升，但未达到 compile 目标 |
| `mqtt_prompt_dedup_fresh_20260714_113738` | 8/11，fatal | 无 specs | 未运行 | >=718154 | Stage 9 原始 JSON 与一次 repair 均非法；失败前已有 Stage 4/8 unresolved |

三个 fresh 累计至少 `1859561` planning tokens，当前为 `0/3` final compile。该数字只记录既有成本与失败，不改变第 0 节预留的 6 个新增 fresh slots，也不授权无变化重跑。

### 6.2 代表性 fixed evidence

最佳现有 candidate 是：

```text
agent/planning/out/goal_task_backup/
  mqtt_semantic_bootstrap_semantic45_stage8_all_commit_20260714_220000/
```

其状态为 11/11 stages、20/20 partitions、43/43 required functions、13/13 types、29/29 wire targets、44/44 test surfaces、coder loader passed，但仍有 3 个 runtime closure errors，尚未执行 coder bounded-repair diagnostic。

该 run 的 manifest 因目录搬迁保留了旧 `specs_root`。使用时必须从当前 physical candidate specs path 读取，并记录 `manifest_path_stale=true`；不得改写原 manifest 伪造历史路径。

Fresh 2 coder machine evidence：

```text
evaluation/planning_utility/out/rq1_fresh02_fixed_coder_20260714/
  initial/pre_repair_summary.json
  repaired/post_repair_summary.json
  repaired/mqtt_repair_20260714_103758/_agent_logs/006_compile_stderr_1.txt
```

repair 已清除原有 source/header compile categories，但引入 duplicate `mqtt_decoder_feed` 与 undefined `mqtt_decode_and_dispatch`，随后以 `no_repairable_sources` 提前停止。该结果证明 planning 与 coder repair control 都需要区分归因。

### 6.3 不再继续的路径

现有 `semantic*` 系列包含 55 个 semantic manifests，0 个 `semantic_qualified=true`；后段 error counts 非单调，最佳 run 后下一次又显著回升。继续对同一 artifact 反复 resume 不能证明 fresh generalization，也不会自然收敛。

后续禁止：

- 复制 semantic45 再形成 semantic46、47…式长链；
- 未修 deterministic bug 就新增 prompt guidance；
- 在同一 revision 下不断 fresh 直到抽到成功样本；
- 因 `qualification_passed=false` 而跳过所有 coder-facing diagnostic。

### 6.4 当前最早 root causes

1. `pipeline.py` 在存在 `unresolved_partitions` 时跳过 deterministic normalization/dependency completion，放大后续 dependency diagnostics。
2. coder repair 只识别 `file:line:column: error`，无法可靠路由 linker 的 multiple-definition/undefined-reference diagnostics，导致 3 轮预算实际只运行 1 轮。
3. 剩余真实 planning 缺口集中在 callback ABI/provider/user-data 与 compile-relevant runtime call flow。
4. Stage 9 whole-response JSON failure与 repair usage/fatal classification 需要确定性修正；不应继续扩大 prompt。

### 6.5 当前 worktree 保护

当前 checkpoint 为 `HEAD 3ccc99f`，已有以下未提交修改：

```text
M agent/planning/PLANNING_RQ1_SEMANTIC_UTILITY_GOAL.md
M agent/planning/facts.py
M agent/planning/planner.py
M agent/planning/prompts.py
M agent/planning/tests/test_pipeline.py
```

除本任务书重构外，其余修改属于 fresh3 后的 fact-slice/prompt 去重与 JSON repair usage 记账，尚未经过新 fresh 验证。后续不得丢弃、覆盖或重复实现；Step 7 必须先审计并复用其中仍然必要的最小改动。

## Step 7：修复 deterministic completion、repair routing 与失败记账

**状态：IN_PROGRESS**

### 目标

让 unresolved semantic state 仍经过所有安全、唯一可推导的 deterministic completion，再 materialize 完整 candidate specs；同时先修复已知 linker diagnostic routing，使本 Step fresh 之后的 bounded-repair 反馈可信。

### 任务

1. 在 `unresolved_partitions` 非空时仍执行：
   - `normalize_plan_for_compiler()`；
   - `complete_deterministic_dependencies()`；
   - deterministic change-log validation；
   - completion 后 implementability analysis。
2. unresolved 存在时不调用 whole-plan semantic patch；只执行无 LLM 的 deterministic completion，避免为候选诊断继续扩大上下文。
3. 原始 unresolved diagnostics 必须保留并与 residual diagnostics 去重合并；不得把 candidate 标为 qualified。
4. candidate specs 仍需 schema-valid、coder-loadable，且 materialize 已承诺 inventory。
5. 增加 integration regression：一个 non-fatal unresolved 不得阻断独立、可唯一推导的 file/module dependency materialization。
6. 修正 failed JSON repair 的 usage accounting 与分类：若已有可序列化 inventory，则记录 non-fatal unresolved、保留最后 state 并继续 candidate compilation；不得误报 deterministic invariant。
7. 使用 protocol-agnostic fixture 和 fresh02 的真实 linker stderr 复现：
   - `path.c:(.text+...)` multiple definition 与 first-defined-here；
   - undefined reference；
   - 无法唯一定位 source 的 link failure。
8. 只有 fixture 证明 coder contract bug 时，才最小修改现有 repair 流程：
   - target extraction 只选择 diagnostics 关联的 in-project `.c`；
   - diagnostic block/compaction 必须把相关 linker lines 交给 repair prompt；
   - 不 repair headers，不 repair all files；
   - 无法定位时返回明确 reason code；
   - 每轮 repair 后比较 clean compile/link roots；新增 duplicate/undefined 或降低 definition coverage 的 candidate 必须 reject/rollback。
9. 本 Step 禁止修改 planning prompt 或 coder prompt。

### 离线门禁

- fresh02/代表性 candidate 的 deterministic replay 不覆盖原 artifacts；
- 可唯一推导的 missing dependencies 被补齐；
- unresolved diagnostics 保留；
- schema、coder loader、required-field preservation 无 regression；
- synthetic 与 fresh02 linker fixtures 能选择正确 source，并把完整相关 linker block送入 repair；
- 可路由 linker error 不再错误返回 `no_repairable_sources`；恶化 candidate 会 rollback；
- planning、planning utility、coder 当前 full suites 及新增 regression 全部通过。

### 单次 fresh 完成门禁

完成修改后只需 1 次 fresh 同时满足：

```text
11/11 stages
specs materialized
schema validation passed
coder loader passed
nonfatal_no_specs_count = 0
required module/file/function inventory 非空
deterministically derivable dependency errors = 0
unresolved 与 residual diagnostics 均完整记录
coder generation/repair/compile outcome 已归档
planning total tokens <= 800000 且 token_accounting_complete=true
```

final compile 在本 Step 记录但不要求成功。

## Step 8：闭合 compile-relevant callback、ABI 与 call flow

**状态：TODO**

### 启动条件

Step 7 `DONE`，且其 fresh 已证明 dependency completion 不再因 unresolved 被跳过。

### 目标

只修真实会传播到 C declaration、callback invocation、symbol visibility、argument provider 或唯一 `main` 的 planning contracts，不扩展协议行为 prose。

### 任务

1. 使用 Step 7 fresh 的真实 residual，选择一个最早 authoritative root-cause family。
2. 确保 callback typedef、registration、provider、consumer、user-data、data buffer 和 length 的 ABI 一致。
3. 确保每个跨文件 direct call 的 declaration/header dependency 与 argument provider 可达。
4. 确保恰好一个 linkable `main`，并让 compile-relevant init/cleanup call chain 可物化。
5. 不把 callback、lifecycle 或 ownership relation 伪装为 direct call。
6. 优先修改 typed stage contract、validation 或 normalization。只有满足第 3 节全部准入条件时，才允许一次 deletion/replacement-based prompt patch。
7. 不增加新的 function/module inventory，除非 facts/rules 明确要求且现有 inventory无法表达必要 ABI。

### 单次 fresh 完成门禁

完成修改后只需 1 次 fresh 同时满足：

```text
Step 7 artifact gates 保持通过
本 Step 选定的 root-cause family 已消除，或由 compile evidence 证明不构成 blocker
其余 compile diagnostics 均映射到 planning-owned 或 coder-owned authoritative source
compile-relevant artifact/ABI/dependency metrics 已完整记录
initial compile 与 bounded-repair final compile 均已测量并归档
planning total tokens <= 800000 且 token_accounting_complete=true
```

initial compile 和 final compile 在本 Step 仍是反馈，不要求 final pass；不影响 compile 的 semantic diagnostics 不得阻止本 Step。若 final compile 已通过，按第 4.3 节的最终目标优先规则执行。

## Step 9：单次 fresh 打通 bounded-repair final compile

**状态：TODO**

### 启动条件

Step 8 `DONE`，且 fixed candidate 的 remaining compile diagnostics 已完成 planning-owned/coder-owned 归因。

### 目标

取得至少 1 个真实 fresh planning -> protocol specs -> coder bounded repair -> final clean compile success。

### 任务

1. 对最新 accepted Step 8 fresh specs 做只读复制和 fixed coder diagnostic，不覆盖原 artifact。
2. semantic45 只作为历史 regression fixture，不代替当前 revision 的 Step 8 specs。
3. 如果 fixed failure 属于 specs 缺失，只在最早 planning authoritative stage 做一次最终最小修复；不通过 coder 猜协议设计。
4. 如果 fixed failure 属于 Step 7 已覆盖的 generic repair defect，修复现有 regression，而不新增第二套 repair path。
5. 本 Step 禁止为 linker/repair routing修改 planning prompt。
6. 修改与 tests 通过后，使用本 Step 唯一 fresh 执行最多 3 轮 coder repair。

### 单次 fresh 完成门禁

```text
fresh/specs/schema/loader gates passed
coder generation completed
repair_rounds_used <= 3
repair 没有因可路由 linker diagnostics 提前停止
final clean compile passed
manual edit count = 0
planning total tokens <= 800000 且 token_accounting_complete=true
```

Step 9 第一次满足上述条件即为 `DONE`，不要求连续 fresh。

## Step 10：冻结 revision、最终稳定性与 pruning

**状态：TODO**

### 启动条件

Step 7–9 中至少 1 次 accepted 真实 fresh 已证明 bounded-repair final compile 可达，且所有 pre-final Step 均为 `DONE`；其中允许按第 4.3 节记录 goal-priority no-op `DONE`。

### Pre-freeze pruning

在冻结前完成所有实现修改：

1. 运行 planning、planning utility、coder relevant 全量 tests、`compileall` 和 `git diff --check`。
2. 检查 `git diff --stat` 与完整 `git diff`。
3. 删除本 Goal 新增的死代码、重复 validation、stale comments 和冗余 prompt prose。
4. 记录新增与删除内容；任何净增超过 50 行的实现 patch 必须解释为何无法用删除/替换完成。

Pre-freeze pruning 完成后，不得再修改 implementation、prompt、facts、schema、coder 或 measurement code。

### Revision freeze

execution source 与运行后会更新的 taskbook/ledger 分开 hash。冻结并记录：

```text
HEAD commit
execution-source tree hash（planning/compiler/coder/measurement；排除 taskbook、out 与 run ledger）
pre-run taskbook hash
facts raw-file SHA256 = 2842e52800f85da836eb9c4d2f0768757e376315da0a6b608d619088df5905be
facts canonical-JSON hash = 68a374601ad61a5ed4cbe6cb1ff3ff9a41d7f4b480cc9fc80d970570d1197c7e
specs schema/coder hash
planning/compiler/measurement source hashes
model + sampling config
compiler/toolchain versions
max_repair_rounds = 3
prompt-token baseline、frozen tokenizer 与 token ceiling
```

### 最终验收

在不修改代码、prompt、facts、schema、coder、budget 的条件下，运行 3 次独立 fresh。禁止 resume、replay、fixed-spec 替代或失败后无痕重跑。

最终成功条件：

```text
3/3 fresh specs materialized
3/3 schema validation passed
3/3 coder loader passed
3/3 required inventory 非空
3/3 coder generation completed
3/3 repair_rounds_used <= 3
3/3 final clean compile passed
3/3 manual edit count = 0
3/3 planning total tokens <= 800000 且 token_accounting_complete=true
```

Step 10 只执行这一组预声明的 3-run sequence。任一 run 失败仍完成并保存本 sequence 的其余预声明 runs，最终将 Goal 标记为 `DEFERRED`；不得自动修改、重新冻结或从 0/3 重启。只有用户明确修订任务书后才可开启新的 sequence。

### Evidence-only 交付

3-run 后只允许写 run artifacts、compact ledger 和最终报告；不得再 prune 或修改 execution source。记录 post-run taskbook/evidence hash、冻结的 execution-source hash和可复现命令。

## 附录 A：Compact run ledger

§6.1 的 Fresh 1–3 与 §6.2 的 semantic45/fresh02 fixed evidence 是本 ledger 不可删除的历史前缀。后续只维护以下字段，不恢复长篇逐 run 流水账：

| Step | Run | Revision | fresh/resume/replay | Fatal/reason | Specs/schema/loader | Qualified/union | Manifest root / actual root / stale | Initial compile | Repair rounds | Final compile | Tokens/complete | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| 7 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |
| 8 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |
| 9 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |
| 10.1 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |
| 10.2 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |
| 10.3 | 待运行 |  | true/false/false |  |  |  |  |  |  |  |  |  |

每个 run 至少保留：

```text
_planning/run_manifest.json
_planning/diagnostics.json
_planning/run_metrics.json
_planning/implementation_plan.json
fatal/non-fatal classification and reason
qualification/union diagnostics/nonfatal_no_specs_count
manifest specs_root + actual physical specs_root + stale flag
coder _agent_logs/run_manifest.json
pre-repair diagnostic snapshot
post-repair summary
initial/final compile stdout+stderr
source hashes
per-call/per-stage/total tokens + token_accounting_complete
```

## 附录 B：每步执行记录模板

```text
### Step N 执行记录

- 状态：
- Root-cause family：
- 最早 authoritative stage：
- 修改文件：
- 删除/替换内容：
- 新增内容：
- Targeted tests：
- Full tests：
- Prompt tokenizer tokens/characters before/after：
- Fresh run：
- Specs/schema/loader：
- Initial compile：
- Repair rounds/stop reason：
- Final clean compile：
- Definition/placeholder/near-empty：
- Per-call/per-stage/total tokens and accounting completeness：
- Artifact paths：
- 是否满足本 Step 门禁：
- 下一步：
```

## 附录 C：Goal 停止条件

### C.1 成功停止

只有以下条件全部满足才标记 `complete`：

```text
Step 0–10 全部 DONE
non-fatal specs materialization 无 regression
Step 7–9 至少一次真实 fresh bounded-repair final compile passed
Step 10 frozen sequence 3/3 passed
manual-edit gate passed
token ceiling passed
tests、diff、pruning 和 final hashes 完成
```

### C.2 允许 BLOCKED/DEFERRED

以下外部或 contract 级阻碍可标记 `BLOCKED` 并形成 blocker report：

- API credential 或 model service 持续不可用；
- 文件系统、compiler 或 toolchain 持续损坏；
- protocol facts 存在可证明的信息缺口或矛盾，无法在不发明事实的情况下表达必要 compile contract；
- specs schema 与冻结 coder contract 存在 protocol-agnostic 最小复现的逻辑冲突；
- 必要信息无法在冻结 token ceiling 内表达；
- 完成需要用户授权扩大 scope，例如非 generic coder redesign。

以下 acceptance 失败标记 `DEFERRED` 并保存完整证据，不伪装成外部 blocker：

- Step 7、8 或 9 的唯一 post-change fresh 未通过本 Step 门禁；此时标记 `DEFERRED`，不自动重试；
- Step 10 预声明 sequence 未达到 3/3；此时标记 `DEFERRED`，不自动开启新 sequence。

`BLOCKED` report 必须包含 3 次一致证据、最小复现、最早失败 stage、已尝试的最小修复、当前 tests/worktree 状态和需要用户决定的具体事项。单次 acceptance failure 使用 `DEFERRED`，不伪标为外部 blocker。

### C.3 不构成失败或阻塞

- initial compile 失败，但 bounded repair final compile 成功；
- `qualification_passed=false`，但 specs 完整且最终 compile 成功；
- diagnostics、definition coverage、placeholder 或 near-empty 指标不理想，但不影响 nonempty specs 和 final compile；
- 单次 fresh 失败不等于外部 blocker，但按本任务书触发 `DEFERRED`；
- fixed-spec 或 resume 失败；
- token 降低不明显，但未超过 ceiling。

这些结果不得驱动无界 prompt 扩充或无变化重跑。若已触发 `DEFERRED`，保存证据并等待用户决定是否修订任务书。
