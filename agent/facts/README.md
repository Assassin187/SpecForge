# Protocol Facts Extraction Agent

这个目录实现了一个面向协议文档集合的事实抽取 agent。  
它的目标不是直接生成代码，而是先把 RFC 或其他技术文档抽取为一份机器可读的协议语义 JSON，供后续“工程实现规划 agent”“规范定义 agent”“代码生成 agent”继续消费。

当前实现是 `~/SpecForge/agent` 下与 `coder` 并列的第二个 agent，主要负责整个多 agent 流水线中的第一步：**协议事实抽取**。

## 输出契约（facts agent 产物是什么）

facts agent 的主要输出是一个**可被下游工程规划/规范定义消费的 JSON 事实包**，默认落盘到：

```text
~/SpecForge/agent/facts/out/<protocol_name>/
```

其中 `<protocol_name>` 会被安全化（小写、去掉特殊字符）。目录内的关键产物：

- `protocol_facts.json`
  - `schema_version` 固定为 `protocol_facts/v2alpha1`
  - 必须包含下游规划所需的 8 类语义分区：
    - `transport` / `interaction_model` / `message_model` / `state_model`
    - `routing_model` / `resource_model` / `error_and_limits` / `minimum_v1`
  - 每条“具体事实”必须带 `evidence_refs`，并且这些 id 必须能在 `evidence_index` 中找到对应证据条目
  - `planning_inputs.planning_checklist` 必须覆盖 verifier 要求的最小清单（见 verifier 校验项）
- `run_manifest.json`
  - 本次抽取的元信息（输入文档、chunk 数量、模型、token 用量统计、输出路径等）
- `_agent_logs/`
  - 调试产物（chunk、召回/rerank、prompt/response、token 汇总等）

## 当前目标

输入：

- 同一协议的一组技术文档路径
- 当前支持 `.txt`

输出：

- `protocol_facts.json`
- `run_manifest.json`
- `_agent_logs/` 下的预处理、chunk、prompt、response 等调试产物

其中 `protocol_facts.json` 的目标是提供后续工程规划所需的核心语义信息，而不是做完整协议综述。

## 当前流程

当前 `facts` agent 采用“surface discovery + 规则召回 + rerank 重排 + 子任务抽取 + reconciliation + 充分性校验”的混合方案，整体流程如下：

1. 加载文档集合
2. 规范化文本内容
3. 按章节/段落切分为 chunk
4. 做协议表面 `surface discovery`
5. 按子任务做规则召回，生成较大的候选 chunk 集
6. 用 rerank 阶段对候选 chunk 重排
7. 按统一预算组装最终上下文
8. 分类别、分子任务调用 LLM 进行结构化 JSON 抽取
9. 做跨类别 reconciliation
10. 汇总为统一的 `protocol_facts.json`
11. 对输出结果做结构和规划充分性校验

当前固定抽取的语义类别包括：

- `transport`
- `interaction_model`
- `message_model`
- `state_model`
- `routing_model`
- `resource_model`
- `error_and_limits`
- `minimum_v1`

## 目录结构

```text
facts/
├── README.md
├── __init__.py
├── __main__.py
├── cli.py
├── models.py
├── document_loader.py
├── preprocess.py
├── prompts.py
├── extraction.py
└── verifier.py
```

各文件职责如下：

- `cli.py`
  - 命令行入口
  - 提供 `validate`、`extract`、`verify`
- `models.py`
  - 定义事实抽取流程内部用到的数据结构
- `document_loader.py`
  - 加载文档并做基础文本归一化
- `preprocess.py`
  - 文档切片、chunk 构造、规则召回
- `prompts.py`
  - rerank prompt 与分类抽取 prompt 模板
- `extraction.py`
  - rerank、上下文装配、抽取主流程与输出文件落盘
- `verifier.py`
  - 对 `protocol_facts.json` 做结构完整性检查

## 使用方式

在 `~/SpecForge` 下运行：

```bash
python3 -m agent facts validate --protocol-name coap --doc ~/SpecForge/document/rfc7252.txt --skip-llm-check
python3 -m agent facts extract --protocol-name coap --doc ~/SpecForge/document/rfc7252.txt
python3 -m agent facts verify --output-dir ~/SpecForge/agent/facts/out/coap
```

也可以直接使用子包入口：

```bash
python3 -m agent.facts validate --protocol-name ftp --doc ~/SpecForge/document/rfc959.txt --skip-llm-check
```

### 命令说明

- `validate`
  - 校验文档路径、文件类型、预处理是否正常
  - 可通过 `--skip-llm-check` 跳过模型环境检查
- `extract`
  - 执行完整事实抽取流程
  - 默认输出到 `~/SpecForge/agent/facts/out/<protocol_name>`
- `verify`
  - 检查 `protocol_facts.json` 是否满足后续工程规划所需的最小结构

## 输出说明

### `protocol_facts.json`

当前顶层字段包括：

- `schema_version`
- `protocol_meta`
- `transport`
- `interaction_model`
- `message_model`
- `state_model`
- `routing_model`
- `resource_model`
- `error_and_limits`
- `minimum_v1`
- `planning_inputs`
- `open_questions`
- `evidence_index`

当前 schema 版本为 `protocol_facts/v2alpha1`。

其中：

- `planning_inputs` 用来直接支撑后续工程规划 agent
- `evidence_index` 用来保留事实和原始文档之间的证据关联
- 各 category 内部的事实项会直接携带 `evidence_refs`
- `open_questions` 使用对象结构承载“文档未抽到 / 外部依赖 / 规范未定义 / 实现策略问题”等不确定项

### `run_manifest.json`

记录本次抽取的基本元数据，例如：

- 协议名
- 输入文档集合
- chunk 数量
- 抽取类别
- 使用的模型
- 输出文件路径
- 每次 LLM 调用、每个类别、整轮工作流的 token 用量统计
- shared stages（如 `surface_discovery`、`cross_category_reconciliation`）的 token 用量统计

### `_agent_logs/`

用于调试和追踪抽取过程，当前通常会包含：

- 归一化后的文档摘要
- chunk 索引
- 各类别规则召回结果
- 各类别 rerank prompt / response
- 各类别最终选中的上下文
- 各子任务 prompt
- 各子任务 response
- `surface_discovery` 和 `cross_category_reconciliation` 的 prompt / response
- token 用量汇总

## 和 `coder` 的关系

`facts` 和 `coder` 是并列子系统：

- `facts`
  - 文档 -> 协议语义事实 JSON
- `coder`
  - 结构化 spec -> 代码工程

二者当前共用 `~/SpecForge/agent/common/llm_client.py` 中的固定模型调用能力。
其中 `facts` 额外复用了 `~/SpecForge/agent/common/rerank_client.py` 作为 rerank 阶段的统一入口。

## 当前限制

- 当前只支持 `.txt`
- 不支持 PDF、扫描件和 OCR
- 当前事实抽取强依赖 LLM；在网络受限环境中，`extract` 会失败，但仍会尽量写出 manifest 和中间产物
- 当前 `protocol_facts.json` 已升级到 v2，结构比早期版本更复杂，仍需要继续打磨抽取质量
- 当前 `verify` 已经包含一部分“充分性”校验，但仍不是语义正确性的完整判定器

## 下一步可继续优化的方向

- 为 `protocol_facts.json` 增加正式 schema
- 增强无模型时的 heuristic-only 降级抽取模式
- 继续优化 rerank 质量，减少弱相关段落进入最终上下文
- 增加更强的证据质量检查，避免 fallback evidence 过于粗糙
