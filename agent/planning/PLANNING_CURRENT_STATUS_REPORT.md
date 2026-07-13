# SpecForge Planning Agent 当前状态、优化进展与稳定性报告

> 更新时间：2026-07-13  
> 本文件是 planning agent 当前实现、优化效果、实际运行结果和后续工作的唯一权威进度报告。原稳定性优化计划已完成历史信息合并并删除；历史 replay 只作为非回归证据，不计作 fresh stability success。

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

本轮优化取得了明显的execution stability进展，但**整体目标仍未完成**。

- 已形成canonical registry、typed delta、partition transaction、bounded recovery、deterministic assembly与严格qualification gate。
- Run 3 deterministic replay可稳定生成43个qualified specs files，并通过planning、coder loader和rendered-header checks。
- 原预算Goal执行4次fresh planning，4/4产生candidate、0/4 qualified，累计1,892,276 planning LLM tokens后按启动门禁停止。
- Goal结束后执行的优化前独立观察run完成11/11 stages、16/16 partitions，但semantic closure失败，仍为candidate-only。
- Token优化后执行2次fresh run，均完成11/11 stages和全部partitions，总token稳定在约40万，较优化前下降超过53%。
- 两次token优化run的semantic diagnostics均高于优化前基线；表面字段覆盖保持完整，但实现语义闭包没有通过非回归验收。
- 合计7次优化后fresh运行：candidate 7/7（100%），qualified 0/7（0%），连续fresh qualified为0。
- 当前pipeline在semantic diagnostics非空时跳过`compile_specs`，因此失败运行只产出candidate planning package，不产出结构完整的candidate specs。

最准确的状态是：

> 局部错误隔离、运行审计、严格门禁和prompt降耗已基本建立；完整fresh run的token目标已经连续两次达到，但semantic质量连续两次未通过非回归验收，尚不能稳定完成`protocol facts -> complete protocol specs`，也尚未满足“局部错误后仍物化结构完整candidate specs”的要求。

## 3. 已实现架构与实际效果

### 3.1 Candidate/qualified lifecycle

当前运行状态分为：

- `completed_with_qualified_specs`：semantic、planning schema、coder loader与rendered-header checks全部通过后，才发布`<protocol>_specs/`；
- `completed_with_candidate_only`：保留registry、committed overlays、unresolved partitions、diagnostics、provenance、metrics与manifests；
- `failed_internal`：用于facts read、registry/canonical/pipeline-state/deterministic invariant等不可恢复错误。

效果：validator error不会被降级，candidate不会被coder默认读取，错误运行也能保留完整审计材料。缺口是semantic closure失败时`candidate_specs_root`仍为`null`。

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

效果：partition rollback、survival和状态隔离已有fixtures；第二次token优化run的Stage 7 JSON syntax repair和Stage 8 local semantic correction均成功，流程继续完成。缺口是whole-stage correction再次失败后仍会提前结束后续stages；unresolved partition虽然可继续到final assembly，但随后跳过semantic closure和specs compilation。

### 3.6 三层Validation与Semantic Closure

当前显式区分：

| Layer | Recovery |
| --- | --- |
| structural | syntax repair或current partition/stage regeneration |
| binding | local correction或`ArtifactRequest` |
| semantic | 完整candidate plan上的bounded semantic closure |

`validation_layers.json`记录diagnostic ID、owner layer、recovery与outcomes；deterministic changes必须带`code`、`artifact_id`、`field`、`reason`和`source_artifact_id`。

Semantic closure执行primary patch和按diagnostic group分区的一轮correction。只有整个patch通过residual closure才会应用；失败时保留原plan并追加`semantic_patch_invalid`。该策略保持了严格门禁，但会丢弃部分已经结构合法、能够改善plan的operations，也不会继续生成candidate specs。

### 3.7 Prompt成本控制

当前API prompt使用compact JSON；Stage 3以后不再无条件携带完整raw facts和全部累计artifacts，而是根据Stage/partition投影直接依赖、registry catalog及trace refs对应的原始fact slice。Local correction复用同一slice，Stage 10在没有open assumptions和上游diagnostics时可0-token完成；metrics记录request count和prompt characters。

两次Fresh验证均通过550,000-token目标；第二次run即使触发syntax repair和local correction，仍保持相同量级，说明降耗对partition数量和局部恢复具有一定稳定性。生成效果对比及具体数据统一见5.4节。

## 4. 原优化步骤的代码审计状态

以下状态来自当前代码路径、tests与实际运行，不沿用旧计划中的checkbox结论：

| Step | 当前状态 | 代码审计结论 |
| --- | --- | --- |
| 0 Baseline/metrics | DONE | stage/partition survival、binding counts、repair、recovery、production、request与token metrics已实现；83项tests可验证 |
| 1 Lifecycle | PARTIAL | 三态、candidate package和qualified-only publication完成；semantic失败时不生成candidate specs |
| 2 Registry | MOSTLY DONE | 七类registry、immutable identity、typed binding与compiler读取完成；缺stage/partition原生typed view |
| 3 Typed Delta | PARTIAL | Stage 5/8/10 runtime shape/binding完成；不是API级schema enforcement，Stage 8 call intent语义校验不足 |
| 4 Amendment | PARTIAL | schema、validation、bounded audit与两类local rerun完成；允许kind/stage的executor覆盖不完整 |
| 5 Transaction | MOSTLY DONE | Stage 5/7/8 partition commit/rollback/correction完成；whole-stage失败仍可能提前结束后续流程 |
| 6 Validation | PARTIAL | 三层ownership、hard-failure分类与bounded semantic closure完成；semantic failure仍导致0 specs |
| 7 Replay/non-regression | DONE | Run 3 replay、历史failure fixtures、planning/coder/header checks与静态门禁完成 |
| 8 Fresh stability | NOT PASSED | 0/7 fresh qualified；未达到连续两次fresh qualified标准 |

因此，不能把本轮优化描述为“全部完成”。更准确的表述是：核心机制大部分落地，execution stability显著改善，qualification stability和完整candidate specs物化仍未完成。

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

## 6. 优化效果评估

### 已取得的效果

- Fresh流程从早期Stage 4/5/8 binding失败推进到最近的11/11 stages与semantic closure。
- High-risk partitions可独立commit/rollback，已完成工作不因局部失败丢失。
- unknown ID、kind mismatch、canonical drift与JSON错误有明确恢复和审计路径。
- Candidate/qualified publication边界准确，错误结果没有进入coder。
- Run 3 replay证明registry -> deterministic plan -> specs compiler -> coder validation链路可用。
- Compact JSON、fact slice、Stage/partition context projector和局部correction裁剪连续两次把完整run控制在约40.5万–40.8万tokens。
- Function signature、behavior、trace和test-vector字段覆盖没有因prompt裁剪下降。
- 最新83/83 tests、`compileall`、anti-hardcoding、reference isolation与`git diff --check`均通过。

### 尚未达到的效果

- 7次fresh均未生成qualified specs，qualification成功率仍为0%。
- 两次token优化run的semantic diagnostics均高于优化前基线，降耗通过但语义非回归连续失败。
- Semantic或unresolved错误存在时不生成结构完整candidate specs，与“局部错误不导致零规格产物”的目标不一致。
- Stage 8 typed IDs解决了kind问题，但没有解决call direction、callback binding和placeholder edge语义真实性。
- Semantic patch correction仍可能修复症状而非源关系，并因原子应用策略丢弃可用的局部改进。
- 当前tests主要证明机制和严格门禁，没有“semantic失败仍生成完整candidate specs”或“连续两次fresh qualified”的通过证据。

## 7. 当前最新进度与下一步

当前没有运行中的planning进程。最新状态是：源码机制保持83项tests通过；两次token优化fresh run均以约40万tokens完成11/11 stages，但semantic closure均失败，仍为candidate-only；没有candidate specs或qualified fresh specs可交给coder。

下一步按优先级执行：

1. **物化完整candidate specs。** Final plan只要满足结构lowering条件，即使semantic qualification失败，也编译到`candidate_planning_package/specs/`并附blocking diagnostics；qualified publication与coder默认输入仍保持严格隔离。
2. **前移Stage 4 inventory closure。** 校验`runtime_entrypoint`中的main、startup/run/cleanup symbols真实存在；lifecycle matrix引用的types/functions必须进入canonical inventory。
3. **限制Stage 6 signature类型。** 参数和返回类型必须绑定type/callback registry enum；禁止再次产生`mqtt_transport_t`之类未登记类型。
4. **增强Stage 8 call-intent validation。** 拒绝placeholder、方向反转、跨文件private callee和缺少参数provider的edges；允许无调用caller返回空集合。
5. **区分普通call、callback binding与lifecycle relation。** 分别建模registration/provider和create/destroy pairing，避免所有关系被错误降为普通call edge。
6. **从源关系修复dependency。** Module-order diagnostic携带来源call/type edge；correction必须修复源关系，不能只删除会被deterministic completion重新生成的派生dependency。
7. 完成上述改动后先做Stage 4/6/8定向resume，再以独立预算重新执行fresh stability；只有连续两次`completed_with_qualified_specs`且planning/coder/rendered-header checks全部通过，才能宣称稳定。

## 8. 修改与验证摘要

本轮核心实现文件：

- `planner.py`：typed stages、transactions、whole-stage/local correction与LLM调用编排；
- `prompts.py`：compact prompt、context projector及stage/repair/correction/amendment message builders；
- `facts.py`：trace-ref驱动的原始fact/evidence slice；
- `registry.py`：canonical registry与file alias修复；
- `amendment.py`：controlled inventory amendment；
- `validation_layers.py`：三层validation、ledger、hard-failure分类与provenance gate；
- `implementability.py`：deterministic completion、analyzer与bounded semantic closure；
- `pipeline.py`：lifecycle、candidate staging、qualification和failure manifests；
- `metrics.py`：stage/partition/recovery/token与request accounting；
- `compiler.py`、`models.py`、`validation.py`、`cli.py`、`README.md`与planning tests。

当前验证基线：

- `python -m compileall -q agent/planning`：pass；
- `python -m unittest discover -s agent/planning/tests -p 'test_*.py'`：83/83 pass；
- anti-hardcoding：pass；
- reference isolation：pass；
- `git diff --check -- agent/planning`：pass；
- protocol facts与generated run artifacts未被手工修改以制造成功。
