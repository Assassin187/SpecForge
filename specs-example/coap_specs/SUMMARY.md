# CoAP specs-example 汇总（基于 specs-example/coap_specs）

> 说明：本 bundle 是与 `protocol-example/coap` 和 `gold_facts/coap_min` 对齐的人工 Gold reference；严格保持当前 minimum_v1 代码行为。

## 协议元信息
- 协议：CoAP minimum_v1
- 角色：SERVER
- Scope：plain UDP minimal server subset；外部 CoAP client 用于验证。

## 规格清单统计
- JSON 文件总数：67
- KIND 统计：PROTOCOL_MODULE_SPEC=1, FILE_SPEC=6, FUNCTION_SPEC=60

## 生成顺序
- network → protocol → resource → server_app

## 模块概览
### network
- 角色：UDP/epoll datagram runtime：接收一个完整 datagram 并携带 peer endpoint 上交；只负责 bytes 与网络生命周期，不解析 CoAP。
- 依赖：（无）
- Public artifacts：11
- 关联源码/header：../network/udp_server.h, ../network/udp_server.c

### protocol
- 角色：CoAP message 数据模型与 binary wire-format 编解码；不得依赖 UDP runtime、resource router 或 server handlers。
- 依赖：（无）
- Public artifacts：27
- 关联源码/header：../protocol/coap_message.h, ../protocol/coap_message.c, ../protocol/coap_codec.h, ../protocol/coap_codec.c

### resource
- 角色：资源注册与 dispatch：规范化 request view 由上层传入，按 path/method 选择 handler，并生成 discovery links。
- 依赖：protocol
- Public artifacts：8
- 关联源码/header：../resource/resource_router.h, ../resource/resource_router.c

### server_app
- 角色：server 入口与协议处理协调：解码 datagram、构建 response envelope、执行资源 handlers、维护内存 KV 并回包。
- 依赖：network, protocol, resource
- Public artifacts：6
- 关联源码/header：../main.c, ../server/coap_server.h, ../server/coap_server.c

## 文件与函数覆盖
- `coap/main`：1 个 FUNCTION_SPEC；CoAP server 进程入口：解析可选端口，创建、启动、运行并销毁 server
- `coap/network/udp_server`：8 个 FUNCTION_SPEC；无连接 UDP runtime：绑定 IPv4 socket，使用 epoll 接收 datagram，并按 peer endpoint 回包
- `coap/protocol/coap_message`：17 个 FUNCTION_SPEC；CoAP message 数据模型、动态内存生命周期、option helper 与 code helper
- `coap/protocol/coap_codec`：6 个 FUNCTION_SPEC；CoAP binary datagram wire-format 编解码器
- `coap/resource/resource_router`：7 个 FUNCTION_SPEC；CoAP resource registry：精确/前缀 path 匹配、method mask dispatch 与 core links 构造
- `coap/server/coap_server`：21 个 FUNCTION_SPEC；CoAP server 协调层：datagram 请求处理、response envelope、资源 handlers 与内存 KV 生命周期

## minimum_v1 行为边界
- 每个 UDP datagram 独立承载一个完整 CoAP message。
- 支持 CON/NON request、GET/POST/PUT/DELETE、Token、Message ID、Uri-Path、Uri-Query、Content-Format、Accept 与 payload。
- 提供 `/.well-known/core`、`/hello`、`/echo`、`/kv`、`/kv/<key>`。
- malformed CON 且可读取 Message ID 时返回空 RST；成功解码的 non-method message 静默忽略。
- 不实现 DTLS、Observe、Block1/Block2、deduplication、retransmission、separate response 或 4.13 enforcement。

## 关键测试向量
- Codec：合法 header/token/options/payload、extended option、invalid version/TKL、truncated message、reserved nibble、unsorted option encode。
- Core flow：malformed CON RST、non-method silent ignore、route miss、method not allowed、discovery Accept mismatch、KV lifecycle。
