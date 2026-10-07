# SpecForge

将应用层协议的任务、需求和原始规范生成为 Linux C99 项目。`www` 的维护基线为
本次整理后的版本，核心生成机制冻结于
`b8a9130d23cd57c975f2f435bb1b1c2304cfee15`；框架、提示词、Schema、模型配置、
四协议输入和验收器保持该稳定提交的原始内容。版本证据及历史限制见 [BASELINE.md](BASELINE.md)。

核心流程：

```text
冻结 TASK / REQUIREMENTS / 原始规范
→ Facts：提取范围、规范事实和原文依据
→ Design：模块、文件和公共头文件
→ Specs：函数行为、向量、调用及资源契约
→ 独立语义审查与规格发布
→ Code：源码、Makefile、开发测试和交付清单
→ 普通构建、自测、ASan/UBSan 自测和交付重建
→ 单独执行隔离的协议行为验收
```

生成器不按协议选择提示词，不提供预制实现。Coder 只读取发布的规格和开发报告；
独立验收不参与生成修复。成功生成表示开发关卡通过，不等于全部独立行为验收通过。

## 目录

| 目录或文件 | 用途 |
|---|---|
| `specforge/` | CLI、模型调用、阶段调度、工具、规格处理及独立评测 |
| `prompts/` | 基线 Facts、Planner 和 Coder 提示词 |
| `schemas/` | 事实、规格、溯源和交付数据约束 |
| `cases/` | MQTT、CoAP、HTTP/1.1、SMTP 的 TASK、REQUIREMENTS 和原始规范 |
| `evaluation/` | 四协议独立行为验收器 |
| `tests/` | 基线框架测试 |
| `assets/mqtt_reference/bundle/` | 框架测试必需的规格夹具；fresh 生成不使用它 |
| `runs/cost_optimization/20261002T125031Z_json_fields/` | 历史 R3 前序实验，版本与最终基线有差异 |
| `runs/stability/20261002T160137Z_two_rounds/` | 最终基线的两轮冻结实验 |
| `runs/validation/` | 本次清理后的一轮四协议验证及整理审计 |
| `pyproject.toml` | Python 版本、固定依赖和 CLI 入口 |

历史记录保留原目录名和原始内容。不得将前序 R3 误记为最终基线实验；不得跨版本
恢复旧生成任务。此前 Token 优化和其他历史资产已备份移出当前工作区。

## 环境

需要 Python 3.11+、GCC、Make、bubblewrap，以及处理 PDF 的 pdftotext。
Python 依赖固定为 `openai==1.97.0` 和 `jsonschema==4.24.0`。

```sh
python -m pip install -e .
export DS_API='你的模型密钥'
python -m unittest discover -s tests -v
```

密钥通过环境变量提供，不写入实验记录，不挂载到 Agent 命令视图。
模型配置为 `deepseek-flash`、`reasoning_effort=high`、thinking enabled、非流式，
`max_tokens=65536`。总 Token 为输入加输出；推理已包含在输出，缓存命中已包含在输入。

MQTT 的互通验收还需要 `mosquitto_pub` / `mosquitto_sub`，CoAP 需要
`coap-client-notls`。这些工具不是基础生成入口的必要依赖；未执行或跳过的验收不能记为通过。

## 四协议生成和验收

每次使用新的输出目录。以下命令均执行原有完整生成流程，不复用历史中间产物。

```sh
python -m specforge run \
  --task cases/mqtt_min/TASK.md \
  --requirements cases/mqtt_min/REQUIREMENTS.md \
  --protocol cases/mqtt_min/spec/mqtt-v3.1.1-os.pdf \
  --out runs/my_mqtt
python -m specforge evaluate --run runs/my_mqtt --evaluator evaluation/mqtt_check.py

python -m specforge run \
  --task cases/coap_min/TASK.md \
  --requirements cases/coap_min/REQUIREMENTS.md \
  --protocol cases/coap_min/spec/rfc7252.txt \
  --out runs/my_coap
python -m specforge evaluate --run runs/my_coap --evaluator evaluation/coap_check.py

python -m specforge run \
  --task cases/http11_min/TASK.md \
  --requirements cases/http11_min/REQUIREMENTS.md \
  --protocol cases/http11_min/spec/rfc9110-rfc9112.txt \
  --out runs/my_http11
python -m specforge evaluate --run runs/my_http11 --evaluator evaluation/http11_check.py

python -m specforge run \
  --task cases/smtp_min/TASK.md \
  --requirements cases/smtp_min/REQUIREMENTS.md \
  --protocol cases/smtp_min/spec/rfc5321.txt \
  --out runs/my_smtp
python -m specforge evaluate --run runs/my_smtp --evaluator evaluation/smtp_check.py
```

规范范围由各案例 REQUIREMENTS 定义，不宣称完整协议合规。协议文本支持 UTF-8 txt
和 PDF；HTTP 单份输入保留 RFC 9110 和 RFC 9112 的完整原文及来源边界。

## 其他基线入口

```sh
python -m specforge run ... --until prepare
python -m specforge resume --run runs/my_mqtt
python -m specforge validate --specs runs/my_mqtt/specs/r001
python -m specforge verify --project runs/my_mqtt/project
python -m specforge code --specs assets/mqtt_reference/bundle --out runs/spec_only
```

`--until` 也支持 facts、design、specs、code、verify。失败退出非零；resume 要求
同版本及未修改的阶段产物。独立评测开始后项目冻结，不能再恢复生成。Spec-only 入口
保留为既有功能，人工夹具实验不能计为 fresh 端到端生成。

## 稳定机制和记录

三层规格、GCC ABI 探针、只读公共头文件、溯源和 delivery.json 一致性关卡保持原样。
模型工具调用按原生顺序执行，截断回答中的调用不执行。每 24 次响应或输入超过
64,000 Token，原控制器要求保存 WORKLOG 并重建上下文；最后 12 次响应保留修复窗口。
Facts / Design / Specs / Code 的响应上限为 40 / 100 / 180 / 180；独立规格审查
上限 60，每个实现修复 job 上限 40。最多三次实现修复、一次规格回修。

生成后 `evaluate` 对交付副本分别执行普通和 sanitizer 构建与协议验收，原项目不被改动。
MQTT / CoAP / HTTP / SMTP 的独立套件分别有 16 / 10 / 13 / 13 个必需场景。

运行目录包含 inputs、documents、facts、design_work、specs、project、snapshots、reports、
logs 和 run.json。原始用量、每阶段状态、修复次数、输入及框架哈希、构建和行为结果分别记录。
整理后的验证结果与备份说明见 [BASELINE.md](BASELINE.md)。
