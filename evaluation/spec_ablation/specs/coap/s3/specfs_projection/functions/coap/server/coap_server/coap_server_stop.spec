[PROMPT]
Implement function `coap_server_stop`. Responsibility: 停止底层 UDP runtime；空指针安全返回

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- FUNC `coap_udp_server_stop`
  role: 调用 coap_udp_server_stop 完成子步骤
```c
void coap_udp_server_stop(coap_udp_server_t* s);
```

[GUARANTEE]
```c
void coap_server_stop(coap_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是可能为空的 server。

**Post-Condition**:
- 无返回值；存在底层 UDP runtime 时会停止 runtime 并关闭其 fd/epoll fd。

**Invariant**:
- 本函数不释放 server 或 udp 对象，只停止 runtime。

**System Algorithm**:
- 若 s 为空或 s->udp 为空直接返回。否则调用 coap_udp_server_stop(s->udp)。
