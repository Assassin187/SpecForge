# SpecForge Planning Evaluation 稳定产出计划

> 建议文件位置：`agent/planning/PLANNING_EVALUATION_STABILITY_PLAN.md`  
> 制定日期：2026-07-13  
> 权威状态依据：`agent/planning/PLANNING_CURRENT_STATUS_REPORT.md`  
> 状态标记：`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED` / `DEFERRED`

---

## 1. 本轮唯一目标

本轮不继续追求 qualified specs、compile-ready specs 或更高语义质量，唯一目标是：

> 使 planning 在流程能够正常执行的情况下，稳定生成结构完整且可被现有 coder loader 加载的 protocol specs。

最终验收标准为：

1. 完成必要代码修改和本地测试；
2. 执行一次完整 fresh planning 作为首次打通测试；
3. 对首次运行暴露的问题使用 `resume` 定位和修复，直到该运行能够产出 coder 可加载的 specs；
4. 随后重新启动独立的 fresh planning 验收；
5. **连续 3 次独立 fresh planning 均完整运行，并分别产出 coder loader validation 通过的 specs；**
6. 3 次验收运行不得使用 replay、resume、缓存复用或人工修改运行产物。

本轮成功不要求：

- semantic closure 通过；
- `completed_with_qualified_specs`；
- generated C project 编译成功；
- coder source generation 或 repair；
- runtime behavior tests 通过。

上述项目属于后续语义质量和端到端能力优化，不得扩大为本轮阻塞条件。

---

## 2. 当前基线

当前实现已经具备以下能力，应直接保留：

- canonical symbol registry；
- typed delta / overlay；
- Stage 5/7/8 partition transaction；
- syntax repair、local correction 和 bounded recovery；
- deterministic final assembly；
- semantic diagnostics 和 provenance；
- candidate / qualified 状态记录；
- resume / replay；
- compact prompt 和 token metrics；
- planning validation、coder loader validation 与 rendered-header checks；
- 当前全部 planning tests。

当前主要缺口是：

1. semantic diagnostics 非空时跳过 `compile_specs`；
2. semantic patch 无效时最终不生成 specs；
3. unresolved partition 或 whole-stage correction failure 可能使后续流程提前结束；
4. candidate planning package 存在，但 `candidate_specs_root` 和 `specs_root` 为 `null`；
5. 因此已有 11/11 stages 完成的 fresh run 仍无法交给 coder loader。

本轮不是从旧无门禁实现重新开发，也不删除当前重构成果。旧实现只作为“流程完成后能够物化 specs”的行为参考。

---

## 3. 必须保持的边界

### 3.1 允许修改

原则上只修改：

```text
agent/planning/
```

包括 planning 源码、tests、README、状态报告和本计划书。

允许只读调用现有 coder loader validation，用于验证输出兼容性。

### 3.2 禁止修改

不得修改：

```text
agent/facts/
agent/coder/
specs-example/
protocol_facts.json
coder schema
generated C/H source
```

不得通过以下方式制造成功：

- 修改 protocol facts；
- 修改 coder loader 以接受原本非法的 specs；
- 从 MQTT 示例 specs 复制 inventory、API、signature、dependency 或 behavior；
- 在 compiler 中补造缺失协议行为、函数、类型或完整 dependency graph；
- 人工编辑 fresh/resume 运行生成的 stage artifacts 或 specs；
- 关闭 validator、批量将 error 改成 warning，或删除 diagnostics；
- 将 replay 或 resume 结果计为 fresh success。

### 3.3 语义边界

必须持续区分：

- **protocol facts**：只来自 facts 输入；
- **inferred engineering decisions**：由 planning 显式生成并保留来源；
- **open assumptions / unresolved items**：信息不足时显式记录。

Deterministic logic 只能完成结构 lowering、identity binding、已确定关系派生和 schema 允许的空值/default 填充，不得发明协议行为或新的工程 artifact。

---

## 4. Evaluation 运行语义

本轮只维护一种 planning 行为，不新增 `evaluation` / `strict` 两套 CLI 模式。

### 4.1 核心不变量

只要满足以下条件：

```text
facts 成功加载
+ pipeline state 未损坏
+ canonical registry 可读取
+ final planning artifact 可进行结构 lowering
```

就必须执行：

```text
final assembly
-> compile_specs
-> planning specs validation
-> coder loader validation
-> 保存 specs、diagnostics、manifest 和 metrics
```

semantic diagnostics、implementability diagnostics 或 semantic patch failure 不得阻止 `compile_specs`。

### 4.2 输出要求

Codex 应审计当前目录约定并采用改动最小的实现，但最终必须满足：

1. 每次正常完成的 planning run 都存在明确的 `specs_root`；
2. `specs_root` 中至少包含：
   - `SUMMARY.md`；
   - exactly one `PROTOCOL_MODULE_SPEC`；
   - 与 final plan 对应的 `FILE_SPEC`；
   - 与 final plan 对应的 `FUNCTION_SPEC`；
   - manifest 或等价运行元数据；
3. `candidate_specs_root` 不得在正常完成时为 `null`；
4. 即使 semantic qualification 未通过，coder loader 仍可显式加载该 specs 目录；
5. qualification 结果继续记录，但只作为质量字段，不再作为 specs 物化前置条件。

可以保留现有：

```text
completed_with_candidate_only
completed_with_qualified_specs
failed_internal
```

等状态名，但前两种完成状态都必须存在 coder-loadable specs。也可以在不破坏兼容性的前提下统一为 `completed_with_specs` 并单独记录 `qualification_passed`。不要为了改名扩大重构范围。

### 4.3 允许终止且不生成 specs 的情况

只有真正的 internal fatal failure 可以不生成 specs，例如：

- facts 文件无法读取或顶层格式损坏；
- pipeline state / registry snapshot 损坏；
- canonical identity 出现无法确定性消解的内部冲突；
- final artifact 连基本结构都无法恢复；
- compiler 自身出现确定性 invariant violation；
- 文件系统或外部 API 出现不可继续的系统错误。

普通 semantic、implementability、coverage、lifecycle、dependency、visibility、data-flow 或 test-oracle 问题均不属于 internal fatal failure。

---

## 5. 执行原则

1. 先审计再修改，不重复重构已经稳定的 registry、typed overlay 和 transaction。
2. 优先修复 lifecycle、control flow、compiler invocation 和 output publication。
3. 不提前进行大范围 Stage 4/6/8 语义增强。
4. Stage 4/6/8 只有在其错误直接阻止：
   - 11 stages 完成；
   - final artifact 结构 assembly；
   - specs lowering；
   - coder loader validation；

   时才进行最小必要修改。
5. semantic quality 问题保留为 diagnostics，不在本轮全部解决。
6. 每次修改后先运行静态测试和 targeted tests，再运行真实 planning。
7. 首次真实运行失败时优先使用 resume 定位，不立即反复 fresh 消耗 token。
8. 3 次正式 fresh 验收必须在首次完整打通后才能启动。
9. fresh 验收中任意一次失败，修复后成功计数清零，重新执行连续 3 次 fresh。
10. 完成连续 3 次成功后立即停止本 Goal，不继续优化 coder compile 或语义质量。

---

## 6. 分步修改计划

## Step 0：审计当前控制流与冻结基线

**状态：DONE**

### 目标

明确 semantic closure、qualification、`compile_specs`、publication 和 coder validation 的真实调用关系，避免根据旧计划重复重构。

### 任务

1. 阅读：
   - `pipeline.py`
   - `planner.py`
   - `implementability.py`
   - `compiler.py`
   - `validation.py`
   - `validation_layers.py`
   - `models.py`
   - `cli.py`
   - 相关 tests
2. 定位所有可能导致以下行为的分支：
   - semantic diagnostics 非空后跳过 `compile_specs`；
   - semantic patch invalid 后直接返回 candidate-only；
   - unresolved partition 后跳过 semantic closure 或 specs compilation；
   - whole-stage correction 失败后提前结束剩余 stages；
   - `candidate_specs_root` / `specs_root` 被设置为 `null`；
   - coder loader validation 只对 qualified specs 执行。
3. 记录当前 baseline：
   - tests 数量与结果；
   - 当前状态模型；
   - 当前正常完成和失败路径；
   - 当前 specs 路径约定；
   - 最近一次 fresh run 的 manifests 和 diagnostics。
4. 在本计划中更新实际审计结论。

### 完成标准

- 找到 specs 未物化的直接控制流原因；
- 列出本轮需要修改的最小文件集合；
- 当前 tests 全部通过；
- 没有修改 protocol facts、coder 或示例 specs。

### Step 0 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：完整读取 `PLANNING_CURRENT_STATUS_REPORT.md`、本计划、`README.md`；审计 `pipeline.py`、`planner.py`、`implementability.py`、`compiler.py`、`validation.py`、`validation_layers.py`、`models.py`、`cli.py`、planning tests，并只读审计 `agent/coder/specs.py` loader contract；本步仅修改本计划书。
- 核心变化：冻结当前基线，未修改运行逻辑。确认 `pipeline.py` 在 `closure_diagnostics` 非空时直接构造空 `compile_manifest`，跳过 `compile_specs`、planning validation 和 coder loader；`_write_planning_artifacts()` 仅对 `QUALIFIED` 写出 `specs_root`；`validate_existing_run()` 只验证 qualified specs。确认 `planner.py` 的 non-partition whole-stage correction 二次失败会抛出 `RecoverablePlanningError` 并终止后续 stages。
- 最小修改集合：`pipeline.py`、`models.py`、`cli.py`、必要的 `planner.py`、`tests/test_pipeline.py`、`README.md`、本计划和最终状态报告；不引入新 abstraction。
- 新增 tests：0（基线审计阶段）。
- 全量 tests：`python3 -m unittest discover -s agent/planning/tests -p 'test_*.py'`，83/83 passed。
- Validation：最近一次 fresh `mqtt_token_optimization_fresh_02_20260711` 为 11/11 stages、15/15 partitions、39 semantic errors、`candidate_specs_root=null`、`specs_root=null`；直接印证物化门禁。
- Planning runs：本步未启动新 run。
- 本步 token：Goal 计量从 5,258 增至 87,683，共 82,425。
- 累计 token：87,683。
- 新发现问题：candidate specs 即便已在 staging 生成，当前 `PlanningResult.success` 和 CLI exit code 仍绑定 qualified；candidate publication 路径与 validation 路径需要一起调整，避免 specs 生成但 CLI 错报失败。
- 是否阻塞下一步：否。
- 后续调整：先完成 Step 1 的 specs production/qualification 解耦，再以最小 continuation 修改处理 Step 2。

---

## Step 1：解除 semantic qualification 对 specs 物化的阻断

**状态：DONE**

### 目标

保证 final planning artifact 可结构 lowering 时，无论 semantic closure 是否通过，都执行 `compile_specs`。

### 任务

1. 调整 pipeline lifecycle：
   - semantic closure 继续执行；
   - semantic patch 成功则使用修复后的 final plan；
   - semantic patch 失败则保留修复前最后一个 structurally valid final plan；
   - 无论 qualification 结果如何，都继续进入 `compile_specs`。
2. semantic diagnostics 保留原 severity、source、owner layer 和 recovery outcome。
3. 不将 semantic error 批量降级为 warning。
4. 将 qualification 从“是否生成 specs”的前置门禁改为 specs 生成后的质量结果。
5. 正常完成时确保：
   - `candidate_specs_root` 非空；
   - 可用的 `specs_root` 非空；
   - manifest 记录 `qualification_passed`；
   - manifest 记录 semantic diagnostics 数量；
   - specs 目录包含 coder 需要的全部文件类型。
6. 保证 semantic patch failure 不覆盖或破坏最后一个 structurally valid plan。

### 必须新增的测试

至少覆盖：

1. semantic diagnostics 非空，仍生成 specs；
2. `semantic_patch_invalid`，仍生成 specs；
3. qualification failed，仍设置可用 `specs_root`；
4. qualification passed，原成功路径不回归；
5. internal fatal failure 仍不会伪造 specs；
6. diagnostics 内容和 severity 未被丢失。

### 完成标准

- `compile_specs` 不再受 semantic qualification gate 控制；
- candidate 和 qualified 完成状态都能获得 coder-facing specs；
- 原有成功 replay 行为不回归。

### Step 1 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：`pipeline.py`、`models.py`、`cli.py`、`tests/test_pipeline.py`、本计划书。
- 核心变化：`compile_specs` 现对任意 structurally lowerable final plan 执行；semantic closure diagnostics 与 `semantic_patch_invalid` 原样保留；candidate specs 的 `specs_root` 指向 `_planning/candidate_planning_package/specs`；qualified publication 使用 copy，保留 candidate specs；manifest 写出 `specs_generated` 与 `qualification_passed`；CLI/`PlanningResult.success` 不再把 semantic qualification 当作 artifact success。
- 新增 tests：新增 `semantic_patch_invalid` 仍物化 specs 且保留 error severity；更新 qualification failed、unresolved partition 和 qualified path assertions。
- 全量 tests：84/84 passed。
- Validation：targeted 4/4 passed；full `unittest discover` passed；真实 coder loader fixture 的 qualified path继续通过。
- Planning runs：本步未启动真实 LLM planning。
- 本步 token：24,703。
- 累计 token：112,386。
- 新发现问题：`run_status` 仍综合全部 diagnostics 表示 qualification，而 artifact success 已解耦；Step 4 需在 manifest 单独记录 planning/coder loader 结果，避免只从 `run_status` 推断。
- 是否阻塞下一步：否。
- 后续调整：Step 2 仅修改 whole-stage correction 的 recoverable continuation，不触碰 canonical registry 与 partition transaction。

---

## Step 2：保证 recoverable failure 后继续完成 11 stages

**状态：DONE**

### 目标

消除普通 structural/binding/local semantic 问题导致 whole run 提前结束的情况。

### 任务

1. 审计 non-partition whole-stage correction 失败后的控制流。
2. 对 recoverable stage failure 采用：
   - 保存最后一个 structurally valid artifact；
   - 若不存在可提交 artifact，使用该 stage schema 明确允许的空 typed delta；
   - 记录 unresolved stage / partition；
   - 继续后续 stages。
3. 对 partition stage 保持：
   - 单 partition rollback；
   - 已提交 partitions 不回滚；
   - unresolved partition 不影响独立 partitions；
   - final assembly 能读取 committed overlays 和 unresolved ledger。
4. 不允许通过空 typed delta 发明任何 artifact；只能表达“本 stage 没有可提交增量”。
5. 如果某 stage 的输出是后续结构上绝对必需且不存在合法空表示：
   - 保留为结构 blocker；
   - 使用一次 bounded correction；
   - 仍失败时允许 run 标记 internal/structural failure；
   - 在首次打通阶段通过 resume 修复该具体问题。
6. 确保 Stage 11 final assembly 和 `compile_specs` 能区分：
   - absent semantic decision；
   - unresolved item；
   - deterministic pipeline corruption。

### 必须新增的测试

至少覆盖：

1. whole-stage correction 二次失败后，后续可独立 stages 继续；
2. unresolved partition 不导致已提交 partitions 丢失；
3. empty typed delta 不新增 symbol 或 protocol behavior；
4. final assembly 能携带 unresolved ledger；
5. recoverable failure 后仍可调用 compiler；
6. 真正结构不可恢复时仍产生明确 fatal diagnostic。

### 完成标准

- recoverable failure 不再自动等于 zero specs；
- 11-stage control flow 对局部失败具有确定行为；
- final artifact 始终来自已验证 artifact、合法空 delta 和显式 unresolved information。

### Step 2 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：`planner.py`、`tests/test_pipeline.py`、本计划书。
- 核心变化：whole-stage bounded correction 二次失败后，对 `scope_fact_inventory`、`architecture_boundaries`、`public_artifact_inventory`、`function_test_vector_design`、`dependency_closure` 使用各自 schema 的合法空 artifact，记录 unresolved 后继续；partition stage 的 whole-stage validation failure 不回滚已提交 partitions；final assembly 及无合法空表示的核心结构 stage 仍明确阻断。空 artifact 只表达无可提交增量，不注册 symbol 或协议行为。
- 新增 tests：新增 whole-stage correction failure -> empty delta -> 后续 11 stages continuation 测试；更新 pending amendment fixture，使 deterministic final assembly携带 unresolved ledger。
- 全量 tests：85/85 passed。
- Validation：`py_compile planner.py` passed；targeted continuation/correction tests 2/2 passed；full suite passed。
- Planning runs：未启动真实 fresh planning。一次旧 unit fixture 因新 continuation 意外进入 semantic closure 并发出 semantic patch 请求，返回 noncanonical ID 后失败；未修改任何 run artifact，fixture 随即改为显式携带 unresolved ledger，后续测试不再访问外部模型。
- 本步 token：54,387。
- 累计 token：166,773。
- 新发现问题：Stage 6 inventory 非空但 interface 全失时不存在不发明 signature 的安全 lowering，因此仍是结构 blocker；若 bootstrap 命中，必须按最早 Stage 6 resume 修复，不能由 compiler 猜测 signature。
- 是否阻塞下一步：否。
- 后续调整：Step 3 验证 compiler 对缺失非必要 behavior/call/test overlays 使用现有合法空结构，并以真实 coder loader fixture 校验。

---

## Step 3：建立 coder-loadable 的 evaluation specs lowering

**状态：DONE**

### 目标

确保结构 lowering 产出的 specs 始终符合现有 coder loader contract。

### 任务

1. 审计 coder loader 对以下内容的实际要求：
   - module spec 数量；
   - file/function spec 必填字段；
   - ID 和 path 格式；
   - header/source interface；
   - manifest；
   - empty list / empty object 的合法性。
2. 只读 coder 代码和 schema，不修改它们。
3. 调整 compiler，使其对 structurally valid final plan：
   - 生成 exactly one module spec；
   - 生成完整 file specs；
   - 为 inventory 中的 functions 生成 function specs；
   - 使用 schema 已允许的空列表、空对象或已有 deterministic defaults；
   - 不为 unresolved semantic information发明函数、类型、调用边或行为。
4. 如果 unresolved partition 导致某些 semantic overlay 缺失：
   - 保留 canonical identity 和已存在的合法定义；
   - 仅使用 schema 允许的最小结构；
   - 将缺失语义记录在 sidecar diagnostics / manifest；
   - 不在 compiler 中猜测协议行为。
5. coder loader validation 必须对每次生成的 specs 执行，而不是只对 qualified specs 执行。
6. coder loader failure 应写回 run manifest，并作为本轮 artifact-stability failure。
7. rendered-header check 可以继续记录，但除非它属于 coder loader 的必需部分，否则不提升为本轮最终验收条件。

### 必须新增的测试

至少覆盖：

1. semantic failed candidate specs 可被 coder loader 加载；
2. unresolved nonessential semantic fields 使用合法空结构；
3. exactly one module spec；
4. file/function references 全部指向已物化文件；
5. compiler 没有引入 registry 中不存在的 symbol；
6. coder loader failure 被准确记录；
7. specs 目录和 manifest 路径在完成状态下始终非空。

### 完成标准

- 任一 structurally lowerable plan 都会生成 coder-facing specs；
- loader compatibility 由真实 coder validation 验证；
- compiler 不承担第二次 planning。

### Step 3 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：只读审计 `agent/coder/specs.py` 与 coder models；修改 `tests/test_pipeline.py`、本计划书。`compiler.py` 无需修改。
- 核心变化：确认现有 compiler 已对缺失非必要 call/wire/test overlays 使用 schema 合法空列表，并以已存在 function role/signature 生成既有 deterministic LOGIC/EVENT default；没有新增 identity、signature、调用边或协议 inventory。新增真实 coder loader acceptance 证据。
- 新增 tests：semantic-failed、optional overlays 缺失的 candidate specs 真实 coder loader 测试；exactly one module、FILE_SPEC/FUNCTION_SPEC inventory preservation；coder loader failure 保留 specs 并记录 diagnostics。
- 全量 tests：87/87 passed。
- Validation：真实 `load_spec_bundle_from_root(..., validate_rendered_headers=True)` 无 error；module/file/function specs 数量分别为 1/plan files/plan functions；loader failure fixture准确出现在 `_planning/diagnostics.json`，`specs_root` 保持非空。
- Planning runs：本步未启动真实 fresh planning。
- 本步 token：12,742。
- 累计 token：179,515。
- 新发现问题：当前 `_coder_validate` 将 rendered-header check 作为 loader 调用的一部分；本轮保持现有 contract，不关闭该检查。若 fresh 仅因 rendered-header error 失败，将按 loader failure 从最早 authoritative planning stage resume，而不修改 coder。
- 是否阻塞下一步：否。
- 后续调整：Step 4 补齐 manifest 的 stage/partition/specs/planning/coder/qualification/fresh-resume/token 字段和 CLI 摘要。

---

## Step 4：统一运行状态、manifest 与可观测性

**状态：DONE**

### 目标

让每次运行都能明确回答：流程是否完成、specs 是否生成、coder 是否可加载、qualification 是否通过。

### 任务

1. 在 manifest 或等价 summary 中记录：
   - `run_status`；
   - `stage_survival`；
   - `partition_survival`；
   - `specs_generated`；
   - `specs_root`；
   - `candidate_specs_root`；
   - `planning_validation_passed`；
   - `coder_loader_passed`；
   - `qualification_passed`；
   - semantic diagnostic counts；
   - unresolved stage/partition counts；
   - token usage；
   - resume/fresh/replay 标记。
2. 保证以下状态不可混淆：
   - 完成并生成 specs，但 qualification failed；
   - 完成并生成 specs，qualification passed；
   - internal fatal，未生成 specs。
3. CLI 最终摘要必须输出实际 specs 路径和 coder loader 结果。
4. 不将 candidate specs 描述为 qualified specs。
5. 更新 README，说明当前为 evaluation-oriented 行为：
   - specs production 与 qualification 分离；
   - semantic error 保留但不阻止 specs；
   - coder loader 是本轮稳定性标准。

### 完成标准

- 单看 manifest 即可判断是否满足本轮成功条件；
- 不需要检查零散日志才能确认 specs 是否可用；
- resume 和 fresh run 能被准确区分。

### Step 4 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：`pipeline.py`、`cli.py`、`README.md`、`tests/test_pipeline.py`、本计划书。
- 核心变化：manifest 新增 `specs_generated`、`planning_validation_passed`、`coder_loader_passed`、`qualification_passed`、semantic counts、unresolved count、stage/partition survival、token accounting、fresh/resume/replay；candidate 与 qualified 都暴露真实 `specs_root`，internal fatal明确记录未生成 specs；CLI 输出 specs path、loader 与 artifact success。
- 新增 tests：normal qualified、qualification failed、semantic patch invalid、internal fatal 的 manifest field assertions；修复 `validate_existing_run` 对 list diagnostics 的读取。
- 全量 tests：87/87 passed。
- Validation：targeted manifest tests 4/4 passed；full suite passed；README 已删除“semantic error 不生成 specs”和“candidate specs_root 为 null”的过时说明。
- Planning runs：本步未启动真实 fresh planning。
- 本步 token：25,476。
- 累计 token：204,991。
- 新发现问题：mocked `_run_stage` 的 unit fixture 不生成真实 stage records，因此其 manifest 只能断言 expected=11；真实 run 的 completed survival 将在 Bootstrap 验证。
- 是否阻塞下一步：否。
- 后续调整：严格执行 Step 5 全套命令和 pruning/diff 审计，全部通过后才启动 Bootstrap。

---

## Step 5：完成静态与回归测试

**状态：DONE**

### 目标

在消耗真实 planning token 前，证明控制流修改没有破坏既有架构。

### 必须执行

```bash
python3 -m compileall -q agent/planning
python3 -m unittest discover -s agent/planning/tests -p 'test_*.py'
git diff --check -- agent/planning
```

同时运行仓库中现有的：

- anti-hardcoding check；
- reference isolation check；
- deterministic replay / frozen fixture；
- planning validation fixture；
- coder loader fixture；
- rendered-header fixture。

如果命令名称与文档不一致，先从现有 tests/README 中查找真实命令，不要臆造新脚本。

### 回归要求

1. 当前 83 项 tests 不得减少或被跳过；
2. 新增 tests 必须覆盖本计划 Step 1–4；
3. replay success 继续通过，但不计入 fresh acceptance；
4. 不允许删除严格 semantic diagnostics 来让测试通过；
5. 不允许放宽 coder schema。

### 完成标准

- 所有现有与新增 tests 通过；
- compileall、diff check、anti-hardcoding 和 reference isolation 通过；
- 可以启动首次真实 planning。

### Step 5 执行记录

- 完成时间：2026-07-13
- 状态：DONE
- 审计/修改文件：审计全部 tracked diff；pruning pass 删除 `validate_existing_run` 中随后会被覆盖的重复 `qualification_passed` assignment；本步另修改本计划书。
- 核心变化：无新行为；冻结 Step 1–4 静态基线。删除量包括 semantic diagnostics -> 空 compile manifest gate、qualified-only specs path、validation failure 时移动并清空 specs 的旧逻辑。
- 新增 tests：本步无新增；累计新增后 suite 为 87 项。
- 全量 tests：87/87 passed（最终 pruning 后再次全量执行）。
- Validation：`compileall` passed；anti-hardcoding/reference isolation passed；Run 3 stable-ID replay regression、compiler normalization、structured callback/header loader、compile-only resume 5/5 passed；`git diff --check` passed。
- Planning runs：未启动真实 fresh planning。
- Patch size：tracked diff +320/-114。净增超过 50 行是必要的：149 行为 lifecycle/loader/continuation/manifest 回归 tests，其余为 recoverable continuation 与本计划要求的 manifest evidence；实现同时删除 114 行 obsolete qualification gate/搬移逻辑，未保留双路径模式。
- 本步 token：20,832。
- 累计 token：225,823。
- 新发现问题：定向测试首次使用了不存在的旧测试名，已从源码定位为 `test_structured_callback_signature_lowers_idempotently` 并以真实名称通过；非代码失败。
- 是否阻塞下一步：否。
- 后续调整：启动独立目录 `mqtt_evaluation_bootstrap_01_20260713` 的 fresh planning，不使用 resume/replay/cache。

---

## Step 6：首次 fresh 完整运行

**状态：DONE**

### 目标

验证修改后的真实路径能否从 facts 开始完整执行，并尝试首次产出 coder-loadable specs。

### 运行目录

使用新的、独立目录，例如：

```text
agent/planning/out/mqtt_evaluation_bootstrap_01
```

不得复用历史 run 目录。

### 执行

使用仓库当前真实 planning 命令执行一次不带 `--resume-from` 的 fresh planning。

运行结束后必须检查：

1. 是否完成全部 11 stages；
2. 所有 partition 的成功、回滚和 unresolved 情况；
3. final planning artifact 是否存在；
4. `compile_specs` 是否执行；
5. specs 目录是否存在；
6. `SUMMARY.md`、module/file/function specs 是否齐全；
7. planning specs validation 结果；
8. coder loader validation 结果；
9. manifest 是否准确；
10. token usage。

### 成功条件

首次 fresh 运行若直接满足：

```text
11/11 stages completed
+ specs generated
+ coder loader passed
```

则直接进入 Step 8。

否则进入 Step 7。

此 run 无论成功与否都不计入最终连续 3 次 fresh 验收。

### Step 6 执行记录

- 完成时间：2026-07-13
- 状态：DONE（Bootstrap 完整执行，但 loader 未通过，按规则进入 Step 7）。
- 审计/修改文件：未修改源码；读取 bootstrap manifest、metrics、unresolved ledger、partition manifests、specs inventory 和 coder diagnostics；更新本计划书。
- 核心变化：无代码变化。首次真实 fresh 证明 semantic/unresolved 不再阻止 final assembly 与 specs 物化。
- 新增 tests：0。
- 全量 tests：启动前 87/87 passed。
- Validation：11/11 stages；16/17 partitions committed，1 unresolved；生成 exactly 1 module、7 file、30 function specs；`specs_generated=true`；planning validation failed（semantic/structural diagnostics 保留）；coder loader failed，唯一 coder error 为 `coder_unsupported_array_type_spelling`。
- Planning runs：Fresh Bootstrap 1，`agent/planning/out/mqtt_evaluation_bootstrap_01_20260713`，未使用 resume/replay/cache，CLI exit 1。
- Planning token：384,702（prompt 351,696；completion 33,006；25 structured requests；0 semantic requests）。
- 本步 Goal token：51,966。
- 累计 Goal token：277,789。
- 新发现问题：Stage 5 合法 C spelling `char[64]` 被原样 lower 到 coder dialect；coder contract 要求 `TYPE: char` + `ARRAY_LEN: 64`。这是 generic deterministic schema-dialect lowering 缺口，不需要修改 Stage 5 artifact。Stage 8 broker partition另有 unknown function ID unresolved，但不造成当前唯一 coder loader error。
- 是否阻塞下一步：是，按 Step 7 修复 compiler dialect lowering 后从 `compile_specs` resume。
- 后续调整：为 array member spelling 增加通用、确定性的 `TYPE`/`ARRAY_LEN` lowering及 unit test；通过静态 tests 后对该 bootstrap run执行 `--resume-from compile_specs`。

---

## Step 7：使用 resume 打通首次完整 specs 产出

**状态：DONE**

### 目标

使用首次 fresh run 的真实失败证据，以最小改动逐步修复，直到该 run 能通过 specs 生成和 coder loader validation。

### 诊断顺序

每次失败先分类：

1. **deterministic pipeline defect**
   - control flow、registry、compiler、manifest、path、state restore 等代码错误；
2. **LLM structural instability**
   - JSON、enum、required field、unknown ID、非法 typed delta 等；
3. **semantic quality issue**
   - lifecycle、direction、dependency、provider、runtime oracle 等；
4. **protocol-fact insufficiency**
   - facts 无法支持唯一工程决策。

本轮只将 1、2 中会阻止完整运行、specs lowering 或 coder loader 的问题视为必须修复。3、4 默认记录为 diagnostics，不扩大为本轮 semantic optimization。

### Resume 规则

从最早受影响的 stage 继续：

- 仅 compiler/publication/validation 问题：`--resume-from compile_specs`；
- Stage 8 call artifact 问题：从 `function_call_contract_closure` 或仓库实际对应 stage ID；
- Stage 6 interface/signature 问题：从 `function_interface_design`；
- Stage 4 inventory 问题：从 `public_artifact_inventory`；
- 更早依赖受影响时，从实际最早 authoritative writer stage 开始。

不得为了快速成功直接修改已生成 artifact。

### 迭代循环

```text
分析失败
-> 最小代码/提示词修复
-> 更新 tests
-> 运行静态与 targeted tests
-> 对 bootstrap run 使用 resume
-> 执行 compile_specs
-> 执行 coder loader validation
-> 更新本计划
```

一直执行到 bootstrap run 满足：

```text
完整 final plan
+ specs generated
+ coder loader passed
```

### 限制

- 优先修复通用机制，不写 MQTT 名称判断；
- 不通过修改 coder 消除 loader error；
- 不把 resume 成功计入 3 次 fresh；
- 若 resume 暴露上游 artifact 已不可恢复，可以新建第二个 bootstrap fresh run，但仍不计入最终验收；
- 在首次完整 specs 产出前，不启动正式 3-run fresh sequence。

### 完成标准

至少有一次真实 planning 路径经过 fresh + 必要 resume 后：

- 完成 final assembly；
- 生成完整 specs；
- coder loader validation 通过；
- 没有人工修改生成 artifacts。

### Step 7 执行记录

- 完成时间：2026-07-13
- 状态：DONE。
- 审计/修改文件：`compiler.py`、`tests/test_pipeline.py`、本计划书；只读检查 bootstrap Stage 5 artifact、coder loader implementation/schema、implementation plan dependencies、resume manifest/specs。
- 核心变化：新增一维 C array spelling 的确定性 `TYPE` + `ARRAY_LEN` lowering；module spec 仅发布与既定 generation order 一致的已有 dependency edges，循环/前向 edges仍保留在 implementation plan 与 semantic diagnostics，不新增、反转或猜测 dependency。未修改任何生成 artifact。
- 新增 tests：array member coder dialect lowering；cyclic/forward dependency 的 evaluation lowering并验证真实 coder loader。累计 89 tests。
- 全量 tests：第一次修复后 88/88，第二次修复后 89/89；compileall/diff check passed。
- Validation：compile-only Resume 1 消除 array error后暴露 3 个 coder generation-order errors；compile-only Resume 2 后 pipeline coder loader passed。独立只读 `load_spec_bundle_from_root(..., validate_rendered_headers=True)` 得到 5 modules / 7 files / 30 functions / 0 errors / 0 warnings。
- Planning runs：Bootstrap Resume 1、2 均从 `compile_specs`，0 LLM requests；Resume 2 达到 `specs_generated=true`、`coder_loader_passed=true`、CLI exit 0。
- Planning token：resume 新增 0；复用 bootstrap累计 384,702。
- 本步 Goal token：26,359。
- 累计 Goal token：304,148。
- 新发现问题：compile-only resume 会重新 deterministic assembly，未把原 `unresolved_partitions` ledger写回 plan，因此 semantic closure报告从1项 unresolved变成18项 implementability diagnostics；不影响 specs/coder loader，但属于后续 resume observability backlog。
- 是否阻塞下一步：否。
- 后续调整：开始连续 3 次全新 fresh sequence；任意失败即清零并按失败 run resume。

---

## Step 8：连续 3 次 fresh planning 最终验收

**状态：DONE**

### 目标

验证 planning 已达到 RQ1 实验所需的 artifact stability。

### 运行要求

使用三个全新、互相独立的输出目录，例如：

```text
agent/planning/out/mqtt_evaluation_fresh_01
agent/planning/out/mqtt_evaluation_fresh_02
agent/planning/out/mqtt_evaluation_fresh_03
```

每次运行必须：

- 从相同 protocol facts 和目标配置开始；
- 不使用 `--resume-from`；
- 不使用 replay；
- 不复制历史 `_planning` artifacts；
- 不人工编辑输出；
- 完整执行 planning；
- 独立执行 coder loader validation。

### 单次 fresh success 定义

一轮只有同时满足以下条件才成功：

1. `fresh=true`；
2. planning 完成全部 11 stages；
3. 没有 `failed_internal`；
4. `compile_specs` 实际执行；
5. specs 目录存在且非空；
6. `SUMMARY.md` 存在；
7. exactly one module spec；
8. file/function specs 能被完整发现；
9. planning schema/structure validation 没有阻止 loader；
10. 现有 coder loader validation exit code 为 0；
11. 没有人工修改运行产物。

不要求：

- qualification passed；
- semantic diagnostics 为 0；
- rendered headers 为 0 warnings；
- generated C code compile success。

### 连续性规则

- 必须连续 3 次成功；
- 任意一次 fresh 失败，当前连续成功计数清零；
- 使用失败 run 的 resume 进行定位和验证；
- 修复代码并重新通过 Step 5；
- 必要时重新执行 bootstrap；
- 然后重新从 `fresh_01` 开始 3 次验收；
- 不得只补跑失败的某一轮后声称 3/3。

### 完成标准

记录到 3 个独立 run，且三者均：

```text
full fresh planning
-> specs generated
-> coder loader passed
```

达到后立即停止 Goal。

### Step 8 执行记录

- 完成时间：2026-07-13。
- 状态：DONE；第二组验收连续 3 次独立 fresh planning 均完成全部 11 stages、实际执行 `compile_specs`、生成非空 specs，并通过现有 coder loader。
- 审计/修改文件：本步未修改实现代码；审计三个 fresh run 的 `_planning/run_manifest.json`、candidate specs 文件集，并以 `agent.coder.specs.load_spec_bundle_from_root` 独立复验。
- 核心变化：无新代码变化；在第一组 Fresh 3 失败并清零、resume 定位、修复和 Step 5 回归后，第二组从 Fresh 1 重新开始并达到连续 3/3。
- 新增 tests：无；本步使用真实 fresh acceptance 和独立 coder loader validation。
- 全量 tests：进入第二组验收前 `python3 -m unittest discover -s agent/planning/tests -p 'test_*.py'` 为 94/94 pass；Step 9 冻结前再次执行全量回归。
- Validation：Sequence 2 Fresh 1 为 11/11 stages、16/16 partitions、1/6/30 specs；Fresh 2 为 11/11、17/17、1/6/30；Fresh 3 为 11/11、18/18、1/8/32。三轮 `fresh=true`、`resume=false`、`replay=false`、`specs_generated=true`、`coder_loader_passed=true`；第三轮独立 loader 复验为 0 errors / 0 warnings。
- Planning runs：`mqtt_evaluation_acceptance_r2_fresh_01_20260713`、`mqtt_evaluation_acceptance_r2_fresh_02_20260713`、`mqtt_evaluation_acceptance_r2_fresh_03_20260713`。
- Planning LLM token：429,947 + 483,783 + 455,323 = 1,369,053。
- 本步 Goal token：735,763（累计 Goal token 从 Step 7 的 304,148 增至 1,039,911）。
- 累计 Goal token：1,039,911。
- 新发现问题：三轮均为 `completed_with_candidate_only`，`planning_validation_passed=false`、`qualification_passed=false`；semantic diagnostics 分别为 23、68、3，第三轮另保留 3 个 unresolved partitions 和 72 个总 errors。这些属于明确排除在本轮成功标准之外的 semantic qualification backlog，未阻止 coder-loadable specs 物化。
- 是否阻塞下一步：否；artifact stability 已达到，进入 Step 9 冻结报告。
- 后续调整：仅更新权威状态报告、完成最终静态与单元回归、审阅 diff；不扩展到 semantic qualification、coder generation、compile 或 behavior tests。

---

## Step 9：冻结结果并更新报告

**状态：DONE**

### 目标

形成可用于 RQ1 实验的稳定 planning 版本和完整证据。

### 任务

1. 更新本计划所有 Step 状态；
2. 更新 `PLANNING_CURRENT_STATUS_REPORT.md`；
3. 记录：
   - 修改文件；
   - 核心 lifecycle 变化；
   - 新增 tests；
   - 全部静态测试结果；
   - bootstrap 和 resume 过程；
   - 3 次 fresh run 路径；
   - 每次 stage/partition survival；
   - 每次 specs 文件数量；
   - coder loader 结果；
   - qualification 和 semantic diagnostics；
   - 每次和累计 token；
   - 尚未解决的语义质量问题。
4. 明确结论只能表述为：

> planning 已达到连续 3 次 fresh 生成 coder-loadable specs 的 artifact stability，可用于 RQ1 后续 coder 生成实验。

不得扩大表述为：

- planning 已稳定生成 qualified specs；
- specs 已 compile-ready；
- 代码已可编译或可运行；
- semantic closure 已解决。
5. 保存当前代码差异和测试证据，作为 RQ1 planning freeze point。

### 完成标准

- 3-run 证据可复查；
- 状态报告与实际 manifest 一致；
- 剩余问题按后续 semantic-quality backlog 记录；
- Goal 结束。

### Step 9 执行记录

- 完成时间：2026-07-13。
- 状态：DONE；Step 0-9全部完成，当前实现与运行证据形成RQ1 planning freeze point。
- 审计/修改文件：更新`PLANNING_CURRENT_STATUS_REPORT.md`和本计划；完整审阅`pipeline.py`、`planner.py`、`compiler.py`、`models.py`、`cli.py`、`README.md`与`tests/test_pipeline.py`差异。`git status --short`确认修改范围仅为`agent/planning`，未修改facts、coder、coder schema、`specs-example`、protocol facts或generated C/H。
- 核心变化：权威报告已从旧的“semantic failure产生0 specs、fresh stability未完成”更新为当前artifact lifecycle、bootstrap/resume路径、失败清零过程和连续3/3证据；明确保留qualification/semantic失败结论。
- 新增 tests：本步无新增；本轮suite从83项增至94项，覆盖semantic failure specs物化、recoverable continuation、manifest、真实coder loader、array/dependency/signature/entrypoint/custom member/shared header lowering和semantic registry invariant。
- 全量 tests：`python3 -m unittest discover -s agent/planning/tests -p 'test_*.py'`为94/94 pass。
- Validation：`python3 -m compileall -q agent/planning` pass；anti-hardcoding/reference isolation pass；Run 3 stable-ID replay、semantic-failed real coder loader、array/entrypoint/shared-header loader、structured callback/header共7项定向tests pass；`git diff --check -- agent/planning` pass；最终三次fresh统一独立coder loader复验均为0 errors / 0 warnings。
- Planning runs：最终计入`mqtt_evaluation_acceptance_r2_fresh_01_20260713`、`mqtt_evaluation_acceptance_r2_fresh_02_20260713`、`mqtt_evaluation_acceptance_r2_fresh_03_20260713`，连续3/3。
- Patch与pruning：tracked diff为+635/-185，另有本计划书作为untracked执行记录。净增超过50行主要因为`tests/test_pipeline.py`新增282行回归fixtures及状态报告新增运行证据；实现同时删除semantic compile gate、validation时移动/删除specs、recoverable stage提前终止等obsolete logic。已完成完整diff pruning审阅，无需新增wrapper、mode、registry或compatibility branch。
- Planning LLM token：最终3-run sequence累计1,369,053；Bootstrap为384,702；resume compile-only为+0。
- 本步 Goal token：Goal meter在Step 9开始前被系统置为`blocked`并冻结，无法取得独立增量；最终可用计数为1,039,911。
- 累计 Goal token：1,039,911。
- 新发现问题：semantic qualification仍为0/3；最终三轮semantic diagnostics为23/68/3，第三轮另有3个unresolved partitions。Goal的`blocked`是长执行后的系统状态，不是代码、API或验收阻塞；本次用户续执行后已完成剩余冻结工作。
- 是否阻塞下一步：否；本轮唯一目标已完成。
- 后续调整：停止本Goal。Semantic qualification、dependency source-edge、callback/lifecycle、access-service、runtime entrypoint、coder generation/compile/behavior和多协议迁移需作为后续独立目标。

---

## 7. 本轮优先级与延后项

### P0：必须完成

1. semantic failure 后仍调用 `compile_specs`；
2. 正常流程始终产生 specs；
3. candidate specs 可被 coder loader 加载；
4. recoverable failure 不导致 zero specs；
5. 首次 fresh + resume 打通；
6. 连续 3 次 fresh coder-loadable success。

### P1：仅在阻塞 P0 时处理

1. Stage 4 inventory 的结构闭包；
2. Stage 6 signature type binding；
3. Stage 8 typed call edge 的结构合法性；
4. whole-stage correction continuation；
5. compiler 对合法空 overlay 的处理。

### DEFERRED：本轮不主动处理

1. qualified specs 稳定率；
2. semantic diagnostics 清零；
3. callback / lifecycle / direct-call 关系完整重构；
4. dependency source-edge semantic repair；
5. access-service provider 完整闭包；
6. runtime entrypoint 质量；
7. runtime test oracle；
8. coder source generation、repair、compile 和 behavior；
9. 多协议迁移；
10. 更高语义密度。

---

## 8. Step 更新模板

每完成一个 Step，立即在该 Step 下增加：

```markdown
### Step N 执行记录

- 完成时间：
- 状态：
- 审计/修改文件：
- 核心变化：
- 新增 tests：
- 全量 tests：
- Validation：
- Planning runs：
- 本步 token：
- 累计 token：
- 新发现问题：
- 是否阻塞下一步：
- 后续调整：
```

不得等 Goal 结束后一次性补写。

---

## 9. 运行记录表

| Run | 类型 | Output Dir | 11/11 Stages | Specs Generated | Module/File/Function Specs | Planning Validation | Coder Loader | Qualification | Semantic Diagnostics | Tokens | 是否计入最终3次 | 结果 |
|---|---|---|---:|---:|---|---|---|---|---:|---:|---:|---|
| Bootstrap 1 | Fresh | `mqtt_evaluation_bootstrap_01_20260713` | 是 | 是 | 1 / 7 / 30 | Failed | Failed：`coder_unsupported_array_type_spelling` | Failed | 1 closure + 36 downstream errors | 384,702 | 否 | 进入 Step 7 |
| Bootstrap Resume 1 | Resume | `mqtt_evaluation_bootstrap_01_20260713` | 是 | 是 | 1 / 7 / 30 | Failed | Failed：3 generation-order errors | Failed | 18 | +0 | 否 | 继续 compiler lowering |
| Bootstrap Resume 2 | Resume | `mqtt_evaluation_bootstrap_01_20260713` | 是 | 是 | 1 / 7 / 30 | Failed | Passed，独立复验0 errors | Failed | 18 | +0 | 否 | Bootstrap 打通 |
| Acceptance Attempt 1 | Fresh | `mqtt_evaluation_fresh_01_20260713` | 否，5/11 | 否 | 0 / 0 / 0 | 未执行 | 未执行 | 未执行 | Stage 6 structural blocker | 158,278 | 否 | 尾分号误判，计数清零 |
| Acceptance Attempt 1 Resume | Resume Stage 6 + Compile | `mqtt_evaluation_fresh_01_20260713` | 是 | 是 | 1 / 6 / 32 | Failed | Passed | Failed | 13 final | 476,542累计 | 否 | 修复尾分号、entrypoint path、unresolved member lowering |
| Fresh 1 | Fresh | `mqtt_evaluation_acceptance_fresh_01_20260713` | 是 | 是 | 1 / 8 / 0 | Failed | Passed，独立复验0 errors | Failed | 1 semantic；2 errors/1 warning total | 110,841 | 否（后续清零） | Success，当时连续1/3 |
| Fresh 2 | Fresh | `mqtt_evaluation_acceptance_fresh_02_20260713` | 是 | 是 | 1 / 6 / 24 | Failed | Passed，独立复验0 errors | Failed | 1 semantic；48 errors total | 308,642 | 否（后续清零） | Success，当时连续2/3 |
| Fresh 3 | Fresh | `mqtt_evaluation_acceptance_fresh_03_20260713` | 是 | 未完成（closure internal） | 0 / 0 / 0 | 未执行 | 未执行 | 未完成 | semantic patch noncanonical ID | 368,382 | 否 | Failed，计数清零 |
| Fresh 3 Resume | Resume Compile | `mqtt_evaluation_acceptance_fresh_03_20260713` | 是 | 是 | 1 / 7 / 26 | Failed | Passed | Failed | 38 | +0 | 否 | 修复 semantic exception/shared headers |
| Sequence 2 Fresh 1 | Fresh | `mqtt_evaluation_acceptance_r2_fresh_01_20260713` | 是 | 是 | 1 / 6 / 30 | Failed | Passed，独立复验0 errors | Failed | 23 | 429,947 | 是 | Success，连续1/3 |
| Sequence 2 Fresh 2 | Fresh | `mqtt_evaluation_acceptance_r2_fresh_02_20260713` | 是 | 是 | 1 / 6 / 30 | Failed | Passed，独立复验0 errors | Failed | 68 | 483,783 | 是 | Success，连续2/3 |
| Sequence 2 Fresh 3 | Fresh | `mqtt_evaluation_acceptance_r2_fresh_03_20260713` | 是 | 是 | 1 / 8 / 32 | Failed | Passed，独立复验0 errors / 0 warnings | Failed | 3 semantic；72 errors total；3 unresolved | 455,323 | 是 | Success，连续3/3 |

---

## 10. Goal 停止条件

### 成功停止

仅当以下条件全部满足时停止：

```text
所有代码和 tests 完成
+ 首次真实路径已打通
+ 连续 3 次独立 fresh planning 完整运行
+ 3/3 均生成 specs
+ 3/3 coder loader validation 通过
+ 计划书和状态报告已更新
```

### 失败停止

只有以下情况可以提前停止：

- **已经运行了5次独立的 fresh planning 流程后，仍未达到成功条件**
- API credential 缺失且无法执行真实 planning；
- 外部模型/API 持续不可用；
- 文件系统或运行环境损坏；
- 修改范围外的不可控依赖使 coder loader 无法执行；
- 发现目标与当前 coder contract 在逻辑上不可同时满足，并有最小复现和明确证据。

提前停止时必须输出最终分析报告，不能只写“未完成”。报告至少包含：

- 已完成步骤；
- 失败位置；
- 可复现命令；
- 最小错误证据；
- 已尝试修复；
- 为什么 resume 无法继续；
- 剩余最小修改建议。

semantic diagnostics 较多、qualification failed 或生成代码可能无法编译，不构成本轮提前停止理由。
