# SpecForge

SpecForge 是一个面向网络协议实现的 agent pipeline：

```text
technical documents -> facts agent -> protocol facts
-> planning agent -> protocol specs -> coder agent -> protocol implementation
```

本项目的核心研究对象是 planning agent。已有工作分别研究了从 technical documents 抽取 protocol facts，以及从 engineering specifications 生成代码；SpecForge 关注两者之间缺失的中间层，将事实性协议知识转化为可执行的工程结构。

planning agent 分析消息格式、状态机、transport、错误处理、模块边界、API 和依赖关系，生成受 schema 约束的 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC`，而不是只输出自然语言摘要。`TRACE_ID`、`DOC_REF` 和 `TRACE_REFS` 用于保留工程决策的来源。

## 仓库结构

- `agent/facts/`：从 `.txt` technical documents 抽取 `protocol_facts.json`。
- `agent/planning/`：将 protocol facts 转换为 implementation-oriented protocol specs。
- `agent/coder/`：从 protocol specs 生成 C 项目，并执行编译、有限修复和行为检查。
- `specs-example/`：参考 specs 与 JSON Schema。
- `protocol-example/`：可独立构建的参考协议实现。
- `evaluation/`：planning utility 与 spec form ablation 实验。
- `tools/specs_optimization/`：protocol specs 诊断工具。

## 环境要求

主流程需要 Linux、Python 3、`gcc` 和 `make`。模型调用使用 OpenAI-compatible API：

```bash
python3 -m pip install openai transformers
export ALI_API=<your-api-key>
cd /path/to/SpecForge
```

默认模型和 endpoint 定义在 `agent/common/llm_client.py`。

## 快速开始

以下命令运行 MQTT minimum profile：

```bash
python3 -m agent facts extract \
  --protocol-name mqtt \
  --doc document/MQTT_3.1.1.txt \
  --target-profile agent/facts/target_profiles/mqtt_min.json \
  --output-dir agent/facts/out/mqtt_min

python3 -m agent planning plan \
  --facts agent/facts/out/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min

python3 -m agent coder \
  --spec-root agent/planning/out/mqtt_min/mqtt_specs \
  --output-dir agent/out/mqtt_min \
  generate
```

也可直接使用参考 specs：

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  generate
```

某些 planning run 只会产生 candidate specs。调用 coder 前，应检查 `<out>/_planning/run_manifest.json` 中的 `specs_root` 与 qualification 字段。

## 测试

```bash
python3 -m unittest discover -s agent/facts/tests -v
python3 -m unittest discover -s agent/planning/tests -v
python3 -m unittest discover -s agent/coder/tests -v
```

仓库中的实现与行为测试面向 minimum profile，不代表完整 RFC conformance。
