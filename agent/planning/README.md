# SpecForge Planning Agent

`agent/planning` 实现 protocol facts -> implementation-oriented protocol specs 的中间层。
它的核心职责是把 facts agent 输出的结构化协议事实转换为 coder agent 可消费的
`PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC` 文件集。

## 阶段边界

1. `Protocol Facts`
   - 输入 `protocol_facts.json` 是协议语义唯一事实来源。
   - planning 只读取 facts，不修改 facts。

2. `Engineering Knowledge`
   - `knowledge.py` 根据 facts 归一化 protocol characteristics。
   - generic rules 由特征触发，例如 stream transport、publish-subscribe、
     stateful session、routing、framing、error termination。
   - rules 只提供工程约束，不给出协议专属 module/file/function inventory。

3. `Structured Planning`
   - `planner.py` 通过多阶段 `LLMStructuredPlanner` 生成 architecture 和
     implementation plan。
   - `prompts.py` 负责stage/partition context projection、prompt payload与system/user
     messages构建，以及JSON repair、local correction和amendment prompts。
   - 当前没有 deterministic local planner；module/file/type/function inventory 必须由
     LLM planning stages 产生。
   - 每个 stage 返回结构化 JSON artifact，最后的 `final_plan_assembly` 只整合前序
     artifact，不得引入新 module/file/type/function。

Structured Planning 子步骤：

| Stage | 作用 |
| --- | --- |
| `scope_fact_inventory` | 整理 minimum scope、deferred features、fact inventory、open assumption candidates。 |
| `architecture_boundaries` | 生成 module candidates、职责边界、ownership decisions、coverage matrix。 |
| `module_file_plan` | 将架构降到 module/file layout、header/source ownership、generation order rationale。 |
| `public_artifact_inventory` | 规划 public/private types、constants、callbacks、functions 和 forbidden symbols。 |
| `type_and_access_path_design` | 只按 registry `type_id` 输出 `definition_overlay`；缺失 identity 进入 `ArtifactRequest`。 |
| `function_interface_design` | 生成 owner file、`TRACE_ID`、`FUNCTION_TYPE`、visibility、C signature、参数 ownership。 |
| `function_behavior_design` | 按 module/file 分区生成 `LOGIC/EVENT` 和可支持的 `WIRE_MAPPING`，不生成 test vectors。 |
| `function_call_contract_closure` | 按 caller file 分区输出 typed call edges；name/signature/`RELY.FUNC`/`CALL_CONTRACTS` 确定性派生。 |
| `function_test_vector_design` | 生成 JSON-safe test vectors，byte array 使用十进制整数或字符串。 |
| `dependency_closure` | 只输出不能唯一推导的 ordering/architecture choices；常规 dependency 由 registry relations确定性派生。 |
| `final_plan_assembly` | 零 LLM 的 deterministic reconciliation；按稳定 artifact identity 组装 compiler 消费的 `implementation_plan.json`。 |

4. `Specs Compiler`
   - `compiler.py` 先把 `final_plan_assembly` 的 coder-facing artifact 规范化为 compiler IR，
     再 deterministic lowering 为 coder-facing specs。
   - 规范化包括 header/source 合并、ID/ownership 引用、C signature、type owner dependency
     和各 function 子阶段 artifact 的结构转换。
   - compiler 不新增 plan 中不存在的 module/file/type/function。

5. `Validation`
   - `validation_layers.py` 明确区分 structural、binding、semantic 三层；每个 diagnostic
     只有一个 owner 与 recovery。Structural failure 只做 syntax repair/current-partition
     regeneration，binding failure 只做 local correction/`ArtifactRequest`，semantic failure
     只在完整 candidate plan 上进入 bounded closure。
   - `_planning/validation_layers.json` 保存 diagnostic ID、layer、recovery 与 outcome；
     registry/canonical/pipeline-state/deterministic invariant 等 allowlist 内错误才进入
     `failed_internal`，其余 validator finding 保持可恢复或 candidate-only。
   - `validation.py` 检查 schema shape、reference isolation、anti-hardcoding、
     facts sensitivity、plan-to-spec preservation、fact/decision/assumption separation、
     structural consistency。
   - `pipeline.py` 默认还调用 coder loader 做 read-only validation 和 rendered header
     compile check。

### Implementability gate

在 specs lowering 前，`implementability.py` 会确定性检查 symbol、visibility、foreign type/header、
call/dependency、opaque lifecycle、owned-resource cleanup、cross-module access、callback contract、
module ownership 和唯一 runtime `main`。能够从现有 artifacts 唯一推导的 dependency 会被
deterministic completion；需要新增 lifecycle/helper/access service 或调整 interface 的问题进入
一次 bounded semantic patch，并最多追加一轮针对明确 validation errors、按 diagnostic group
分区的 correction。每个 correction partition 只返回必要 stable-ID delta operations，pipeline
再按 add-then-update 顺序确定性合并。

Semantic patch 只接收直接相关 artifacts、facts、generic constraints 和 decisions，只允许稳定 ID
的 add/update，并要求 reason、provenance 与 affected artifact IDs。closure 仍有 error 时，pipeline
保留最后一个 structurally valid plan 和原 error severity，继续生成 evaluation specs；error 只影响
`qualification_passed`，不再阻止 specs 物化。

`<out>/_planning/semantic_closure/` 保存 initial/final diagnostics、deterministic completion、
input slice、raw patch responses、patch usage、validated applied patch 与 implementability report。

### Candidate 与 qualified publication

每次可恢复运行都会生成 `_planning/candidate_planning_package/`，保存已提交 stage overlays、
unresolved partitions、blocking diagnostics、provenance、manifests、registry snapshot 和 metrics。
运行状态严格区分 `completed_with_qualified_specs`、`completed_with_candidate_only` 与
`failed_internal`。Specs 始终先写入 candidate staging，并对该目录执行 planning validation、coder
loader 与 rendered-header checks；全部 qualification checks 通过时另行复制到 `<protocol>_specs/`。
Candidate-only manifest 的 `specs_root` 指向 candidate specs，供 evaluation 显式加载；它不会被描述
为 qualified specs。`run_manifest.json` 分别记录 `specs_generated`、`planning_validation_passed`、
`coder_loader_passed`、`qualification_passed`、stage/partition survival、semantic/unresolved counts、
token accounting 与 fresh/resume 标记。

### Controlled Inventory Amendment

Stage 5/8 若发现 closed inventory缺失，只能输出 `ArtifactRequest`。Request必须声明 requested kind、
semantic role、owner、required-by artifact、reason、provenance、preferred visibility，可提供
`proposed_name`。系统只会 typed-bind已有 artifact，或注册一个 `status=requested` 的 canonical
identity；不会自动生成 signature、fields、behavior 或 API family。每个 stage/partition最多一个
amendment round，结果与受影响的 Stage 5/6/7/8 local rerun schedule写入
`_planning/inventory_amendments.json`。未完成 rerun或无效 request保持 blocking，只能生成 candidate。

## 运行

```bash
python3 -m agent.planning plan \
  --planner-mode llm \
  --api-key-env ALI_API \
  --facts /home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out /home/ljf/SpecForge/agent/planning/out/mqtt_min_run
```

查看 Structured Planning 子步骤：

```bash
python3 -m agent.planning stages
```

输出结构：

```text
<out>/
├── mqtt_specs/
│   ├── SUMMARY.md
│   ├── mqtt_module_spec.json
│   └── ...
└── _planning/
    ├── candidate_planning_package/
    ├── run_metrics.json
    ├── validation_layers.json
    ├── typed_stage_overlays.json
    ├── normalized_characteristics.json
    ├── activated_engineering_rules.json
    ├── structured_planning_stages.json
    ├── structured_planning_usage.json
    ├── selected_architecture.json
    ├── implementation_plan.json
    ├── stage_logs/
    ├── engineering_decisions.json
    ├── open_assumptions.json
    ├── diagnostics.json
    └── run_manifest.json
```

## 验证

重新验证已有 run：

```bash
python3 -m agent.planning validate \
  --run-dir /home/ljf/SpecForge/agent/planning/out/mqtt_min_run
```

运行 planning tests：

```bash
python3 -m unittest discover -s /home/ljf/SpecForge/agent/planning/tests
```

## 恢复

如果任务中断，先读取统一的进展与效果报告：

```text
/home/ljf/SpecForge/agent/planning/PLANNING_CURRENT_STATUS_REPORT.md
```

再查看最近一次 run 的：

```text
<out>/_planning/run_manifest.json
<out>/_planning/diagnostics.json
<out>/_planning/implementation_plan.json
```

`run_manifest.json` 记录 facts path、facts hash、planner mode、specs root、写出的文件和
diagnostic counts。

每个 LLM stage 都会写入：

```text
<out>/_planning/stage_logs/
├── stage_records.json
├── 01_scope_fact_inventory/
│   ├── prompt.json
│   ├── response.raw.txt
│   ├── artifact.json
│   └── stage_manifest.json
└── ...
```

`stage_manifest.json` 和 `structured_planning_usage.json` 记录每个 stage 的
`prompt_tokens`、`completion_tokens`、`total_tokens` 与耗时。若运行中断，最近 stage 的
manifest 会保留 `started` 或 `failed` 状态。

LLM请求使用compact JSON。Stage 3以后按stage/partition只发送直接依赖artifact、compact registry
catalog及trace refs对应的原始fact/evidence slice，不重复发送完整raw facts和累计stage artifacts。
`run_metrics.json.request_accounting`记录structured/semantic request数量与prompt字符数。

若 stage 返回非法 JSON，planner 会发起一次 JSON repair LLM 调用。repair 只能修复语法，
例如把 `0x48` 改为十进制整数，不允许补充新设计；repair token 会单独记录在
`repair_usage` 与 `structured_planning_usage.json` 中。

Stage 5/7/8 的每个 partition按 `generate -> parse -> bind -> validate -> commit` 执行；commit前
不会修改 canonical overlays。JSON syntax repair与一次 local semantic correction使用独立 prompt、
response和usage日志。Correction仍失败时只 rollback当前 partition，写入
`unresolved_partitions.json`/`blocking_diagnostics.json`，继续其他独立 partition；最终 run只能成为
candidate。Resolved amendment可追加一个新 type partition，或仅重跑新 function对应的 Stage 6
interface、Stage 7 behavior与Stage 8 call partition，不重写已有 artifacts。

使用同一个 `--out`，可从任一 Structured Planning stage 重新执行该 stage 及其后续阶段：

```bash
python3 -m agent.planning plan \
  --planner-mode llm \
  --api-key-env ALI_API \
  --facts /home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out /home/ljf/SpecForge/agent/planning/out/mqtt_min_run \
  --resume-from function_behavior_design
```

如果 11 个 stage 已完成，只需在修改 compiler 后重新规范化、编译和验证 specs，可使用：

```bash
python3 -m agent.planning plan \
  --planner-mode llm \
  --api-key-env ALI_API \
  --facts /home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out /home/ljf/SpecForge/agent/planning/out/mqtt_min_run \
  --resume-from compile_specs
```

stage 续跑会保留目标 stage 之前的 `artifact.json`，删除目标及之后的旧 stage 日志并重新执行。
`compile_specs` 续跑会复用全部 stage artifact，不发起 LLM 调用；若 analyzer 规则更新，它会严格
重验已落盘 semantic candidates，并只复用 patch validation 与 residual closure 均为零的候选。
不传 `--resume-from` 时为全新运行。

Stage 6 是 function identity、owner、visibility 和完整 signature 的 canonical definition；
Stages 7-10 只允许 behavior/call/test/dependency overlays。final reconciliation 按 stable function ID
合并，`CALL_CONTRACTS` signature 从唯一 canonical callee 重建，compiler 不接受 function name
作为 signature fallback。未知 ID、identity 漂移和 incomplete overlay inventory 会在 stage-local
validation 中失败并写入 manifest。

当前稳定性边界：确定性 Run 3 replay 已能生成 coder-loadable 且 rendered-header-valid 的完整
specs，但三次 fresh planning 仍未形成连续成功。已观察到的 LLM 越界包括 Stage 5 新增未登记
type identity，以及 Stage 8 把 type symbol 写成 callee function；这些会被 gate 明确阻断，不能通过
compiler 猜测或放宽 validator 规避。

## 扩展第二种协议

优先增加 facts-driven generic extraction，而不是增加协议模板：

- 在 `knowledge.py` 中补充新的 protocol characteristic 或 generic engineering rule。
- 在 `planner.py` 中调整stage执行契约，在 `prompts.py` 中调整prompt，让
  module/type/function设计由LLM基于characteristic/rule/facts产生。
- 在 `compiler.py` 中只增加 schema dialect lowering，不增加协议专属语义。
- 在 `tests` 中添加 facts sensitivity fixture，确认输出随 transport、interaction、
  statefulness、roles、minimum scope 改变。

不要添加固定协议 inventory、固定对象数量、参考 specs 文本改写或协议专属 fallback。
