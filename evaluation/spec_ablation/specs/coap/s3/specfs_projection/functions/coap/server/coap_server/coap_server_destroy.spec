[PROMPT]
Implement function `coap_server_destroy`. Responsibility: 停止 server，销毁 UDP/router，释放全部 KV entries 与 server

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- STRUCT `kv_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct kv_entry kv_entry_t;
```

- FUNC `coap_server_stop`
  role: 调用 coap_server_stop 完成子步骤
```c
void coap_server_stop(coap_server_t* s);
```

- FUNC `coap_udp_server_destroy`
  role: 调用 coap_udp_server_destroy 完成子步骤
```c
void coap_udp_server_destroy(coap_udp_server_t* s);
```

- FUNC `coap_resource_router_destroy`
  role: 调用 coap_resource_router_destroy 完成子步骤
```c
void coap_resource_router_destroy(coap_resource_router_t* router);
```

[GUARANTEE]
```c
void coap_server_destroy(coap_server_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- s 是可能为空的 server。

**Post-Condition**:
- 无返回值；非空 server 拥有的 UDP runtime、router、KV entries 和 server 对象均被释放。

**Invariant**:
- destroy 前先 stop，以停止底层 UDP runtime。
- KV content_format metadata 不需要单独释放。

**System Algorithm**:
- 若 s 为空直接返回。否则先 coap_server_stop(s)，再销毁 s->udp 与 s->router。随后遍历 kv_entries，释放每个 key 和 value；释放 kv_entries 数组；最后释放 server 本身。
