[PROMPT]
Implement function `coap_server_run`. Responsibility: 进入底层 UDP runtime 事件循环

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- FUNC `coap_udp_server_run`
  role: 调用 coap_udp_server_run 完成子步骤
```c
void coap_udp_server_run(coap_udp_server_t* s);
```

[GUARANTEE]
```c
void coap_server_run(coap_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 应用在 coap_server_start 成功后调用。
- Precondition: s 和 s->udp 非空；否则立即返回。
- Input: 输入是底层 UDP runtime 产生的 datagram events。

**Post-Condition**:
- State Change: 本函数自身不修改 server 状态；运行期间状态变化来自 UDP runtime callback 触发的 handlers。
- Response: 无返回值；coap_udp_server_run 返回后本函数返回。

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 或 s->udp 为空直接返回；否则调用 coap_udp_server_run(s->udp)，由 UDP runtime 通过 on_datagram callback 驱动 server handler。
