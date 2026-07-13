# SpecForge Planning Agent 当前状态、优化进展与稳定性报告

> 更新时间：2026-07-13  
> 本文件是 planning agent 当前实现、优化效果、实际运行结果和后续工作的唯一权威进度报告。`PLANNING_EVALUATION_STABILITY_PLAN.md` 记录本轮逐步执行证据；历史 replay 只作为非回归证据，不计作 fresh stability success。

## 1. 研究定位与范围

SpecForge 的核心研究贡献是 planning agent：它填补 technical documents -> protocol facts 与 engineering specifications -> code 之间的缺口，把 facts agent 输出的 protocol facts 转换为 coder agent 可直接消费的 implementation-oriented protocol specs。

```text
technical documents
  -> facts agent
  -> protocol facts
  -> planning agent
  -> protocol specs
  -> coder agent
  -> protocol implementation
```

本轮优化范围仅限 `agent/planning`：重构跨Stage identity、typed overlay、局部恢复、semantic closure、candidate/qualified lifecycle、prompt成本控制及观测能力。未修改protocol facts、facts agent、coder、coder schema、`specs-example`或generated C/H；未运行coder source generation/repair、最终项目compile或runtime behavior tests。

## 2. 当前结论

本轮 **evaluation artifact stability 目标已经完成**。

- 保留了canonical registry、typed overlays、partition transaction、bounded recovery、diagnostics、provenance、resume、token metrics和deterministic compiler，没有另建strict/evaluation双模式。
- 只要facts、pipeline state、registry和final structural lowering正常，pipeline现在即使存在semantic diagnostics或semantic patch failure，也会执行`compile_specs`并保存candidate specs。
- semantic diagnostics、planning validation和qualification结论保持原severity并进入manifest；candidate specs可由现有coder loader读取，不会被误发布为qualified specs。
- Bootstrap fresh首次暴露coder array spelling问题；两次compile-only resume依次修复array lowering和generation-order lowering后，真实路径首次产出coder-loadable specs。
- 第一组acceptance因Stage 6尾分号和后续semantic patch异常两次清零；通过失败run的resume定位并修复后，重新从全新输出目录开始第二组验收。
- 第二组3次独立fresh运行均为11/11 stages、`specs_generated=true`、`coder_loader_passed=true`，形成连续3/3。
- 三次最终fresh均为candidate-only且`qualification_passed=false`；semantic qualification、generated code compile和behavior tests仍未完成，也不属于本轮目标。

最准确的状态是：

> planning 已达到连续3次fresh生成coder-loadable specs的artifact stability，可用于RQ1后续coder生成实验；该结论不代表specs已qualified、compile-ready或behavior-correct。

## 3. 已实现架构与实际效果

### 3.1 Candidate/qualified lifecycle

当前运行状态分为：

- `completed_with_qualified_specs`：semantic、planning schema、coder loader与rendered-header checks全部通过后，才发布`<protocol>_specs/`；
- `completed_with_candidate_only`：保留registry、committed overlays、unresolved partitions、diagnostics、provenance、metrics与manifests；
- `failed_internal`：用于facts read、registry/canonical/pipeline-state/deterministic invariant等不可恢复错误。

效果：validator error不会被降级，错误运行仍保留完整审计材料；semantic closure失败时，只要structural lowering条件成立，`candidate_specs_root`会指向实际生成的specs。只有qualification全部通过时才发布qualified specs。

### 3.2 Canonical Symbol Registry

`registry.py`维护module/file/type/callback/function/constant/test七类artifact：

- canonical identity、kind、owner、visibility与definition stage默认immutable；
- typed resolve拒绝unknown ID、ambiguous alias和artifact kind mismatch；
- snapshot支持resume/replay；
- split header/source identity不会互相占用header alias；
- semantic patch与`ArtifactRequest`新增identity必须显式且带provenance。

效果：最近两次token优化run的Stage 5/8均未出现unknown ID或artifact kind mismatch。缺口是`typed_view()`只原生支持kind/module/file过滤，没有完整stage/partition scope接口。

### 3.3 Typed high-risk stages

- Stage 5采用`{type_id, definition_overlay}`，identity来自registry；
- Stage 7按module/file生成behavior overlays；
- Stage 8按caller file分区，只接受六字段function-to-function call edges；name、signature、`RELY.FUNC`、`CALL_CONTRACTS`与dependency确定性派生；
- Stage 10只接受无法唯一推导的ordering/architecture choices，禁止重复inventory；
- Stage 11是0-token deterministic reconciliation，不新增identity。

效果：最近两次token优化run的high-risk partitions分别为18/18和15/15完成。缺口包括：typed enum目前是prompt contract加post-generation validation，并非LLM API级JSON Schema；Stage 8只验证edge shape和function kind，没有拒绝`condition=never`、`no call occurs`、方向反转、跨文件private callee或缺少参数provider的调用边。

### 3.4 Controlled Inventory Amendment

`ArtifactRequest`会校验kind、semantic role、owner、required-by、reason、provenance与visibility；每个scope最多一轮。系统只bind已有artifact或注册`status=requested` identity，不自动生成signature、fields、behavior或API family。

已覆盖Stage 5 type/callback amendment和Stage 8 function amendment的局部rerun。缺口是executor没有完整覆盖所有允许kind/stage组合，特别是constant及非预期stage来源；文档层将`proposed_name`视为可选，而当前validator实际要求非空。

### 3.5 Transaction与局部恢复

Stage 5/7/8按`generate -> parse -> bind -> validate -> commit`执行：

- syntax repair、semantic correction、inventory amendment独立计费与落盘；
- typed failure最多一次local correction；
- correction失败则rollback当前partition，保留blocking diagnostic并继续独立partitions；
- non-partition whole-stage structural/binding failure最多一次correction；
- unresolved结果不会进入qualified publication。

效果：partition rollback、survival和状态隔离已有fixtures；recoverable whole-stage或partition failure会物化schema-valid empty artifact、写入unresolved ledger并继续后续独立stage。Essential final assembly、registry或pipeline invariant仍属于不可恢复失败。Unresolved/semantic diagnostics不再跳过specs compilation。

### 3.6 三层Validation与Semantic Closure

当前显式区分：

| Layer | Recovery |
| --- | --- |
| structural | syntax repair或current partition/stage regeneration |
| binding | local correction或`ArtifactRequest` |
| semantic | 完整candidate plan上的bounded semantic closure |

`validation_layers.json`记录diagnostic ID、owner layer、recovery与outcomes；deterministic changes必须带`code`、`artifact_id`、`field`、`reason`和`source_artifact_id`。

Semantic closure执行primary patch和按diagnostic group分区的一轮correction。只有整个patch通过residual closure才会应用；失败时保留原plan并追加`semantic_patch_invalid`。`RegistryInvariantError`等invalid semantic patch现在转为可审计diagnostic，而不是逃逸为`failed_internal`；无论patch是否成功，只要结构可lower，都会继续生成candidate specs。

### 3.7 Prompt成本控制

当前API prompt使用compact JSON；Stage 3以后不再无条件携带完整raw facts和全部累计artifacts，而是根据Stage/partition投影直接依赖、registry catalog及trace refs对应的原始fact slice。Local correction复用同一slice，Stage 10在没有open assumptions和上游diagnostics时可0-token完成；metrics记录request count和prompt characters。

两次Fresh验证均通过550,000-token目标；第二次run即使触发syntax repair和local correction，仍保持相同量级，说明降耗对partition数量和局部恢复具有一定稳定性。生成效果对比及具体数据统一见5.4节。

## 4. Evaluation artifact stability执行状态

以下状态对应`PLANNING_EVALUATION_STABILITY_PLAN.md`的Step 0-9：

| Step | 状态 | 结论 |
| --- | --- | --- |
| 0 控制流与基线 | DONE | 冻结83项tests及旧行为，定位semantic gate阻断`compile_specs` |
| 1 specs物化 | DONE | structural lowering正常时始终编译candidate specs |
| 2 recoverable continuation | DONE | 可恢复whole-stage/partition失败写ledger并继续11 stages |
| 3 coder-loadable lowering | DONE | 修复array、dependency、signature、entrypoint、custom type和shared header lowering |
| 4 manifest/可观测性 | DONE | artifact success与qualification分离，manifest记录specs/loader/semantic/survival/token |
| 5 静态与回归 | DONE | 94项tests覆盖anti-hardcoding、reference isolation、replay和新增failure fixtures |
| 6 bootstrap fresh | DONE | 11/11并生成specs；首次loader失败后进入resume定位 |
| 7 bootstrap resume | DONE | 两次compile-only resume后独立coder loader通过 |
| 8 连续3次fresh | DONE | 第二组acceptance连续3/3生成coder-loadable specs |
| 9 冻结与报告 | DONE | 报告、最终回归、定向门禁、scope和完整diff审计均完成 |

本轮完成的是artifact stability，不是qualification stability；原研究架构和严格semantic diagnostics均保留。

## 5. Replay与Fresh运行结果

### 5.1 Run 3 deterministic replay

| 项目 | 结果 |
| --- | --- |
| 模型调用 | 0；复用已落盘stage artifacts与semantic patch |
| Registry | 111 entries，覆盖七类artifact |
| Typed overlays | Stage 5为8个definitions；Stage 8为21条六字段call edges |
| Plan | 5 modules / 10 files / 31 functions |
| Specs | 43 files，含Module/File/Function specs与`SUMMARY.md` |
| Validation | planning、coder loader、rendered headers均0 errors / 0 warnings |
| Non-regression | 与冻结specs逐文件无差异 |

该结果证明deterministic pipeline可以处理一组引用一致的artifacts，但不是fresh success。

### 5.2 原预算Goal的4次Fresh runs

| Run | Stage/partition survival | Candidate | Qualified | Tokens | 阻断 |
| --- | --- | ---: | ---: | ---: | --- |
| 1 | 3/11；未进入partition | 是 | 否 | 75,072 | split source占用header alias，Stage 4 owner binding ambiguous |
| 2 | 3/11；未进入partition | 是 | 否 | 70,330 | Stage 4输出`visibility=opaque`，当时whole-stage correction未接通 |
| 3 | 11/11；16/17 partitions | 是 | 否 | 892,243 | Stage 8把payload type伪造成function callee，correction失败 |
| 4 | 9/11；17/17 partitions | 是 | 否 | 854,631 | Stage 10 choices遗漏`affected_artifact_ids`，correction重复遗漏 |

Goal累计1,892,276 planning LLM tokens。Run 4启动前累计1,037,645，符合1.5M启动门禁；Run 4自然完成后超过阈值，因此没有再启动Goal内planning进程。

### 5.3 优化前独立观察run

位置：`agent/planning/out/mqtt_observation_fresh_20260711_01`。

| 项目 | 结果 |
| --- | --- |
| Run status | `completed_with_candidate_only` |
| Stage survival | 11/11 |
| Partition survival | 16/16 |
| Plan inventory | 6 modules / 6 files / 10 types / 25 functions |
| Structured usage | 789,174 tokens |
| Stage 9 syntax repair | 11,999 tokens；非法hex JSON修复成功 |
| Semantic patch | 78,216 tokens；primary + 5个correction groups |
| 总usage | 879,389 tokens |
| Initial semantic diagnostics | 12 |
| Final report | 原12项 + `semantic_patch_invalid`，共13 errors |
| Candidate specs / qualified specs | 0 / 0 |
| Coder validation | 未运行 |

Initial diagnostics包括3个module generation-order conflicts、5个callback signature mismatches、1个opaque destructor缺失和3个access-service缺失。试验patch经过5组correction后仍有6个residual errors，因此整体未应用。

该run暴露的主要问题不是ID binding，而是Stage 8语义：模型输出了`condition=never`、`no call occurs`的placeholder edges，以及codec/session/transport -> broker的反向调用边。它们结构合法，却产生虚假的callback关系、反向module dependencies和generation-order cycles。Semantic patch只删除派生dependency而未删除源call edges，deterministic completion会重新生成这些dependency。

### 5.4 Token优化Fresh runs与生成质量对比

位置：

- Run 1：`agent/planning/out/mqtt_token_optimization_fresh_01_20260711`
- Run 2：`agent/planning/out/mqtt_token_optimization_fresh_02_20260711`

两次均为独立fresh运行，没有使用resume。

| 项目 | 优化前观察run | Token Run 1 | Token Run 2 |
| --- | ---: | ---: | ---: |
| Run status | candidate-only | candidate-only | candidate-only |
| Stage / partition survival | 11/11；16/16 | 11/11；18/18 | 11/11；15/15 |
| Modules / files | 6 / 6 | 5 / 8 | 5 / 7 |
| Types / functions | 10 / 25 | 10 / 27 | 12 / 33 |
| Structured usage | 789,174 | 367,217 | 344,321 |
| Repair / local correction | 11,999 / 0 | 0 / 0 | 3,189 / 19,687 |
| Semantic patch | 78,216 | 38,190 | 41,143 |
| 总usage | 879,389 | 405,407 | 408,340 |
| Initial / final diagnostics | 12 / 13 | 22 / 23 | 38 / 39 |
| Binding errors | 0 | 0 | 0 |
| Candidate specs / qualified specs | 0 / 0 | 0 / 0 | 0 / 0 |

Token验收线为550,000，两次均通过；相对优化前分别下降53.9%和53.6%。Run 2即使发生syntax repair和local correction，总usage仍仅比Run 1高0.7%，说明降耗效果可重复。

严格来说，三次Fresh run均没有生成`*_spec.json`或`SUMMARY.md`：`candidate_specs_root`与`specs_root`均为`null`，因此当前只能比较spec compiler之前的candidate plan和Stage artifacts，不能宣称最终protocol specs质量已经验证。

表面完整度保持稳定：三个run的所有functions均有signature、behavior、trace refs和test vectors；token优化后artifact数量甚至增加。但implementation-oriented semantic quality明显下降：

- 优化前不存在跨文件private调用；Run 1有4项，Run 2有8项。
- `access_service_missing`从3项增至5项和23项，新增call edges没有同步证明session/router/decoder/connection等参数provider。
- Run 1出现4项`unresolved_type`，包括signature引用未登记的`mqtt_transport_t`。
- Run 2的Stage 4声明`runtime_entrypoint.main_function=main`，但function inventory没有`main`，产生`runtime_entrypoint_missing`。
- Stage 8仍把callback binding、lifecycle pairing或状态前置关系表示成普通call edge，持续产生反向dependency和generation-order conflict。

因此，当前最准确的判断是：compact JSON和context slicing稳定降低了token，且没有破坏schema字段生成；但两次token优化run均未保持优化前的semantic基线。由于Fresh LLM存在随机性，现有样本不能把全部退化严格归因于context裁剪，但已足以判定语义非回归没有通过。

### 5.5 Evaluation artifact stability修复与最终验收

本节记录本轮新目标的真实运行路径。所有fresh run均使用同一MQTT protocol facts，不使用resume/replay、不复制历史artifacts、不人工修改运行产物。

Bootstrap路径：

| Run | Stage survival | Specs | Coder loader | Tokens | 结论 |
| --- | --- | --- | --- | ---: | --- |
| `mqtt_evaluation_bootstrap_01_20260713` | 11/11 | 1/7/30 | Failed：array type spelling | 384,702 | 进入resume定位 |
| 同run compile-only resume 1 | 11/11 | 1/7/30 | Failed：3 generation-order errors | +0 | 继续lowering修复 |
| 同run compile-only resume 2 | 11/11 | 1/7/30 | Passed，独立0 errors | +0 | 首次真实路径打通 |

第一组acceptance未计入最终结果：`mqtt_evaluation_fresh_01_20260713`在Stage 6因signature尾分号误判失败；resume修复尾分号、entrypoint path和unresolved member lowering后loader通过。随后`mqtt_evaluation_acceptance_fresh_01_20260713`、`..._02_...`先后成功，但`..._03_...`发生semantic patch noncanonical ID内部异常，连续计数清零。该失败run的resume又暴露duplicate shared header owner；修复semantic exception与shared-header lowering、重新通过94项tests后，才启动第二组全新验收。

最终连续3次fresh证据：

| Run | Stage / partition survival | Module/File/Function | Planning / Qualification | Coder loader | Semantic diagnostics | Tokens |
| --- | --- | --- | --- | --- | ---: | ---: |
| `mqtt_evaluation_acceptance_r2_fresh_01_20260713` | 11/11；16/16 | 1/6/30 | Failed / Failed | Passed，独立0 errors / 0 warnings | 23 | 429,947 |
| `mqtt_evaluation_acceptance_r2_fresh_02_20260713` | 11/11；17/17 | 1/6/30 | Failed / Failed | Passed，独立0 errors / 0 warnings | 68 | 483,783 |
| `mqtt_evaluation_acceptance_r2_fresh_03_20260713` | 11/11；18/18 | 1/8/32 | Failed / Failed | Passed，独立0 errors / 0 warnings | 3 semantic；72 total；3 unresolved | 455,323 |

三轮均满足`fresh=true`、`resume=false`、`replay=false`、`specs_generated=true`、exactly one module spec、可发现FILE_SPEC/FUNCTION_SPEC、`coder_loader_passed=true`。最终sequence累计1,369,053 planning LLM tokens，连续成功计数为3/3。

## 6. 优化效果评估

### 已取得的效果

- Fresh流程已从早期Stage 4/5/8 binding失败推进到连续3次11/11 stages、完整specs materialization和coder loader pass。
- High-risk partitions可独立commit/rollback；recoverable whole-stage失败也可生成empty artifact、记录unresolved并继续。
- Semantic patch failure不会吞掉diagnostics，也不会在结构可lower时阻止candidate specs生成。
- Candidate/qualified publication边界保持明确：artifact success不伪装成qualification success。
- Compiler以通用结构规则lower array、dependency、signature、entrypoint、自定义member和shared header，没有复制MQTT示例inventory或发明协议行为。
- Run 3 replay和新增fixtures共同证明registry -> deterministic plan -> specs compiler -> coder loader链路非回归。
- 最终连续3次fresh分别产出1/6/30、1/6/30和1/8/32 specs，coder loader均通过。
- 当前代码基线为94项tests；freeze时已再次执行`compileall`、全量tests、anti-hardcoding/reference isolation、replay/coder-loader/header定向fixtures和`git diff --check`，全部通过。

### 尚未达到的效果

- 最终三次fresh均未生成qualified specs，qualification成功率仍为0%。
- Semantic diagnostics分别为23、68、3，第三轮另有3个unresolved partitions；semantic closure仍未稳定。
- Stage 8 typed IDs解决了kind问题，但没有解决call direction、callback binding和placeholder edge语义真实性。
- Semantic patch correction仍可能修复症状而非源关系，并因原子应用策略丢弃可用的局部改进。
- Dependency source-edge repair、access-service provider闭包、runtime entrypoint质量仍是后续semantic qualification工作。
- 本轮没有运行coder source generation/repair、generated code compile或behavior tests，不能据此判断protocol implementation质量。

## 7. 当前最新进度与下一步

当前没有运行中的planning进程。最新状态是：第二组3次独立fresh均完成11/11 stages、生成candidate specs并通过现有coder loader，evaluation artifact stability目标已达到。

后续工作必须作为独立目标授权，不能混入本轮结论：

1. 提升semantic qualification稳定率，处理call direction、callback/lifecycle relation和placeholder edges。
2. 修复dependency source-edge、access-service provider、signature type和runtime entrypoint语义闭包。
3. 评估candidate specs的semantic密度和跨协议迁移能力。
4. 另行执行coder source generation/repair、generated code compile和behavior tests。

## 8. 修改与验证摘要

本轮evaluation artifact stability实际修改文件：

- `pipeline.py`：无条件candidate compilation、planning/coder validation、manifest和artifact-success lifecycle；
- `planner.py`：recoverable whole-stage continuation、empty artifacts、unresolved ledger及semantic patch invariant diagnostic；
- `compiler.py`：coder-loadable structural lowering；
- `models.py`、`cli.py`：artifact success语义和CLI结果输出；
- `tests/test_pipeline.py`：新增lifecycle、continuation、lowering、semantic exception和shared header fixtures；
- `README.md`：同步candidate/qualified行为与manifest contract；
- `PLANNING_EVALUATION_STABILITY_PLAN.md`、本报告：逐Step执行与freeze证据。

删除或替换的旧行为包括：semantic diagnostics直接跳过`compile_specs`、validation失败后移除candidate specs、recoverable whole-stage错误直接终止、coder-facing forward/cyclic dependency以及未规范化的array/signature/header owner输出。没有引入strict/evaluation平行模式。

当前验证基线：

- `python -m compileall -q agent/planning`：pass；
- `python -m unittest discover -s agent/planning/tests -p 'test_*.py'`：94/94 pass；
- anti-hardcoding：pass；
- reference isolation：pass；
- `git diff --check -- agent/planning`：pass；
- protocol facts与generated run artifacts未被手工修改以制造成功。

冻结结论：planning已达到连续3次fresh生成coder-loadable specs的artifact stability，可用于RQ1后续coder生成实验。该freeze point不包含semantic qualification、generated code compile或behavior correctness保证。
