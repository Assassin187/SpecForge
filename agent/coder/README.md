# Spec-to-Code Coder Agent

这个目录实现了一个代码生成 agent（当前以 C 语言的 MQTT broker-only 服务端为示例落地）。

它位于三步流水线的第 3 步：

1. facts：技术文档 -> `protocol_facts.json`
2. planning：`protocol_facts.json` -> 工程规划/规格（spec）
3. coder：规格（spec）-> 可编译的代码工程

当前 `coder` 的核心关注点是：**输入的规格（spec）是什么、如何组织，以及它必须满足哪些一致性约束**。

## 当前工作流程（PPT 版）

`coder` 的端到端流程可以理解为：**加载 SPEC → 校验一致性 → 按模块生成工程 → 编译 → 按错误修复 → 编译成功后执行非阻断行为检查 → 输出 manifest 与日志**。

```mermaid
flowchart TD
    B[Spec Root<br/>PROTOCOL_MODULE_SPEC + FILE_SPEC + FUNCTION_SPEC] --> C[load_spec_bundle]

    C --> D[路径规范化<br/>../x -> x]
    D --> E[构建 SpecBundle<br/>protocol/modules/files/functions]
    E --> F[一致性校验<br/>trace/signature/dependency/order/type]

    F -->|有 error| X[停止生成]
    F -->|无 error| G[ProjectGenerator.prepare_output_dir]

    G --> H[按 GENERATION_ORDER 遍历模块]
    H --> I[Header 本地生成<br/>HEADER.DATA + HEADER.INTERFACE]
    H --> J[Source LLM 生成<br/>File Spec + Function Spec + 依赖 Header]
    H --> K[main.c 本地生成]

    I --> L[写入 project_dir]
    J --> L
    K --> L
    L --> M[Makefile 本地生成]
    M --> N[make mqtt_broker]

    N -->|成功| V[执行协议行为检查<br/>逐项记录 pass/fail]
    V --> O[写 run_manifest.json<br/>行为结果不改变生成成功状态]
    N -->|失败| P[抽取项目内 gcc error 文件]
    P -->|header 错误| X2[停止 repair<br/>记录 blocking header]
    P -->|source 错误| Q[LLM repair 普通 .c]
    Q --> N
    O --> R[输出工程 + _agent_logs]
```

### 运行目录结构

默认输出目录由 CLI 在运行时生成，形如：

```text
agent/out/<protocol>_<role>_YYYYMMDD_HHMMSS/
├── <protocol>/              # project_dir，真正生成出的协议工程
│   ├── Makefile
│   ├── main.c
│   ├── network/
│   ├── protocol/
│   ├── broker/
│   ├── topic/
│   └── router/
└── _agent_logs/             # prompt、编译输出、manifest、token 用量
    ├── 001_prompt_*.txt
    ├── 0xx_compile_stdout_*.txt
    ├── 0xx_compile_stderr_*.txt
    └── run_manifest.json
```

### 生成策略分层

```mermaid
flowchart LR
    S[SPEC] --> H[.h 本地确定性生成]
    S --> C[.c LLM 生成]
    S --> M[Makefile/main.c 本地模板生成]

    H --> HP[快：不调用模型<br/>public type / include / signature]
    C --> CP[慢：调用模型<br/>函数逻辑实现]
    M --> MP[快：不调用模型<br/>工程入口与构建规则]
```

| 产物 | 生成方式 | 主要输入 | 是否调用 LLM |
| --- | --- | --- | --- |
| `.h` | 本地确定性渲染 | `HEADER.DEPENDENCY` / `HEADER.DATA.TYPE_SPEC` / `HEADER.INTERFACE` | 否 |
| 普通 `.c` | LLM 生成 | File Spec、Function Spec、已生成 header、依赖 header、Consistency Rules | 是 |
| `main.c` | 本地模板生成 | broker/app 模块公开类型与 create/start/run/destroy 接口 | 否 |
| `Makefile` | 本地模板生成 | `GENERATION_ORDER` 后的模块文件列表、协议角色 | 否 |
| repair 文件 | LLM 修复 | 当前 `.c` 文件内容、压缩后的编译错误、canonical header、依赖 header | 是 |

> `.h`、`main.c` 和 Makefile 是本地确定性生成产物，不进入 LLM repair。若编译错误指向项目 header，`generate` 会停止 repair，并在 manifest 中记录 `deterministic_header_compile_error`，提示需要修复 specs 或 header lowering。

> `generate` 的成功条件仅为最终编译成功。编译成功后会自动执行预设协议行为检查，并逐项记录到控制台、`behavior_verification.json` 和 manifest；行为检查失败不会触发 repair、不会覆盖 compile repair 状态，也不会改变 `generate` 的成功状态或退出码。

### 单个模块内的生成顺序

```mermaid
sequenceDiagram
    participant G as ProjectGenerator
    participant S as SpecBundle
    participant L as LLM
    participant FS as Filesystem

    G->>S: 读取 GENERATION_ORDER 中的 module
    loop header files
        G->>S: 查 File Spec
        G->>G: render_header()
        G->>FS: 写入 .h
    end
    loop source files
        G->>S: 查 File Spec + Function Specs
        G->>FS: 读取依赖 header
        G->>L: build_source_prompt()
        L-->>G: 返回完整 .c 内容
        G->>FS: 写入 .c
    end
    G->>G: render_makefile()
    G->>FS: 写入 Makefile
```

## 输入契约（coder agent 吃什么）

`coder` 的输入是一组 JSON 规格文件（统称 SpecBundle），通过一个规格根目录传入：

1) Module Spec（模块级规格，单文件）
- 位于 `--spec-root` 指向的目录中
- `coder` 会递归扫描并自动发现唯一一个 `KIND == "PROTOCOL_MODULE_SPEC"` 的文件
- 例子：`~/SpecForge/specs-example/mqtt_specs/mqtt_module_spec.json`

2) Spec Root（规格根目录，一整个目录）
- 通过命令行参数 `--spec-root` 指定
- `coder` 会递归扫描该目录下所有匹配 `*_spec.json` 的文件
- 例子：`~/SpecForge/specs-example/mqtt_specs/`

> 注意：正常入口只需要传 `--spec-root`。`--module-spec` 仍保留为兼容性覆盖项；默认情况下 `coder` 会自动跳过已发现的模块级规格文件，避免重复解析。

### 路径与规范化规则

`coder` 在解析时会对所有路径做规范化（见 `normalize_repo_path()`）：

- 把 `\\` 替换为 `/`
- 去掉前后空白
- 反复剥离前缀 `../` 与 `./`

因此 Module Spec 里的 `FILES` 可以写成 `../network/connection.h` 这类形式，最终会归一化成 `network/connection.h`。规格文件里的路径不包含协议名目录；协议名是否作为输出目录的一部分由 `--output-dir` 决定。

## 输入文件 1：Module Spec（PROTOCOL_MODULE_SPEC）

Module Spec 必须是 JSON 对象，并且至少包含：

- `MODULES`: array
- （可选）`CONSISTENCY_RULES`: array

每个 `MODULES[i]` 至少应包含：

- `NAME`: 模块名（用于依赖排序与生成顺序）
- `ROLE`: 模块职责描述
- `DEPENDENCIES`: array[string]（模块依赖，必须在生成顺序里出现在当前模块之前）
- `FILES`: array[string]（该模块要生成/校验的头文件与源文件路径，路径会被规范化）
- （可选）`ARTIFACTS`: array[object]
- （可选）`DOC_REF`: array

`coder` 当前的生成顺序使用 `GENERATION_ORDER` 字段；如果该字段缺失或为空，会退回 `MODULES` 的数组顺序并给出 warning。无论使用哪种顺序，模块依赖都必须在生成顺序里出现在当前模块之前。

## 输入文件 2：File Spec（FILE_SPEC）

Spec Root 目录下，任意 `*_spec.json` 只要满足：

- `KIND == "FILE_SPEC"`

就会被解析为 File Spec。其结构要求如下（只列出 `coder` 读取/校验的关键字段）：

- `FILE`（object）
  - `TRACE_ID`：文件级 trace id（例如 `mqtt/router/message_router`）
  - `LANG`
  - `ROLE`
- `HEADER`（object，可选；无头文件的 `main.c` 可省略；planning 当前要求其他 `.c` 都有唯一对应 `.h`）
  - `PATH`：头文件相对路径（例如 `../router/message_router.h`，规范化后为 `router/message_router.h`）
  - `DEPENDENCY`：`#include "..."` 依赖列表（array[string]）
  - `DATA`：类型/常量等数据项声明（array[object]，目前主要用于头文件 public type 生成）
  - `INTERFACE`：头文件公开接口列表（array[object]）
    - 每项必须包含：`SIGNATURE` / `NAME` / `KIND` / `FUNCTION_TYPE` / `ROLE` / `VISIBILITY`
- `SOURCE`（object）
  - `PATH`：源文件相对路径（例如 `../router/message_router.c`，规范化后为 `router/message_router.c`）
  - `DEPENDENCY`：源文件依赖的头文件列表（array[string]）
  - `DATA`：源文件私有数据项（array[object]）
  - `INTERFACE`：源文件需要实现的接口列表（array[object]）
    - 每项必须包含：`TRACE_ID` / `SIGNATURE` / `NAME` / `KIND` / `ROLE` / `VISIBILITY`

### Source-only File Spec

一般 C 模块同时包含 `HEADER` 和 `SOURCE`。为服务 SpecForge 当前“快速从技术文档生成可用协议代码”的研究目标，planning 默认采用一源一头的简化 specs 架构：除入口 `main.c` 外，每个 `FILE_SPEC` 都应包含自己的 `HEADER.PATH`。入口文件 `main.c` 没有对应头文件时，可以只声明 `SOURCE`，用于描述 broker 启动入口；这种规格仍然可以通过 `SOURCE.INTERFACE[*].TRACE_ID` 关联 Function Spec。

### File Spec 与 Function Spec 的关联

`SOURCE.INTERFACE[*].TRACE_ID` 用于链接 Function Spec。

同时 `coder` 会检查 function trace id 的“父级”必须是某个 File Spec 的 `FILE.TRACE_ID`：

- File Spec: `mqtt/router/message_router`
- Function Spec: `mqtt/router/message_router/mqtt_message_router_publish`

如果 Function Spec 的父级不存在，会报错 `orphan_function_spec`。

## 输入文件 3：Function Spec（FUNCTION_SPEC）

Spec Root 目录下，任意 `*_spec.json` 只要满足：

- `KIND == "FUNCTION_SPEC"`

就会被解析为 Function Spec。其结构要求如下（只列出 `coder` 读取/校验/生成 prompt 会用到的关键字段）：

- `TRACE_ID`：必须能通过 `TRACE_ID.rsplit("/", 1)[0]` 找到对应 File Spec 的 `FILE.TRACE_ID`
- `FUNCTION_TYPE`：例如 `ALGORITHM` / `EVENT`
- `ROLE`
- `SIGNATURE`（object）
  - `RAW` / `NAME` / `RETURN` / `PARAMS`
- `RELY`（object）：描述依赖的 struct/func/var（用于生成 prompt 的上下文）
- `LOGIC` 或 `EVENT`：行为描述（`coder` 会用 `LOGIC`，否则 fallback 到 `EVENT`）

## 最小可用的运行方式（示例）

使用仓库自带样例 specs：

`coder` 的命令行全局参数需要放在子命令之前：

```bash
python3 -m agent coder \
  --spec-root ~/SpecForge/specs-example/mqtt_specs \
  validate

python3 -m agent coder \
  --spec-root ~/SpecForge/specs-example/mqtt_specs \
  generate

python3 -m agent coder \
  --api-key-env ALI_API_2 \
  --spec-root /home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_205409_170615_t/spec_bundle \
  generate

python3 -m agent coder \
  --api-key-env ALI_API_2 \
  --spec-root ~/SpecForge/specs-example/mqtt_specs \
  generate

python3 -m agent coder \
  --spec-root /home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  generate

```

`validate` 负责发现输入规格的结构/一致性问题；`generate` 在通过校验后生成一次运行目录，并以最终编译结果判定生成成功。编译成功后的协议行为检查仅作为非阻断检查项。生成的协议工程位于运行目录下的协议名子目录（例如 `mqtt/`），日志位于同级 `_agent_logs/`，`run_manifest.json` 也写入 `_agent_logs/`。日志只保留 prompt、编译输出、manifest 等诊断材料，不再镜像保存生成出的 `.c/.h`/Makefile 内容；manifest 中包含逐次 LLM 调用、按阶段汇总和最终总计的 token 用量。

## 常见输入问题（会影响生成质量）

- File Spec 里声明了 `SOURCE.INTERFACE`，但缺失对应的 Function Spec：会产生 `missing_function_spec` 警告
- Header/SOURCE/Function 三者的函数签名不一致：会产生 `signature_mismatch` / `header_signature_mismatch` 警告
- 不同 File Spec 认领了同一个 `HEADER.PATH` 或 `SOURCE.PATH`：会报错 `duplicate_file_path`
- 非 `main.c` File Spec 缺失 `HEADER.PATH`：属于 planning 输出不完整，可能导致生成阶段无法建立 canonical header 上下文
- 模块依赖顺序不满足（依赖出现在后面）：会报错 `generation_order_violation`
