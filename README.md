# SpecForge

SpecForge 将协议任务、编号需求和原始规范转换为 Linux C99 项目。系统通过
事实提取、结构设计、行为规格和语义审查组织代码生成，交付源码、公共头文件、
Makefile、开发测试和交付清单。

规范输入支持 UTF-8 文本和可提取文本的 PDF。项目内提供 MQTT、CoAP、HTTP/1.1
和 SMTP 案例，以及对应的独立协议行为验收器。

## 工作流程

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

三层规格描述模块职责、文件接口和函数行为，并传递调用、状态、报文及资源约束。
编码阶段读取已发布的规格，公共头文件保持只读；开发检查发现的问题可触发有界的
实现修复或规格回修。

独立验收在交付副本中执行普通与 ASan/UBSan 构建及协议测试，其结果不参与生成修复。
生成成功表示开发关卡通过，协议行为是否通过由独立验收判断。

## 环境与安装

需要 Linux、Python 3.11+、GCC、Make、bubblewrap 和 pdftotext。
Python 依赖由安装命令自动安装，版本固定为 `openai==1.97.0` 和
`jsonschema==4.24.0`。

在项目根目录执行：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

MQTT 互通验收还需要 `mosquitto_pub` 和 `mosquitto_sub`，CoAP 互通验收需要
`coap-client-notls`。

## 模型配置

通过环境变量提供所选模型的密钥：

| 模型 | 密钥环境变量 | 推理强度 |
|---|---|---|
| `deepseek-flash`（默认） | `DS_API` | `high` |
| `qwen3.8-flash` | `ALI_API` | `high` |
| `gpt-6.1-sol` | `DES_CODEX_API` | `high` |
| `gpt-6-luna` | `DES_CODEX_API` | `max` |

`run` 和 `code` 支持通过 `--model` 选择模型，`resume` 沿用任务原有配置。
接口地址及请求参数由 [specforge/llm.py](specforge/llm.py) 中的 `MODEL_CONFIGS`
定义；GPT 配置使用内网服务，需要能够访问对应地址。

## 快速开始

以下以 MQTT 为例。设置默认模型的密钥，并将 `--out` 指向一个尚不存在的输出目录：

```sh
export DS_API='你的模型密钥'

python -m specforge run \
  --task cases/mqtt_min/TASK.md \
  --requirements cases/mqtt_min/REQUIREMENTS.md \
  --protocol cases/mqtt_min/spec/mqtt-v3.1.1-os.pdf \
  --out /tmp/specforge-mqtt
```

生成完成后，单独执行协议行为验收：

```sh
python -m specforge evaluate \
  --run /tmp/specforge-mqtt \
  --evaluator evaluation/mqtt_check.py
```

如需使用 Qwen，设置 `ALI_API`，并在生成命令后添加 `--model qwen3.8-flash`。

### 协议案例

| 协议 | 案例目录 | 规范文件（位于案例的 `spec/` 下） | 独立验收器 |
|---|---|---|---|
| MQTT 3.1.1 | `cases/mqtt_min/` | `mqtt-v3.1.1-os.pdf` | `evaluation/mqtt_check.py` |
| CoAP | `cases/coap_min/` | `rfc7252.txt` | `evaluation/coap_check.py` |
| HTTP/1.1 | `cases/http11_min/` | `rfc9110-rfc9112.txt` | `evaluation/http11_check.py` |
| SMTP | `cases/smtp_min/` | `rfc5321.txt` | `evaluation/smtp_check.py` |

使用其他案例时，替换生成和验收命令中的 TASK、REQUIREMENTS、规范文件和验收器。
各案例的功能范围与排除项由对应的 REQUIREMENTS 定义，不代表完整协议合规。

## 其他命令

| 命令 | 用途 |
|---|---|
| `run` | 从任务、需求和规范开始完整生成 |
| `resume` | 恢复尚未进入独立验收的任务 |
| `validate` | 校验规格包的结构、引用和接口一致性 |
| `code` | 从已有规格包生成项目 |
| `verify` | 对已有项目执行交付检查、构建和开发自测 |
| `evaluate` | 对完成生成的项目执行独立协议验收 |

`run --until` 可选择 `prepare`、`facts`、`design`、`specs`、`code` 或 `verify`，
默认执行至 `verify`。恢复任务要求框架和已完成的阶段产物保持一致；进入独立验收后
不能继续恢复生成。

查看完整参数：

```sh
python -m specforge --help
python -m specforge run --help
```

## 项目结构

| 目录或文件 | 用途 |
|---|---|
| `specforge/` | 命令行入口、模型调用、阶段调度、规格处理及独立验收 |
| `prompts/` | Facts、Planner 和 Coder 提示词 |
| `schemas/` | 事实、规格、溯源和交付数据约束 |
| `cases/` | 协议案例的任务、需求和原始规范 |
| `evaluation/` | 独立协议行为验收器 |
| `tests/` | 框架测试 |
| `assets/mqtt_reference/bundle/` | 框架测试使用的规格夹具 |
| `pyproject.toml` | Python 版本、依赖及命令行入口配置 |

## 测试

```sh
python -m unittest discover -s tests -v
```
