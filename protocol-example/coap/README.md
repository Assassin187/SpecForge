# CoAP 服务端最小实现

这是一个基于 UDP 的 CoAP 最小服务端实现，目标是“常用功能可运行 + 代码结构清晰，便于扩展”。

## 已支持能力

- CoAP over UDP
- `CON` / `NON` 请求
- `GET` / `POST` / `PUT` / `DELETE`
- Token、Message ID、URI Path、URI Query、Content-Format、Accept
- 常见响应码：`2.01` / `2.02` / `2.04` / `2.05` / `4.00` / `4.04` / `4.05` / `4.06` / `4.13` / `5.00`
- `/.well-known/core` 资源发现
- 示例资源：
  - `/hello`
  - `/echo`
  - `/kv`
  - `/kv/<key>`

## 目录

```text
coap/
├── Makefile
├── main.c
├── network/
│   └── udp_server.h/.c
├── protocol/
│   ├── coap_message.h/.c
│   └── coap_codec.h/.c
├── resource/
│   └── resource_router.h/.c
└── server/
    └── coap_server.h/.c
```

## 构建

```bash
cd ~/SpecForge/protocol-example/coap
make
```

## 运行

```bash
./coap_server [port]
```

默认端口是 `5683`。

## 快速测试

如果本机装了 `coap-client`：

```bash
coap-client-notls -m get coap://127.0.0.1:5683/hello
coap-client-notls -m get coap://127.0.0.1:5683/.well-known/core
coap-client-notls -m put -e 'value-1' coap://127.0.0.1:5683/kv/demo
coap-client-notls -m get coap://127.0.0.1:5683/kv/demo
coap-client-notls -m delete coap://127.0.0.1:5683/kv/demo
```

`/echo` 可以用 `POST` 或 `PUT` 测试：

```bash
coap-client-notls -m post -e 'hello' coap://127.0.0.1:5683/echo
```

## 当前范围

- 没有实现 DTLS
- 没有实现 Observe、Block1/Block2、去重缓存、重传状态机
- 以服务端常用路径处理为主，适合作为学习、实验和继续扩展的基础
