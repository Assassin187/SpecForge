# Multi-Agent Protocol Tooling

这个目录现在包含两类 agent：

- `coder`
  - 面向当前 `~/SpecForge/specs-example/mqtt_specs` 的 spec-to-code 代码生成 agent
- `facts`
  - 面向协议文档集合的事实抽取 agent
  - 目标是把 RFC/技术文档抽取为机器可读 JSON，供后续工程规划 agent 使用

两个 agent 共用同一套固定模型调用能力，模型仍然固定为 `qwen3-max-2026-01-23`。

下文原有内容主要描述 `coder` 的当前实现状态。

## 当前进度

目前已经完成的内容：

- 已经实现 agent 的 Python 包结构，支持直接通过 `python3 -m agent ...` 运行
- 已经实现固定模型接入，`agent` 项目内部自带 `chat_with_llm` 实现
- 已经固定模型名称为 `qwen3-max-2026-01-23`
- 已经实现 `validate`、`generate`、`verify` 三个命令
- 已经实现对 `mqtt_module_spec.json`、文件级 spec、函数级 spec 的统一解析
- 已经实现基础 spec 诊断，包括：
  - 缺字段
  - 重复 `TRACE_ID`
  - 模块依赖顺序错误
  - 文件接口和函数接口不一致
  - 缺失函数级 spec 的告警
- 已经实现生成流水线：
  - 先生成头文件
  - 再调用 LLM 生成源文件
  - 自动生成 `main.c`
  - 自动生成 broker-only `Makefile`
  - 记录 prompt / 编译日志 / manifest
- 已经实现验证流水线：
  - 结构检查
  - 编译检查
  - MQTT smoke test 脚本
- 已经真实调用过 Qwen 模型，不只是本地 mock

目前已经验证通过的内容：

- `python3 -m agent validate` 可以正常运行
- `ALI_API` 环境变量检测正常
- agent 内置的 Qwen 调用函数可正常初始化
- 最小真实 Qwen 请求已经验证通过
- Qwen 模型请求链路已经打通
- 当前 prompt/编译日志会正确写入输出目录

目前还没有完全达到的目标：

- 当前 `generate` 虽然已经能生成完整工程骨架和大部分源码，但生成出的 broker 代码还不能稳定通过编译
- 主要失败点集中在“跨文件共享的公共数据结构约束不够强”，例如：
  - `mqtt_packet_t` 的字段命名
  - `mqtt_decoder` / `broker` / `router` 对公共类型的理解不一致
  - 某些函数签名虽然在头文件里正确，但模型仍可能在源文件里按自己理解调用
- 也就是说：**agent 本身已经实现出来并且可运行，但生成质量还需要继续收敛，尤其是 protocol/broker 交界处**

## 已执行过的验证

已经实际跑过以下命令：

```bash
python3 -m agent validate
python3 -m agent generate
python3 -m agent verify
```

验证结论：

- `validate`：通过
- `generate`：已真实调用模型并产出代码
- `verify`：当前失败，失败原因来自生成代码本身尚未编译通过，而不是验证框架失效

当前生成输出默认落在：

```text
~/SpecForge/agent/out/mqtt_broker_<timestamp>
```

生成目录里目前已经能看到：

- 生成出的 MQTT 工程源码
- `run_manifest.json`
- `_agent_logs/` 下的 prompt、编译日志和 manifest

## 目录中文件说明

### [`__init__.py`](./__init__.py)

包入口的最小元信息文件。

作用：

- 声明 `agent` 是一个 Python 包
- 暴露当前版本号 `0.1.0`

### [`__main__.py`](./__main__.py)

模块直接运行入口。

作用：

- 支持通过 `python3 -m agent` 方式启动
- 将执行流程转发到 `cli.py` 中的 `main()`

### [`cli.py`](./cli.py)

命令行入口与总调度器。

作用：

- 解析命令行参数
- 提供三个子命令：
  - `validate`
  - `generate`
  - `verify`
- 组织各模块之间的调用关系
- 打印诊断信息与执行结果

当前职责边界：

- 不直接做 spec 解析细节
- 不直接做代码生成细节
- 不直接做网络调用细节
- 主要负责串联整个 agent 的执行路径

### [`models.py`](./models.py)

内部数据结构定义文件。

作用：

- 定义 agent 运行过程中使用的 dataclass
- 统一表示：
  - 诊断信息 `Diagnostic`
  - 函数签名 `FunctionSignature`
  - 函数级规格 `FunctionSpec`
  - 文件级规格 `FileSpec`
  - 模块信息 `ModuleEntry`
  - 总规格包 `SpecBundle`

这个文件相当于 agent 内部的“内存数据模型层”。

### [`specs.py`](./specs.py)

spec 解析和静态诊断的核心文件。

作用：

- 读取 module spec / file spec / function spec
- 归一化路径
- 建立从 trace_id 到规格对象的索引
- 建立从 header/source 路径到文件规格的索引
- 做一致性检查与诊断输出

当前已经实现的检查包括：

- JSON 基本结构缺失
- 文件级 spec 和函数级 spec 的关联关系
- 重复 trace_id
- 模块依赖顺序
- header/source 接口签名差异
- 孤立函数 spec

这个文件是整个 agent 的“规格真源入口”。

### [`llm_client.py`](./llm_client.py)

固定模型调用适配层。

作用：

- 固定使用模型 `qwen3-max-2026-01-23`
- 在 `agent` 项目内部直接实现 `chat_with_llm`
- 直接通过 OpenAI Python SDK 调用 DashScope 的 OpenAI-compatible 接口
- 对外暴露统一的 `FixedQwenClient`
- 在运行前检查所选 Qwen API key 环境变量是否存在

当前设计特点：

- 不支持模型切换
- 不依赖外部项目中的工具函数
- 在 agent 内部完成最小可用的重试、流式拼接和环境检查

### [`header_recipes.py`](./header_recipes.py)

头文件公共类型生成辅助文件。

作用：

- 为某些仅靠 spec 难以完整推断的公共类型提供“硬编码 recipe”
- 当前重点覆盖：
  - `mqtt/network/tcp_server.h`
  - `mqtt/protocol/mqtt_packet.h`
  - `mqtt/protocol/mqtt_decoder.h`
  - `mqtt/protocol/mqtt_encoder.h`
- 对于普通 opaque handle 类型，则按规则自动生成前置声明

之所以需要这个文件，是因为当前 specs 对部分公共结构体只描述了“角色”，没有完整字段信息；如果完全交给模型自由发挥，会导致跨文件结构不一致。

### [`prompts.py`](./prompts.py)

prompt 组织与压缩逻辑。

作用：

- 构造源文件生成 prompt
- 构造 repair prompt
- 将原始 spec 数据压缩成更适合模型消费的摘要
- 显式强调：
  - canonical header 是唯一公共接口真源
  - 不允许自造公共字段/枚举常量
  - 必须遵守 consistency rules

这个文件直接决定生成质量。

当前已经做过一次重要优化：

- 把原先过长的 prompt 改成了摘要式 prompt，减少模型响应时间和跑偏概率

### [`generation.py`](./generation.py)

代码生成主流程文件，是当前 agent 的核心实现。

作用：

- 生成头文件
- 生成 `main.c`
- 生成 `Makefile`
- 按模块顺序调用 LLM 生成 `.c` 文件
- 记录每一次 prompt 和 token 用量
- 执行编译
- 识别编译错误涉及的文件
- 对失败文件发起 repair prompt
- 写出 `run_manifest.json`

内部关键能力：

- `render_header()`：按 file spec 生成头文件
- `render_main_c()`：生成 broker 入口
- `render_makefile()`：生成 broker-only Makefile
- `ProjectGenerator.generate()`：串起完整生成流程
- `_repair_until_compiles()`：尝试编译修复闭环

当前状态：

- 主流程已经跑通
- 日志落盘正常
- 首轮生成完整工程正常
- repair 机制还需要继续增强，因为当前编译错误主要来自模型对公共类型的偏差，而不是单文件语法错误

### [`verifier.py`](./verifier.py)

生成结果验证器。

作用：

- 检查目标文件是否生成完整
- 检查头文件中是否包含 canonical signature
- 运行 `make mqtt_broker`
- 启动生成出的 broker
- 用 Python socket 构造最小 MQTT 报文做 smoke test

当前 smoke test 覆盖：

- `CONNECT -> CONNACK`
- `SUBSCRIBE -> SUBACK`
- `PUBLISH(QoS0)` 转发
- `PINGREQ -> PINGRESP`
- `DISCONNECT`
- `a/#` 与 `a/+` 的 topic 匹配行为

当前状态：

- 验证框架本身是完整的
- 当前失败原因是生成代码尚未编译通过，不是 verifier 自身坏了

## 运行方式

在 `~/SpecForge` 下运行：

```bash
# root 路由
python3 -m agent facts --help
python3 -m agent coder --help

# 兼容旧的 coder 入口
python3 -m agent validate
python3 -m agent generate
python3 -m agent verify

# 或使用子包入口
python3 -m agent.coder validate
python3 -m agent.coder generate
python3 -m agent.coder verify
python3 -m agent.facts validate --protocol-name coap --doc ~/SpecForge/document/rfc7252.txt --skip-llm-check
```

默认参数：

- module spec：`~/SpecForge/specs-example/mqtt_specs/mqtt_module_spec.json`
- spec root：`~/SpecForge/specs-example/mqtt_specs`
- output dir：`~/SpecForge/agent/out/mqtt_broker_<timestamp>`
- max repair rounds：`3`

与模型接入相关的环境变量：

- `ALI_API`
  - DashScope API Key
- `AGENT_LLM_TIMEOUT`
  - 单次模型请求超时秒数，默认 `30`
- `AGENT_USE_ENV_PROXY`
  - 是否信任当前 shell 的 `HTTP_PROXY` / `HTTPS_PROXY`
  - 默认关闭，也就是默认忽略环境代理
  - 如果你确认本地代理是通的，再显式设为 `1`

## 前置条件

- 已设置环境变量 `ALI_API`，或使用 `--api-key-env <name>` 选择其他已设置的 Qwen API key 环境变量
- Python 3.12
- 本机可访问 DashScope/OpenAI-compatible 接口

## 当前主要问题

当前 agent 最主要的瓶颈不是“没有功能”，而是“生成质量还不够稳定”。

具体表现：

- 头文件已经比较稳定
- 网络层和部分简单模块能生成出较像样的代码
- protocol/broker 交界处容易因为公共类型理解不一致而失败
- 当前 specs 中也存在一些不一致或信息不足的地方，例如：
  - 某些私有 helper 有 source interface，但没有 function spec
  - `mqtt_decoder_feed` 在文件级 spec 和函数级 spec 间存在签名差异
  - 有些公共类型只给了角色，没有完整结构定义

## 下一步建议

如果继续推进，建议优先做这几件事：

1. 继续强化 `mqtt_packet.h`、`mqtt_decoder.h`、`mqtt_encoder.h` 的公共类型约束，把 protocol 层做成更强的硬规则
2. 优化 repair 流程，让它能针对编译错误重新注入 canonical header 和更窄的上下文
3. 对 `broker.c`、`mqtt_decoder.c`、`message_router.c` 这几个高风险文件增加更强的专用 prompt 约束
4. 视需要补充 spec，使跨模块共享结构更完整，减少模型推断空间
