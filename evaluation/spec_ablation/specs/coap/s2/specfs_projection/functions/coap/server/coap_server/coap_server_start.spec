[PROMPT]
Implement function `coap_server_start`. Responsibility: 启动底层 UDP runtime

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- FUNC `coap_udp_server_start`
  role: 调用 coap_udp_server_start 完成子步骤
```c
bool coap_udp_server_start(coap_udp_server_t* s);
```

[GUARANTEE]
```c
bool coap_server_start(coap_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是 server 对象。

**Post-Condition**:
- 底层 UDP server start 成功返回 true；server/udp 缺失或底层启动失败返回 false。

**Invariant**:
- 本函数不直接打开 socket；所有网络启动副作用由 coap_udp_server_start 执行。

**System Algorithm**:
- 若 s 为空或 s->udp 为空，返回 false。否则调用 coap_udp_server_start(s->udp) 并原样返回其结果。
