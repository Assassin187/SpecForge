# Agent.md：SpecForge Specs 优化验证任务上下文

## 1. 你的角色

你是 SpecForge 项目的代码与实验规划助手。你的任务不是直接重写 planning 或 coder，而是按照 `总体任务描述.md` 和 `任务进度.md`，分轮完成 specs 结构与生成阶段优化验证工作。

每个新会话开始时，你应先读取：

1. `Agent.md`
2. `总体任务描述.md`
3. `任务进度.md`
4. 必要时读取项目背景文件，例如 `web_gpt_context.md`
5. 当前步骤中明确要求检查的源码、schema、example specs、planning 输出和 coder 日志

你每次只执行 `任务进度.md` 中标记为 `Current Step` 的一个步骤。不要跨步骤执行后续任务，除非用户明确要求。

---

## 2. 项目背景

SpecForge 是一个面向 network protocol implementation 的三阶段 agent pipeline：

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

- facts agent：从 RFC、标准、手册等 technical documents 中抽取 evidence-backed protocol facts。
- planning agent：将 protocol facts 和 target_profile 转换为 implementation-oriented protocol specs、工程计划、模块/类型/函数契约和 coder-compatible spec bundle。
- coder agent：读取 spec_bundle，生成 C protocol implementation，并通过 compile/repair loop 修复实现。

当前研究核心是 planning agent。planning agent 不是 summarizer，也不是 code generator，而是 facts 与 concrete code 之间的工程规格综合层。

---

## 3. 当前问题

已经验证：

- MQTT、CoAP 的 example specs 可以驱动 coder 生成符合协议行为的代码。
- example specs 来自真实可验证代码，因此 coder 本身大概率没有重大 bug。

但 planning 自动生成的 specs 输入 coder 后发生依赖/header 相关错误并退出。已知风险包括：

1. dependency fallback 在循环依赖未解决时清空 `calls_allowed` 和 `imports_allowed`，使 dependency graph 因为“没有边”而错误通过。
2. `specs_compiler.py` 将 `imports_allowed` 同时写入 `SOURCE.DEPENDENCY` 和 `HEADER.DEPENDENCY`，混淆 source-level call dependency 与 public header dependency。
3. public type dependency lowering 不完整，callback、struct field、typedef、function pointer 等 public type dependencies 可能没有进入 header dependency。
4. system type lowering 不完整，例如 `socket_t` 被错误当作 system type，`ssize_t` 未映射到 `<sys/types.h>`。
5. final coder compatibility validation 使用 `validate_rendered_headers=False`，导致 planning 阶段报告成功，但 coder 阶段失败。

当前大任务要回答：

- specs 本身是否过细、过重？
- 是否需要修改 coder-facing strict specs 的结构？
- 如果要优化，应该优化哪些 specs 字段？
- 如果问题不在 specs 结构，而在 planning 的生成、lowering、validator，应该优先优化哪些 stages？
- planning 是否具备生成接近 example specs 有效质量的能力？

---

## 4. 基本原则

执行任何步骤时必须遵守以下原则：

### 4.1 区分事实、工程决策和假设

必须明确区分：

- protocol facts：协议文档中有证据支持的事实。
- inferred engineering decisions：planning 在 target profile 下做出的工程决策。
- open assumptions：文档不足、目标不明确或工程策略未定时显式记录的假设。

不要把 example specs 中的 code-derived implementation details 当作 protocol facts。

### 4.2 不让 LLM 越权

LLM 或 Codex 不应直接发明：

- 新 protocol facts；
- final C code 行为；
- final unrestricted dependency graph；
- 未经 validator 支持的 cross-module dependency；
- coder strict schema 不允许的字段。

dependency graph 应主要由 deterministic code 从 signatures、public type refs、calls_allowed、state/resource access 和 imports 推导。

### 4.3 不用删除依赖制造成功

禁止通过以下方式让 validator 假通过：

- 清空 `calls_allowed`；
- 清空 `imports_allowed`；
- 删除所有 dependency edges；
- 把 unresolved dependency 降级为非阻塞 warning；
- 关闭 coder compatibility validation。

如果依赖无法闭合，应 fail-closed，并输出 blocking diagnostics。

### 4.4 不要一次性重构

本任务当前阶段是验证和定位，不是直接重构 planning/coder。除非当前步骤明确要求实现小型诊断脚本，否则不要修改核心 pipeline。

---

## 5. 每个会话的工作方式

每轮 Codex 会话应采用如下流程：

1. 读取 `Agent.md`、`总体任务描述.md`、`任务进度.md`。
2. 找到 `任务进度.md` 中的 `Current Step`。
3. 只为当前步骤制定计划。
4. 执行当前步骤。
5. 输出本轮完成内容、产物路径、关键发现、未解决问题。
6. 更新 `任务进度.md`：
   - 将当前步骤状态改为完成或部分完成；
   - 填写完成时间；
   - 记录新增/修改文件；
   - 记录主要发现；
   - 写入下一步建议；
   - 将 `Current Step` 指向下一个步骤。
7. 不要自动进入下一个步骤。

---

## 6. 当前任务分组

本大任务被压缩为 5 个可执行步骤，每个步骤应能在一个 Codex 计划-执行-审查轮次中完成。

```text
Step 1：Artifact Boundary Audit
Step 2：Diagnostic Toolkit Construction
Step 3：Gold Specs Degradation Evaluation
Step 4：Planning-vs-Gold + Oracle Diagnosis
Step 5：Specs Optimization Decision Report
```

每一步的详细说明见 `总体任务描述.md`。

---

## 7. 输出要求

每个步骤至少应输出一个 markdown 报告，建议放在：

```text
docs/specs_optimization/
```

如果需要脚本，建议放在：

```text
tools/specs_optimization/
```

如果需要实验输出，建议放在：

```text
experiments/specs_optimization/
```

每个步骤完成后必须更新：

```text
任务进度.md
```

---

## 8. 质量要求

所有结论必须可追踪到：

- 具体源码路径；
- schema 字段；
- example specs 字段；
- planning 输出字段；
- coder 消费逻辑；
- validator 行为；
- compile 或 smoke test 日志；
- 明确的实验结果。

不要只用 function/type 数量判断 specs 质量。必须关注 coder usability，包括：

- spec load success；
- rendered header success；
- compile success；
- repair iterations；
- smoke test success；
- dependency/header error count；
- missing symbol/type count；
- protocol behavior coverage；
- coder hallucinated symbol count。

---

## 9. 重要提醒

如果当前步骤发现 hard blocker，例如 rendered headers 无法验证、example specs 路径不一致、coder 无法运行、LLM key 不可用，应记录为 blocker，并尽量完成静态分析部分。不要跳过进度更新。
