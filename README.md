## 目录总览

- `document/`
  - 存放协议参考资料。
  - 当前文件：`MQTT_Version_3.1.1.html`，用于对照 MQTT 3.1.1 协议条款。

- `mqtt/`
  - MQTT 项目的 C 代码实现（源码与可执行入口）。
  - 关键内容：
    - `main.c`：程序入口。
    - `Makefile`：编译构建脚本。
    - `README.md`：项目使用与说明。
    - `broker/`：Broker 核心逻辑（会话管理、报文处理、生命周期控制）。
    - `network/`：网络层封装（连接对象、TCP 服务器、事件循环）。
    - `protocol/`：协议编解码与报文结构处理（decoder/encoder/packet）。
    - `router/`：消息路由（订阅匹配后转发消息）。
    - `topic/`：主题过滤与订阅树匹配（含 `+`、`#` 通配逻辑）。

- `mqtt_specs/`
  - `mqtt/` 源码对应的规格描述文件。
  - 结构按模块与源码目录对齐，分为：`broker/`、`network/`、`protocol/`、`router/`、`topic/`。
  - 主要文件类型：
    - `*_spec.json`（文件级规格，描述 FILE/HEADER/SOURCE）。
    - 函数级 `*_spec.json`（描述单个函数的签名、依赖、逻辑/事件）。
    - `mqtt_module_spec.json`（模块级汇总入口）。

- `specs_schema/`
  - 规格 JSON 的 schema 定义。
  - `file_spec_schema.json`：约束文件级规格格式（FILE/HEADER/SOURCE 结构）。
  - `function_spec_schema.json`：约束函数级规格格式（SIGNATURE/RELY/LOGIC/EVENT 等）。

## 目录关系

- `mqtt/` 是实现层（代码）。
- `mqtt_specs/` 是规格层（对实现做结构化描述）。
- `specs_schema/` 是校验层（约束规格文件格式与字段）。
- `document/` 是参考层（协议原文依据）。

## 使用建议

- 修改代码时：优先更新 `mqtt/`，再同步更新 `mqtt_specs/` 对应模块。
- 修改规格字段结构时：先更新 `specs_schema/`，再批量校验和修订 `mqtt_specs/`。
- 协议语义不确定时：回查 `document/MQTT_Version_3.1.1.html` 作为依据。
