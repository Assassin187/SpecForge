# Spec Form Transfer Ablation

## 1. 实验目标

本实验只研究 coder-facing spec form 对协议实现生成的影响，不评价 facts agent 或 planning agent 的事实提取质量。所有实验组都从同一套完整 `specs-example/<protocol>_specs` 确定性转换得到，避免不同 LLM 规划结果带来的内容差异。

研究问题：

- **RQ1：** SpecFS 的 function-local `[PROMPT]/[RELY]/[GUARANTEE]/[SPECIFICATION]` 结构是否足以驱动可运行的网络协议实现？
- **RQ2：** 在保留 SpecFS function contract 的基础上，加入 module/file/function 三层工程结构是否改善代码生成？
- **RQ3：** 在三层结构上进一步加入 protocol-specific grounding 和 machine-readable constraints 后，是否继续改善实现正确性并减少 repair？

该实验对应如下递进关系：

```text
S1 SpecFS-Flat
    + coder-visible module/file/function hierarchy
    = S2 SpecFS-Hierarchy
    + SpecForge engineering semantics and protocol-specific constraints
    = S3 Full-SpecForge
```

## 2. 代码约束与设计结论

当前 coder 不能直接消费 flat SpecFS specs：

- `agent/coder/specs.py::discover_module_spec()` 要求 spec root 中恰好存在一个 `PROTOCOL_MODULE_SPEC`。
- loader 依赖 module → file → function linkage，并要求 `FUNCTION_SPEC.TRACE_ID` 的 parent 对应一个 `FILE_SPEC`。
- generation 按 `GENERATION_ORDER` 和 `MODULES[].FILES` 生成 header/source，并在 source prompt 中加入 module role、file dependency 和 machine-readable constraints。
- coder prompt 会直接消费 `ACCESS_PATHS`、`WIRE_MAPPING`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS` 和 `TEST_VECTORS`。

因此：

1. **S1 不应通过伪造空 module/file specs 接入现有 loader。** 这样会把被消融的 hierarchy 重新提供给 coder，使 S1 不再是 SpecFS-only。
2. **S1 使用独立的 thin adapter，但复用相同的 generation/compile/repair/behavior infrastructure。**
3. **S2 必须保留完整 function behavior。** SpecFS 原本就包含 Pre-Condition、Post-Condition 和 System Algorithm；若同时弱化 behavior，实验会混淆“结构形式不足”和“输入信息不足”。
4. **S1 与 S2 的 function-local semantic payload 必须完全相同。** S2 只额外暴露 module/file/function membership、dependency edges 和 generation order；不能同时加入 role、artifact、event model、ownership 或 wire/test 字段。

## 3. 三个实验组

### S1：SpecFS-Flat

目标：模拟 SpecFS/SYSSPEC 的原始 coder-facing form，只提供 function-local contract 和 raw header。

每个 function 生成一个 `.spec`，只包含：

```text
[PROMPT]
[RELY]
[GUARANTEE]
[SPECIFICATION]
  Pre-Condition
  Post-Condition
  Invariant
  System Algorithm
```

确定性投影规则：

| SpecForge 来源 | SpecFS-Flat 目标 |
| --- | --- |
| function `ROLE` + target source path | `[PROMPT]` 中的任务描述 |
| function signature | `[GUARANTEE]` raw C signature |
| `RELY.STRUCT/FUNC/VAR` | `[RELY]` 中对应 canonical C declarations |
| `RELY.*.ROLE` | 对应 declaration 前的自然语言注释 |
| `LOGIC.INPUT` | Pre-Condition |
| `LOGIC.OUTPUT` | Post-Condition |
| `LOGIC.ACTION` | System Algorithm |
| `LOGIC.INVARIANTS_USED` | Invariant |
| `EVENT.TRIGGER/PRECONDITION/INPUT` | Pre-Condition |
| `EVENT.STATE_CHANGE/RESPONSE` | Post-Condition |
| `EVENT.ACTION` | System Algorithm |

每个 header 使用 SpecFS `.header` 形式：

- 第一行：header dependencies；
- 后续内容：与其他组相同的 canonical C declarations。

S1 明确不向 coder prompt 暴露：

- protocol/module metadata；
- module role、dependency、artifact inventory 和 generation order；
- file role、file dependency graph 和 source data inventory；
- `FUNCTION_TYPE` 和独立 event model；
- `PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`WIRE_MAPPING`；
- `CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`；
- `CONSISTENCY_RULES`、`TEST_VECTORS`、`DOC_REF/TRACE_REFS`。

同一个 `.c` 对应的多个 function `.spec` 由 runner 按固定顺序拼接到一次 file-level generation prompt 中。拼接只改变 generation unit，不增加工程语义。

### S2：SpecFS-Hierarchy

目标：只测试 module/file/function hierarchy 的独立价值。S2 中每个 function 的 `[PROMPT]/[RELY]/[GUARANTEE]/[SPECIFICATION]` 和 raw `.header` 必须与 S1 逐字同源；唯一 treatment 是把原本仅供 runner 使用的部分结构关系暴露给 coder。

Coder-visible structural view：

| 层次 | S2 新增的可见结构 |
| --- | --- |
| Module | module `NAME`、module dependency edges、包含的 file IDs、`GENERATION_ORDER` |
| File | file `TRACE_ID`、header/source paths、dependency edges、包含的 function IDs |
| Function | 用于 membership/linkage 的 `TRACE_ID`；语义内容仍是与 S1 相同的 SpecFS blocks |

以下信息即使因当前 schema/loader 要求而存在于内部 envelope，也必须设为空、使用中性占位值或在 prompt renderer 中隐藏：

- `PROTOCOL.SPEC_VERSION/ROLES/DEFAULT_PORT/SCOPE`；协议名、binary/argv contract 作为三组共有的 evaluator input，不算 S2 treatment；
- module/file/function 的额外 `ROLE` 描述；function `ROLE` 只允许通过与 S1 相同的 `[PROMPT]` 出现；
- `MODULES[].ARTIFACTS`；
- structured `HEADER.DATA`、`SOURCE.DATA`、visibility inventory；header 仍只以与 S1 相同的 raw `.header` 暴露；
- `FUNCTION_TYPE`、独立 `EVENT` model、structured params、`NULLABLE/OWNERSHIP`；
- module/file/function `PUBLIC_SYMBOLS`；
- file/function `ACCESS_PATHS`、function `WIRE_MAPPING`；
- file/function `CALL_CONTRACTS`；
- module/file/function `FORBIDDEN_SYMBOLS`、`TEST_VECTORS`；
- `CONSISTENCY_RULES`、`DOC_REF/TRACE_REFS`。

这一边界比“hierarchical engineering core”更严格：S1 vs. S2 只测 hierarchy visibility，不把 architecture description、data inventory 或 event classification 一并加入。

已有 degradation 结果支持这一边界：

- `Gold-No-Behavior-Detail` 曾导致 MQTT compile failure 和 CoAP behavior degradation，因此 S1/S2 都应保留同一份 SpecFS-equivalent behavior contract；
- 单独删除 `WIRE_MAPPING` 未稳定造成退化，但该结果以保留 `ACCESS_PATHS` 为前提，因此 S2 应将 wire/access grounding 作为一个整体移除；
- 删除 `TEST_VECTORS` 后 CoAP 曾降至 0/4 behavior scenarios，通过 S2 vs. S3 可以继续检验 test oracle 的协议敏感性。

### S3：Full-SpecForge

直接使用未经降级的 `specs-example/<protocol>_specs` 和现有 coder。

相较 S2，S3 的主要增量为：

- protocol metadata、module/file/function roles 和 artifact inventory；
- structured public/private data、function classification、event model、parameter nullability/ownership；
- public/allowed symbol surface；
- canonical access paths；
- wire field → memory field/skip/reject mappings；
- cross-function call contracts；
- forbidden symbols 和 negative constraints；
- module/file/function test vectors；
- cross-module consistency rules；
- trace references（仅在实际非空时计入）。

结果解释必须以实际字段为准。目前 `specs-example` 中 `CALL_CONTRACTS` 没有实际条目，`DOC_REF` 也均为空，因此本轮实验不能把 S3 的效果归因于这两类字段。

## 4. S1 的兼容执行方案

### 4.1 Hidden execution manifest

为保证三组生成相同项目，S1 runner 使用 evaluator-owned `execution_manifest.json`：

```json
{
  "protocol": "mqtt",
  "binary_name": "mqtt_broker",
  "argv_contract": "./mqtt_broker <port>",
  "files": [
    {
      "header_path": "protocol/mqtt_packet.h",
      "source_path": "protocol/mqtt_packet.c",
      "function_specs": ["mqtt_packet_free.spec"],
      "order": 0
    }
  ]
}
```

manifest 只负责：

- target paths；
- function-to-source grouping；
- generation scheduling；
- Makefile source list；
- binary/argv runtime contract。

manifest 不得进入 generation 或 repair prompt，也不得包含 module role、behavior、types、wire rules、dependencies 或 test guidance。它是实验控制信息，不是 S1 spec。

### 4.2 Shared execution pipeline

三组统一使用：

- 相同的 `.c` file-level generation unit；
- 内容等价的 canonical public headers；
- 相同 LLM model、temperature `0.2`、top_p `0.2`；
- 相同 compile flags 和 deterministic Makefile；
- 最多 3 轮 source repair；
- 相同 compiler diagnostics extraction；
- 相同 protocol behavior verifier 和 runtime contract。

建议三组共享同一个 evaluator-owned `ExperimentBundle`，再由不同 renderer 控制 coder-visible view：

```text
ExperimentBundle
├── S1 renderer: SpecFS blocks only
├── S2 renderer: identical SpecFS blocks + hierarchy view
└── S3 renderer: full SpecForge specs
```

S1 不调用 `load_spec_bundle_from_root()`；S2 可以继续使用当前 schema 和 loader。两组都需要 ablation-specific prompt renderer，其余 compile、repair scheduling、usage accounting 和 behavior verification 尽量复用现有实现。

S2 的 loader envelope 与 coder-visible spec 必须分离：为通过 schema 而保留的 required placeholders 不能进入 prompt。当前 `_machine_constraints()` 总是输出 `ACCESS_PATHS/WIRE_MAPPING/TEST_VECTORS` 等键，因此不能原样复用。

### 4.3 Repair 公平性

repair prompt 必须遵守组别边界：

- S1：当前 source、compiler diagnostics、canonical/dependency headers、对应 SpecFS blocks；
- S2：当前 source、compiler diagnostics、与 S1 相同的 SpecFS blocks、S2 hierarchy view；
- S3：当前完整 coder repair prompt。

三组 repair rounds 和 repairable-file selection 相同。禁止在 S1/S2 repair 阶段重新注入 full-spec fields。

## 5. 公平性控制

### 5.1 统一输入

- 协议：MQTT、HTTP、CoAP、SMTP；
- source oracle：对应 `specs-example/*_specs`；
- S1/S2 由 deterministic transformer 从 S3 生成；
- 不为三个组分别调用 LLM 重新撰写 specs；
- 每个 transformed bundle 保存 mutation/projection manifest 和 input hash。

### 5.2 固定项目结构

- 三组使用相同 header/source paths；
- 三组使用相同 public signatures、public type declarations 和底层 dependency sets；
- 三组生成相同数量的 `.c/.h` files；
- 三组使用相同 binary name、argv contract 和 behavior scenarios；
- S1 的 layout 只存在于 hidden manifest，不向 coder 暴露。

保持 canonical ABI 一致是必要控制：SpecFS 本身允许 `.header` 提供 raw C declarations，因此 S1/S2 获得相同 public ABI 不构成信息泄漏。若三组使用不同 header guard/wrapper，应比较 normalized declaration hash 和 dependency set，而不是要求整个 header 文件逐字节相同。

S1 只能从 raw `.header` 和 `[RELY]` 获得 declarations；不得额外注入由完整 FILE_SPEC dependency graph 收集的整份 dependency headers。S2 可以看到显式 file dependency edges，但 function-local SpecFS blocks 仍不得改变。

### 5.3 Prompt leakage guard

S1 prompt 禁止出现：

```text
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
GENERATION_ORDER
CONSISTENCY_RULES
ACCESS_PATHS
WIRE_MAPPING
CALL_CONTRACTS
FORBIDDEN_SYMBOLS
TEST_VECTORS
specs-example
```

S2 prompt 禁止出现：

```text
SPEC_VERSION
DEFAULT_PORT
SCOPE
ARTIFACTS
HEADER.DATA
SOURCE.DATA
FUNCTION_TYPE
NULLABLE
OWNERSHIP
PUBLIC_SYMBOLS
ACCESS_PATHS
WIRE_MAPPING
CALL_CONTRACTS
FORBIDDEN_SYMBOLS
TEST_VECTORS
CONSISTENCY_RULES
DOC_REF
TRACE_REFS
```

runner 在每次 LLM call 前按 serialized field name/section label 扫描 messages，而不是对普通 prose 做宽泛 substring 匹配；发现泄漏则 fail closed，并把 prompt hash、命中项和 call subject 写入 manifest。

### 5.4 重复次数

主实验建议：

```text
4 protocols × 3 spec forms × 3 independent repetitions = 36 runs
```

若某组同一协议三次结果不一致，再增加至 5 次。运行顺序按 protocol/form/repetition 随机化，避免 API 时间和服务状态形成系统偏差。

## 6. 指标

### Primary metrics

- **End-to-end success**：最终 compile 通过且全部 required behavior scenarios 通过；
- **Held-out behavior scenario pass rate**；
- **Interop pass rate**：使用外部 client/server 的场景通过率；
- **Final compile success rate**。

### Secondary metrics

- initial compile success；
- repair iterations；
- repaired file count；
- failure stage 和 failure category；
- missing/undefined symbol、type、field、header 数量；
- forbidden/nonexistent symbol hallucination count；
- generated source LoC；
- generation/repair token usage；
- wall-clock time。

compile success 不能替代 behavior success。主要论文结论应以 end-to-end success 和 scenario pass rate 为核心。

`TEST_VECTORS` 是 S3 treatment 的一部分，但不得把与它逐字相同的 scenario 作为唯一 evaluator。结果应分开报告：

- in-spec scenarios：检验 coder 是否遵循已给 oracle；
- held-out protocol/interop scenarios：检验 specs 是否支持泛化实现，作为主要 behavior 指标。

## 7. 对比与结论口径

| 对比 | 回答的问题 |
| --- | --- |
| S1 vs. S2 | 在 function-local 语义相同的条件下，coder-visible hierarchy 是否优于 flat function specs |
| S2 vs. S3 | 完整 engineering semantics、protocol grounding、negative constraints 和 test oracle 是否带来额外收益 |
| S1 vs. S3 | SpecForge 完整 spec form 相对 SpecFS form 的总体收益 |

按 protocol × repetition 做 paired comparison，报告：

- 成功率和绝对差值；
- scenario pass-rate difference；
- repair-iteration difference；
- paired bootstrap 95% confidence interval；
- 典型失败类别分布。

样本规模较小时，应优先报告 effect size、逐协议结果和失败证据，不依赖单一 p-value。

## 8. 实施步骤

1. 新增 deterministic transformer：
   - `full -> specfs_flat`
   - `full -> specfs_hierarchy`
2. 为每个输出写 `transformation_manifest.json`，记录保留、投影、删除字段。
3. 对 S1/S2 做 semantic payload equality check：逐 function 比较规范化后的四个 SpecFS blocks 和 raw header declaration hash。
4. 对 S2/S3 执行 schema、loader 和 rendered-header validation。
5. 对 S1 执行：
   - `.header/.spec` grammar validation；
   - function coverage validation；
   - hidden manifest coverage validation；
   - prompt leakage validation。
6. 为三组生成并比较 normalized public declaration hashes 和底层 dependency sets。
7. 执行 file-level source generation。
8. 执行相同的 compile/repair loop。
9. 执行相同 behavior verifier。
10. 聚合 per-run `summary.json` 和 matrix-level `matrix_summary.json/.md`。

建议输出目录：

```text
evaluation/spec_ablation/out/<run_id>/
├── transformation/
│   └── <protocol>/<form>/
├── runs/
│   └── <protocol>/<form>/<repetition>/
└── matrix_summary.json
```

## 9. Validity Threats

- `specs-example` 是 code-derived oracle；本实验只能证明 spec form 对 coder usability 的影响，不能证明 planning agent 能从 technical documents 自动恢复同等质量的 specs。
- S1 adapter 如果向 prompt 泄漏 hidden manifest 内容，会破坏 RQ1；必须 fail closed。
- S1 vs. S2 可以近似解释 hierarchy 的独立作用；S2 vs. S3 同时改变表示形式和信息量，应表述为完整 SpecForge engineering contract 的组合效应，而非纯 JSON form effect。
- S3 字段在四个协议中的覆盖不均匀，应同时报告 per-protocol field counts。
- 当前 example specs 中为空的 `CALL_CONTRACTS`、`DOC_REF` 不能作为 S3 收益来源。
- S3 的 `TEST_VECTORS` 若与 evaluator scenarios 完全重合会造成 oracle leakage，因此主要结论必须包含 held-out behavior/interop tests。
- 完整 specs 更长，token 数量也是 treatment 的一部分；不应人为补齐或截断 S1，但必须报告各组 prompt/token size。
- 单一 model 的结论只适用于该 coder/model setting；若资源允许，可用第二个 model 做 robustness check。

## 10. 推荐决策

第二组采用 **SpecFS-Hierarchy**：其 function-local SpecFS blocks 与 S1 完全相同，只额外暴露 module/file/function membership、dependency edges 和 generation order。不要加入 module/file role、artifact、structured data inventory、event classification、ownership 或 protocol-specific fields。

第一组采用 **flat SpecFS artifacts + hidden execution manifest + dedicated prompt adapter**。不要让 S1 通过当前 `SpecBundle` loader，也不要把 synthetic module/file specs 暴露给 coder。这样可以在保持相同项目、生成单位、repair 和 verifier 的同时，真正测试 SpecFS form 本身是否足够。
