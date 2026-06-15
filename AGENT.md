# AGENT.md：SpecForge Planning 稳定化修复会话背景

## 1. 本次修复的核心动机

SpecForge 当前已经具备从 `protocol_facts.json` 和 `target_profile.json` 生成 `spec_bundle/` 的能力，但还没有达到中期目标：

```text
planning 输出能够稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

前期 specs 结构优化诊断已经形成明确结论：当前不应整体压缩 coder-facing strict specs。更强证据表明，主要问题不是 specs 过细，而是 planning 内部 IR 到 coder-facing specs 的 deterministic lowering、dependency closure、public/system type closure 和 final readiness validation 不闭合。

因此，本轮修复的主要目标不是重写 schema，也不是删减 specs 字段，而是让 planning 的成功状态真正意味着：

```text
spec_bundle schema valid
+ coder loader valid
+ rendered headers valid
+ public type / system type dependency closed
+ source/header dependency 不混用
+ call/dependency graph 不假通过
+ coder 可以进入 compile/smoke 阶段
```

## 2. SpecForge 项目定位

SpecForge 是一个三阶段 protocol implementation pipeline：

```text
Technical Documents
  -> Facts Agent
  -> protocol_facts.json
  -> Planning Agent
  -> spec_bundle/ + coder_manifest.json
  -> Coder Agent
  -> generated C protocol implementation
```

三个 agent 的职责边界：

1. `facts agent`
   - 输入 technical documents。
   - 输出 evidence-backed protocol facts。
   - 不做 implementation planning。
   - 不生成代码。

2. `planning agent`
   - 输入 `protocol_facts.json` 和 `target_profile.json`。
   - 输出 implementation-oriented protocol specs、implementation plan、dependency graph、coder-compatible `spec_bundle/`。
   - 是 SpecForge 的核心研究贡献。
   - 不是 summarizer，也不是 coder。
   - 必须区分 protocol facts、inferred engineering decisions 和 open assumptions。

3. `coder agent`
   - 输入 planning 生成的 `spec_bundle/`。
   - 生成 C protocol implementation。
   - 通过 compile/repair/smoke 验证最终代码。

本次修复聚焦 `agent/planning/`，必要时可以修改 coder loader/header validation 来增强 planning readiness gate，但不要把 coder 改成绕过 planning 错误的容错器。

## 3. 本次修复的中期目标

中期目标分为两个层次：

### 3.1 单协议稳定目标

对 MQTT minimum profile，planning 生成的 specs 应稳定通过：

```text
planning final validation
-> coder schema validation
-> coder loader validation
-> rendered header validation
-> coder compile
-> minimum smoke tests
```

验收口径：

- 不允许 planning final report 显示 success，但 coder 阶段才暴露 deterministic header/type/include failure。
- 不允许 dependency fallback 清空 `calls_allowed` / `imports_allowed` 后制造 empty graph success。
- 不允许 `HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY` 在 public ABI 需要外部类型或系统类型时为空。
- 对失败 run，必须有明确 blocking diagnostic，能够定位到 stage、field、symbol、type、file 或 dependency edge。

### 3.2 多协议复现目标

在 MQTT、CoAP、SMTP 的最小功能集上复现：

```text
protocol facts / gold facts
-> planning
-> spec_bundle
-> coder
-> compile
-> smoke
```

验收口径：

- 每个协议至少有一个 fresh planning output 可以驱动 coder compile。
- 每个协议至少通过对应 minimum smoke。
- 每个协议的 planning/coder artifacts、validation reports、compile logs、smoke logs 都可以归档复查。
- 失败原因必须被分类，不允许 silent fallback 或 false success。

## 4. 当前已知诊断结论

当前不应优先做 broad schema compression。原因：

1. `Gold-Full` specs 可以驱动 coder compile/smoke。
2. `Gold-Min-Interface` 在 MQTT/CoAP 上明显退化。
3. behavior detail、test vectors、calls/rely 对 source generation、repair convergence 或 smoke behavior 有价值。
4. 可低风险下沉的是 traceability 类字段，例如 module/file `DOC_REF`。
5. function-level `WIRE_MAPPING` 可以作为后续 sidecar/optional 候选，但不能删除整体 wire/access 语义，因为 `ACCESS_PATHS` 仍有价值。

当前主要 blocker：

1. `5.6_dependency_closure`
2. `specs_compiler`
3. `5.7_spec_readiness`
4. `coder_compat validator`
5. `5.5a_file_layout`
6. `5.3_type_data`
7. `5.4b_function_signatures`
8. `5.4a-5.4e` semantic actionability

## 5. LLM 与 deterministic code 的权限边界

本项目必须保持以下边界：

1. LLM 可以生成 local candidate 或 patch。
2. LLM 不可以直接生成 final dependency graph。
3. LLM 不可以新增 protocol facts。
4. LLM 不可以绕过 schema、validator、reference integrity 或 coder compatibility。
5. dependency graph 必须由 deterministic code 从 structured planning signals 派生。
6. specs compiler 不应调用 LLM。
7. planning-only 字段不能直接塞入 coder strict specs 顶层。
8. unknown、ambiguous、conflicting information 应进入 assumptions 或 unresolved questions，不能伪造成 protocol behavior。

## 6. 本次修复的非目标

本次不要做：

1. 不要重写 planning/coder 全流程。
2. 不要整体压缩 strict specs schema。
3. 不要关闭 coder compatibility validation。
4. 不要通过删除 dependency、清空 calls/imports 让 validation 通过。
5. 不要让 LLM 直接生成最终 dependency graph。
6. 不要把 example specs / gold specs 中的 code-derived implementation detail 当成 protocol facts。
7. 不要强制 planning 复制 gold 的 module/file/function 名称。
8. 不要为了让 MQTT 单协议通过而引入无法泛化到 CoAP/SMTP 的硬编码。

## 7. 核心修复原则

### 7.1 Fail closed

如果 dependency、type、signature、header 或 source dependency 无法闭合，应该生成 blocking diagnostic，而不是清空字段后继续。

### 7.2 Closure first

优先修复 deterministic closure：

```text
public type closure
system type closure
header dependency closure
source dependency closure
call graph closure
rendered header compile readiness
```

在这些 closure 未稳定前，不应优先优化 LLM prompt 或 semantic density。

### 7.3 Coder compatibility is hard gate

planning success 必须包含 coder-compatible readiness。至少应覆盖：

```text
schema validation
loader validation
rendered header validation
dummy header translation unit compile
public symbol consistency
dependency resolvability
```

### 7.4 Rich internal IR，controlled lowering

planning internal IR 可以保持 rich，但最终 strict specs 只能包含 coder schema 接受并可消费的字段。traceability、decision rationale、validation reports 应留在 sidecar 或 validator-only artifacts。

### 7.5 Protocol facts / engineering decisions / assumptions 分离

修复时必须明确每个新增规则属于哪一类：

- protocol facts：来自 technical documents / protocol_facts。
- inferred engineering decisions：由 target profile 和工程约束推导。
- open assumptions：事实不足或设计选择未完全确定时显式记录。

## 8. Codex 工作方式要求

每次会话应遵循：

1. 先阅读本文件、总体任务描述文件、当前任务状态文件。
2. 明确本轮只处理一个任务步骤或一个紧密相关的子步骤。
3. 修改前先定位相关代码路径、现有 validator、现有 tests/logs。
4. 不进行大范围重写，除非任务文件明确要求。
5. 每次修改后必须运行可负担的确定性检查。
6. 每次结束必须更新当前任务状态文件。
7. 若无法完成当前步骤，应记录：
   - 已完成内容
   - 未完成内容
   - 失败原因
   - 下一轮应从哪里继续
   - 新增风险或 open assumptions

## 9. 推荐的证据输出

每轮修复后尽量产出以下证据：

```text
修改文件列表
新增/更新 validator 列表
新增/更新测试列表
运行命令
validation result
planning run path
coder run path
compile log path
smoke log path
failure diagnostics summary
```

这些证据后续会用于论文 evaluation 和 ablation 设计，因此不要只给口头结论。
