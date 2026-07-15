# SpecForge Planning Agent 当前状态与稳定性报告

> 更新时间：2026-07-15
> 核心对象：planning agent
> 文档地位：planning 当前实现、历史优化、真实运行结果和后续边界的唯一权威状态报告
> 当前结论：final root-fix pilot 的 materialization/Candidate-through 为 3/3，但 compile-contract ready 仅 1/3、sound build 为 0/3；稳定性未收敛
> 当前执行模式：root-fix tranche 已永久停止；不启动正式 RQ1

## 1. 研究定位与当前范围

SpecForge 的核心研究贡献是 planning agent。它位于 protocol facts 与 coder agent 之间，把 facts agent 提取的 factual protocol knowledge 转换为 implementation-oriented protocol specs：

```text
technical documents
-> facts agent
-> protocol facts
-> planning agent
-> protocol specs
-> coder agent
-> protocol implementation
```

Planning 的目标不是生成开放式 prose，而是产生 coder 可直接消费的 `SUMMARY.md`、`PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC`，并保留 type、wire、ownership、dependency、callback、call contract、test surface 与 traceability 信息。

本报告合并并替代原 `PLANNING_RQ1_SEMANTIC_OPTIMIZATION_REPORT.md`。早期 artifact-stability 证据与后续 specs-to-code compile 证据统一保留在本文；重复的架构说明、逐轮流水账和已过时结论已删除。

## 2. 当前结论

当前 planning 已经具备以下能力：

- 独立 fresh run 可以完成 11 个 structured stages，并生成非空 protocol specs；
- candidate specs 可以通过 schema/coder loader，被 coder 用于 source generation；
- semantic diagnostics、unresolved partitions 与 `qualification_passed` 能够保留，而不是通过清空 artifacts 制造成功；
- 历史 run 证明 coder generation 后的代码存在 bounded-repair clean-compile feasibility，但 final root-fix pilot 未复现；
- planning whole-fresh token accounting 与 hard-ceiling preflight 已生效。

当前尚不能声称：

- 同一冻结 revision 每次都能 materialize specs；
- 每次 candidate specs 都能让 coder 在 bounded repair 内 compile；
- `qualification_passed=true` 已稳定；
- runtime startup 或 MQTT minimum behavior 已通过；
- 当前两个 compile-success M2 工程可以直接用于正式 RQ1 方法比较。

最准确的表述是：

> Planning 的 materialization 与 Candidate-through 已稳定发生，但 compile-critical contracts、cross-file ABI 与 closed-world provider/runtime graph 仍跨 run 复现；最终 sound-build rate 为 0/3，因此当前路线在预声明的最后一次 root-fix 后判定未收敛。

## 3. 当前 Planning 架构

### 3.1 Structured planning 与 canonical registry

Planning 使用多阶段 structured workflow，从 scope、architecture、module/file layout、public artifacts、types、functions、behavior、call contracts、test vectors和dependency closure逐步形成 `implementation_plan.json`。

`registry.py` 维护 module/file/type/callback/function/constant/test 等 canonical artifacts：

- identity、kind、owner、visibility 与 definition stage 默认 immutable；
- typed resolve 拒绝 unknown ID、ambiguous alias 与 kind mismatch；
- Stage 5/7/8 使用 typed overlays 和 partition transaction；
- inventory amendment 必须显式、bounded 且带 provenance；
- final plan assembly 只整合已提交 artifacts，不应引入新的 semantic identity。

### 3.2 Validation、recovery 与 candidate lifecycle

当前 validation 分为 structural、binding 和 semantic 三层。Partition 按 `generate -> parse -> bind -> validate -> commit` 执行，失败时 rollback 当前 partition、记录 unresolved，并继续安全且独立的后续工作。

运行状态区分：

- `completed_with_qualified_specs`：全部 qualification gates 通过；
- `completed_with_candidate_only`：specs 可物化，但仍保留 semantic/unresolved diagnostics；
- `failed_internal`：facts、registry、pipeline state 或 deterministic invariant 无法继续。

Non-fatal diagnostics 默认不阻止 candidate specs materialization。Compiler 负责 deterministic lowering，不应在 registry 外临时发明 module/file/type/function identity。

### 3.3 Coder-facing contract

Coder 从 specs 确定性生成 headers，使用 LLM 生成 sources 和 `main.c`，再执行 compile 与最多 3 轮 repair。Planning 必须提供足够稳定的：

- public C ABI 与 type ownership；
- callback typedef、consumer、provider 与 user-data contract；
- direct-call argument provider 与 result usage；
- cross-file visibility 和 header/source dependencies；
- opaque resource create/destroy/access paths；
- exactly-one runtime entrypoint；
- wire constants、parser/encoder mappings 与 minimum behavior obligations。

## 4. 已完成优化与阶段性证据

### 4.1 Artifact-stability 阶段

2026-07-13 的第二组 acceptance 连续三次 fresh 均完成 11/11 stages、生成 specs 并通过 coder loader：

| Run | Stage/partition | Module/File/Function | Coder loader | Tokens |
| --- | --- | --- | --- | ---: |
| `mqtt_evaluation_acceptance_r2_fresh_01_20260713` | 11/11；16/16 | 1/6/30 | passed | 429947 |
| `mqtt_evaluation_acceptance_r2_fresh_02_20260713` | 11/11；17/17 | 1/6/30 | passed | 483783 |
| `mqtt_evaluation_acceptance_r2_fresh_03_20260713` | 11/11；18/18 | 1/8/32 | passed | 455323 |

这证明 artifact materialization 与 coder discovery 曾达到连续稳定，但三次均为 candidate-only，不能推导 compile 或 behavior utility。

该阶段还完成了：

- semantic failure 后继续 candidate compilation；
- recoverable whole-stage/partition continuation；
- array、dependency、signature、entrypoint、custom type 和 shared-header lowering；
- manifest 中 artifact success、qualification、survival 和 token accounting 分离；
- compact context projection，使完整 fresh 从早期约 879389 tokens 降到约 405000–408000 tokens，同时保留 stage artifacts。

### 4.2 Compile hardening 阶段

Step 7 修复了 deterministic completion、linker diagnostics routing、repair rollback 与 whole-fresh token accounting。Step 7-R1 完成 11/11 stages，生成 1/6/33 specs，schema/loader passed；planning 使用 771279 tokens。Coder 3/3 repair 后仍因 opaque session access contract 不闭合而 compile failed。

Step 8-R1 对 callback provider/consumer、decoded-data provider、routing lifecycle、transport input-buffer access/consume/identity 和 session state-query obligations进行了最小修复。代表性 Stage 4 请求为 16409 → 16516 tokenizer tokens，仅增加 0.65%，没有修改 `prompts.py`。

Step 8-R1 首次打通完整链路：

| 指标 | 结果 |
| --- | --- |
| Planning | 11/11 stages；1/6/34 specs |
| Specs | schema/coder loader passed |
| Planning tokens | 746305；accounting complete |
| Initial compile | failed |
| Bounded repair | 2/3 rounds；`compile_succeeded` |
| Final compile | passed；独立 clean rebuild passed |
| Manual edits | 0 |

### 4.3 Frozen Step 10 稳定性验证

冻结条件：

```text
HEAD = 5891e2449a0854c5b7fbe274a69088416c1f17f1
execution-source SHA256 = 9e6abdf442162bb96065e847dcdcb071d73b2e3c99233f58417bc0f32b6fa4fc
model = qwen3-max-2026-01-23
compiler = cc 12.3.0
max_repair_rounds = 3
planning target/hard ceiling = 749726 / 783804 tokens
```

三次 independent fresh 没有 resume、replay、fixed-spec 替代或失败补跑：

| Run | Planning | Specs/loader | Tokens | Repair | Final outcome |
| --- | --- | --- | ---: | ---: | --- |
| Step 10.1 | 11/11；15/16 partitions | 1/5/33；passed | 602042 | 3/3 | compile failed |
| Step 10.2 | 11/11；16/19 partitions | 1/7/38；passed | 727172 | 1/3 | compile passed |
| Step 10.3 | 11/11；17/20 partitions | materialization failed | 726377 | 0/3 | coder not run |

聚合结果：

| Gate | 结果 |
| --- | ---: |
| Fixed fresh sequence completed | 3/3 |
| 11/11 stages executed | 3/3 |
| Token accounting complete and <= target | 3/3 |
| Specs materialized and coder-loadable | 2/3 |
| Coder generation entered | 2/3 |
| Bounded-repair final compile passed | 1/3 overall；1/2 coder-started |
| Manual artifact/source edits | 0 |

Step 10.2 再次证明 candidate-only specs 可以在一轮 repair 后 compile；Step 10.1 和 Step 10.3 则证明当前 revision 尚未稳定。

## 5. 当前确认的 Candidate-through 模式

从 2026-07-15 起，后续 planning stability 与 RQ1 M2 workflow 使用 Candidate-through：

```text
planning fresh
-> 若 physical specs 已生成且 inventory 非空，始终进入 coder validate
-> coder validate passed 后执行 coder source generation
-> qualification_passed 只记录，不作为 early-stop 条件
-> 保存 repair 前原始代码
-> 在原始代码副本上执行 <= 3 rounds repair
-> 记录 final clean compile outcome
```

具体语义：

- `qualification_passed=false`、semantic diagnostics 或 unresolved partitions 不得单独阻止 coder；
- physical specs 不存在、inventory 为空时不能伪造 coder run；
- coder validate/schema/loader 失败是 Candidate-through 的真实 downstream outcome，必须保留；
- coder generation 只在 coder validate passed 后开始；
- 所有 runs 都进入分母，不能只选择 compile-success candidate；
- Candidate-through 不把 candidate 描述为 qualified，也不降低 diagnostics severity。

当前 `evaluation/planning_utility/full_specforge_adapter.py` 仍会在 `planning validate` return code 或 `qualification_passed=false` 时 early-stop，因此该决定尚需在下一份稳定化计划中实现和测试。

## 6. 已暴露的稳定性问题

### 6.1 Registry/materialization deterministic failure

Step 10.3 触发：

```text
RegistryBindingError: unknown_artifact_id: cannot bind
'type:lowering/mqtt/mqtt_transport_t' as ['callback', 'type']
```

Compiler 从 create/destroy ABI 临时推导 opaque handle，却没有把该 identity 注册到 canonical registry，随后 canonical resolve 失败。这是 deterministic pipeline defect，不是 token、model service 或 coder failure。

### 6.2 ABI、access 与 call-contract variance

Step 10.1 的最终 compile errors 包括：

- `mqtt_session_manager_is_connected` caller/callee 参数不一致；
- `mqtt_session_manager_mark_connected` 参数语义不一致；
- topic-router subscription argument ordering 漂移；
- generated/repair code调用 specs 未声明的 `mqtt_session_manager_get_connection`。

Planning 尚未稳定证明 session、router 与 connection 之间的 owner handle、access provider 和 result propagation。

### 6.3 Opaque type、runtime flow 与 test surface

Step 10.2 虽然最终 compile passed，planning diagnostics 仍包含 `opaque_type_by_value`、`runtime_flow_missing`、empty test inventory 和 wire-mapping test-vector缺口。这些问题没有阻止本次 compile，但仍会影响不同 fresh decomposition 下的稳定性和后续 behavior evaluation。

### 6.4 Budget-edge fallback

Step 10.2 与 Step 10.3 的下一次 Stage 9 model call都被 hard-ceiling preflight阻止。预算控制本身正确，且三次 Step 10 均低于 749726 target；风险在于 fallback state 必须始终可 materialize，不能在 budget stop 后因 registry/compiler invariant 丢失 specs。

### 6.5 Repair 前原始代码没有统一 workflow contract

Coder `generate` 默认在同一 project directory 内编译并 repair。虽然 `repair_existing` 会复制 source project，但 current Full-SpecForge adapter 尚未强制使用 `--skip-repair generate -> source hash/snapshot -> repair existing copy` 的两阶段流程，因此正式 runs 不能稳定保证 pre-repair code 与 post-repair code同时归档。

## 7. Token 与 Prompt 约束

当前 whole-fresh baseline 使用 Step 7 的 681569 tokens，后续目标与硬上限保持：

```text
target <= 749726  (baseline +10%)
hard ceiling <= 783804  (baseline +15%)
single request input <= 64000
single response <= 16000
```

Prompt 修改仍是最后手段。优先顺序为：

```text
deterministic control flow
-> registry/typed validation
-> compiler preservation
-> Candidate-through adapter orchestration
-> protocol-agnostic coder contract bug
-> earliest authoritative prompt stage
```

不得通过增加重复全量 facts、plan、diagnostics 或 inventory context修复 deterministic defects。若确需 prompt replacement，同一 representative fixture 的 tokenizer input增长目标不超过10%，硬上限不超过15%。

## 8. 当前 RQ1 Readiness

当前能力足以进入独立 stabilization pilot，但不足以直接开始正式 RQ1 publication runs：

- 两个 M2 compile-success 工程都是 `qualification_passed=false`、按 final compile success 选择的 post-repair candidates；
- 当前 contract-closure 表混合 M0/M1 pre-repair 与 M2 selected post-repair，不能估计方法效应；
- 两个 M2 的 required-obligation realization 为 100%，semantic-grounding closure 为 60%，但 executable call-path closure 为 0%；
- 当前两个 compiled M2 没有统一 runtime/behavior evidence；
- Candidate-through 尚未在正式 adapter 中实现；
- known deterministic materialization defect 尚未修复。

正式 RQ1 必须在稳定化修改完成、三次预声明 fresh sequence结束、revision/inputs/verifier/measurement冻结后另行启动。正式数据不得挑选成功样本，也不得把本报告中的工程调试 runs混入 publication denominator。

## 9. 当前验证基线与证据

Step 10 freeze 前验证：

```text
planning tests: 148/148 passed
planning utility tests: 33/33 passed
coder tests: 29/29 passed
compileall: passed
git diff --check: passed
```

关键 evidence：

- Step 8-R1 planning：`agent/planning/out/mqtt_step8_r1_fresh_20260714_204807/`
- Step 8-R1 coder：`evaluation/planning_utility/out/step8_r1_fresh_20260714_204807/`
- Step 10.1：`agent/planning/out/mqtt_step10_1_fresh_20260714_213220/` 与 `evaluation/planning_utility/out/step10_1_fresh_20260714_213220/`
- Step 10.2：`agent/planning/out/mqtt_step10_2_fresh_20260714_215355/` 与 `evaluation/planning_utility/out/step10_2_fresh_20260714_215355/`
- Step 10.3：`agent/planning/out/mqtt_step10_3_fresh_20260714_221315/`
- Frozen sequence：`agent/planning/out/step10_sequence_20260714/EVIDENCE.md`
- RQ1 contract closure：`evaluation/planning_utility/out/data/contract_closure_metrics_summary.md`

## 10. 下一步

`PLANNING_CANDIDATE_THROUGH_STABILITY_PLAN.md` 已按预声明完成，恰好执行3次 fresh planning → coder sequence，没有补跑或运行间修改。后续若继续研究，应作为新目标处理 compile-contract variance 和正式RQ1 readiness；不得把本 sequence 中唯一 compile-success Run 1单独选作正式样本。

## 11. Candidate-through Stability Sequence 最终结果

冻结 revision下三次独立运行均为 `fresh=true`、`resume=false`、`replay=false`：

| Run | Stage/partition | Specs | Qualified | Coder validate | Original preserved | Repair | Final compile | Planning tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| 1 | 11/11；15/16 | 1/7/29 | false | passed | true | 2/3 | passed | 606486 |
| 2 | 11/11；16/18 | 1/6/33 | false | passed | true | 3/3 | failed | 684815 |
| 3 | 11/11；16/20 | 1/9/36 | false | passed | true | 3/3 | failed | 763558 |

分层结论：

- materialization stability为3/3，且没有`deterministic_internal_invariant`；
- Candidate-through stability为3/3：所有specs-bearing run均通过 coder validate、生成original code并保持hash不变；
- 三次initial clean compile均failed，bounded repair后final clean compile为1/3；
- `qualification_passed`为0/3，Run 3超过749726 token target但低于783804 hard ceiling；
- runtime/behavior smoke按预声明未运行，因此不能声称behavior correctness；
- 当前不具备直接进入正式RQ1的充分条件，原因是compile stability仍只有1/3。

Machine-readable evidence位于`agent/planning/out/candidate_through_stability_20260715/freeze_record.json`与`sequence_summary.json`。Adapter summary 的initial compile字段使用legacy flat key；最终ledger以`pre_repair_diagnostics.json`中的`diagnostic_snapshot.build.returncode`为权威值，三次均为2，未改变本次分类。Sequence结束后没有修改execution或measurement source。

## 12. Final Root-fix Pilot 最终结果

2026-07-15 按 `PLANNING_CANDIDATE_THROUGH_STABILITY_PLAN.md` 完成最后一次 root-fix。R0–R5 在 0 fresh、0 planning model call 条件下完成 shared C type/header closure、opaque/provider diagnostics、unresolved-partition isolation、bounded compile-critical slice、repair fingerprint monotonicity与 sound-build measurement；冻结前验证为 planning `157/157`、coder `33/33`、planning utility `36/36`。

冻结后恰好执行三次 independent fresh planning → coder sequence，无 resume、replay、replacement run、run 间 source 修改或人工 artifact/code 编辑：

| Run | Stage/partition | Specs | Contract ready | Initial compile | Repair stop | Final compile | Sound build | Tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| 1 | 11/11；16/17 | 1/6/33 | true | failed | `spec_contract_blocked`，0 rounds | failed | failed | 702824 |
| 2 | 11/11；16/17 | 1/6/29 | false | failed | `repair_stagnated`，1 round rollback | failed | failed | 675871 |
| 3 | 11/11；13/16 | 1/5/31 | false | failed | `spec_contract_blocked`，0 rounds | failed | failed | 636364 |

聚合结果：physical specs、coder validate、original hash preservation 均为 `3/3`；compile-contract ready=`1/3`；initial/final raw compile=`0/3`；sound build=`0/3`；qualification=`0/3`；planning target 与 hard ceiling 均为 `3/3`。

问题性质相较旧 sequence 进一步变化：repair 不再用三轮猜名掩盖 planning/spec contract gap，两个 run 在 0 rounds 明确停止，另一个 run 因 fingerprints 未形成严格子集而立即 rollback。这说明 bounded failure detection 已收敛；但 RF2 cross-file ABI 与 RF3 closed-world provider/runtime family 仍跨 run 复现，compile stability 本身没有收敛。

最终判定为 `NEGATIVE_STABILITY_RESULT`。当前只允许陈述“materialization/Candidate-through 优于早期版本，且错误阻断更 sound”；不能陈述 M2 compile stability 优于 M0/M1，也不具备启动 formal RQ1 的工程条件。本优化方向到此永久停止，不追加 fresh、不继续 prompt tuning。权威 machine-readable evidence：`agent/planning/out/final_root_fix_pilot_20260715/freeze_record.json` 与 `sequence_summary.json`。
