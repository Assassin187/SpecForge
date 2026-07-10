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
| `type_and_access_path_design` | 展开 `TYPE_SPEC`、field、public `ACCESS_PATHS`、type dependency notes。 |
| `function_interface_design` | 生成 owner file、`TRACE_ID`、`FUNCTION_TYPE`、visibility、C signature、参数 ownership。 |
| `function_behavior_design` | 按 module/file 分区生成 `LOGIC/EVENT` 和可支持的 `WIRE_MAPPING`，不生成 test vectors。 |
| `function_call_contract_closure` | 生成 `RELY`、`CALL_CONTRACTS` 和 call graph，只引用既有函数。 |
| `function_test_vector_design` | 生成 JSON-safe test vectors，byte array 使用十进制整数或字符串。 |
| `dependency_closure` | 闭合 module/file/function dependency graph、visibility、call graph、generation order。 |
| `final_plan_assembly` | 组装 compiler 消费的完整 `implementation_plan.json`。 |

4. `Specs Compiler`
   - `compiler.py` 先把 `final_plan_assembly` 的 coder-facing artifact 规范化为 compiler IR，
     再 deterministic lowering 为 coder-facing specs。
   - 规范化包括 header/source 合并、ID/ownership 引用、C signature、type owner dependency
     和各 function 子阶段 artifact 的结构转换。
   - compiler 不新增 plan 中不存在的 module/file/type/function。

5. `Validation`
   - `validation.py` 检查 schema shape、reference isolation、anti-hardcoding、
     facts sensitivity、plan-to-spec preservation、fact/decision/assumption separation、
     structural consistency。
   - `pipeline.py` 默认还调用 coder loader 做 read-only validation 和 rendered header
     compile check。

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

如果任务中断，先读取：

```text
/home/ljf/SpecForge/agent/planning/PLANNING_REWRITE_PROGRESS.md
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

若 stage 返回非法 JSON，planner 会发起一次 JSON repair LLM 调用。repair 只能修复语法，
例如把 `0x48` 改为十进制整数，不允许补充新设计；repair token 会单独记录在
`repair_usage` 与 `structured_planning_usage.json` 中。

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
`compile_specs` 续跑会复用全部 stage artifact，不发起 LLM 调用。不传 `--resume-from` 时为全新运行。

## 扩展第二种协议

优先增加 facts-driven generic extraction，而不是增加协议模板：

- 在 `knowledge.py` 中补充新的 protocol characteristic 或 generic engineering rule。
- 在 `planner.py` 中调整 stage contract 或 prompt，让 module/type/function 设计由
  LLM 基于 characteristic/rule/facts 产生。
- 在 `compiler.py` 中只增加 schema dialect lowering，不增加协议专属语义。
- 在 `tests` 中添加 facts sensitivity fixture，确认输出随 transport、interaction、
  statefulness、roles、minimum scope 改变。

不要添加固定协议 inventory、固定对象数量、参考 specs 文本改写或协议专属 fallback。
