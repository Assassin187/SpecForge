[PROMPT]
Implement function `coap_server_create`. Responsibility: 创建 server、router 与 UDP runtime，并用 1u << COAP_METHOD_* bitset 注册 discovery/hello/echo/kv routes

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- STRUCT `coap_udp_callbacks_t`
  role: UDP runtime callback 集合
```c
typedef struct coap_udp_callbacks {
    coap_udp_on_datagram_fn on_datagram;
} coap_udp_callbacks_t;
```

- STRUCT `coap_resource_router_t`
  role: 资源注册表与 dispatch 逻辑的不透明句柄
```c
typedef struct coap_resource_router coap_resource_router_t;
```

- FUNC `coap_resource_router_create`
  role: 调用 coap_resource_router_create 完成子步骤
```c
coap_resource_router_t* coap_resource_router_create(void);
```

- FUNC `coap_udp_server_create`
  role: 调用 coap_udp_server_create 完成子步骤
```c
coap_udp_server_t* coap_udp_server_create(uint16_t port, coap_udp_callbacks_t cb, void* user);
```

- FUNC `coap_resource_router_add`
  role: 调用 coap_resource_router_add 完成子步骤
```c
bool coap_resource_router_add(coap_resource_router_t* router, const char* path, bool prefix_match, uint32_t methods_mask, bool discoverable, const char* attributes, coap_resource_handler_fn handler, void* user);
```

- FUNC `coap_server_destroy`
  role: 调用 coap_server_destroy 完成子步骤
```c
void coap_server_destroy(coap_server_t* s);
```

[GUARANTEE]
```c
coap_server_t* coap_server_create(uint16_t port);
```

[SPECIFICATION]
**Pre-Condition**:
- port 是 UDP 监听端口。

**Post-Condition**:
- 成功返回拥有 router、udp runtime 和四条 route 的 server；任一分配或注册失败返回 NULL，并释放已经创建的资源。

**Invariant**:
- /.well-known/core 只允许 GET 且不 discoverable。
- /hello 只允许 GET 且 discoverable attributes 为 ct=0;title="hello"。
- /echo 允许 POST/PUT。
- /kv 使用 prefix match 并允许 GET/POST/PUT/DELETE。

**System Algorithm**:
- calloc 分配 coap_server_t；失败返回 NULL。保存 port。创建 resource router；失败时释放 server 并返回 NULL。构造 zeroed callbacks，设置 cb.on_datagram = on_datagram。创建 UDP server；失败时销毁 router、释放 server 并返回 NULL。计算 get_mask、read_write_mask(GET|POST|PUT|DELETE) 和 echo_mask(POST|PUT)。按顺序注册 /.well-known/core、/hello、/echo、/kv 四条 route；任一 coap_resource_router_add 失败时调用 coap_server_destroy(s) 并返回 NULL。全部成功返回 server。
