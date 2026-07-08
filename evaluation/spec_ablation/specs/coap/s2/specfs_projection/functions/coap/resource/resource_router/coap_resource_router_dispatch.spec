[PROMPT]
Implement function `coap_resource_router_dispatch`. Responsibility: 按注册顺序查找首个 path match；用 1u << req->method 检查 methods_mask，method 不允许时写 4.05，否则调用 handler；无 route 返回 false

[RELY]
- STRUCT `coap_resource_router_t`
  role: 资源注册表与 dispatch 逻辑的不透明句柄
```c
typedef struct coap_resource_router coap_resource_router_t;
```

- STRUCT `coap_resource_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct coap_resource_entry coap_resource_entry_t;
```

- STRUCT `coap_request_t`
  role: 从 decoded message 投影出的资源请求视图
```c
typedef struct coap_request {
    const coap_message_t* message;
    coap_method_t method;
    char* path;
    char* query;
    uint16_t content_format;
    bool has_content_format;
    uint16_t accept;
    bool has_accept;
} coap_request_t;
```

- STRUCT `coap_message_t`
  role: 统一 CoAP message 对象，保存 header、token、options 与 payload
```c
typedef struct coap_message {
    uint8_t version;
    coap_type_t type;
    uint8_t code;
    uint16_t message_id;
    uint8_t token_len;
    uint8_t token[COAP_MAX_TOKEN_LEN];
    coap_option_t* options;
    size_t option_count;
    uint8_t* payload;
    size_t payload_len;
} coap_message_t;
```

- FUNC `path_matches`
  role: 调用 path_matches 完成子步骤
```c
static bool path_matches(const coap_resource_entry_t* e, const char* path);
```

- FUNC `coap_make_code`
  role: 调用 coap_make_code 完成子步骤
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[GUARANTEE]
```c
bool coap_resource_router_dispatch(coap_resource_router_t* router, const coap_request_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- router 是注册表；req 包含 method/path；resp 是 handler 或 router 写入 response code 的输出 message。

**Post-Condition**:
- 返回 true 表示已由 router/handler 处理并可能写入 resp；返回 false 表示参数无效或没有匹配 route。method 不允许时返回 true 且 response code 为 4.05。

**Invariant**:
- 只使用第一个 path match。
- 无 route 时不写 resp->code，由调用方决定 4.04。

**System Algorithm**:
- 若 router、req、resp 或 req->path 为空返回 false。计算 method_bit = 1u << req->method。按注册顺序遍历 entries，跳过 path_matches 为 false 的 entry。首个 path match 若 methods_mask 不含 method_bit，则设置 resp->code = coap_make_code(4, 5) 并返回 true。若 method 允许，则调用 entry handler(user, req, resp) 并原样返回 handler 结果。遍历结束仍无 path match 时返回 false。
