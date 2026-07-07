[PROMPT]
Implement function `handle_discovery`. Responsibility: 处理 /.well-known/core discovery；Accept 存在且不是 COAP_FORMAT_LINK_FORMAT 时返回 4.06

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
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

- FUNC `coap_resource_router_build_core_links`
  role: 调用 coap_resource_router_build_core_links 完成子步骤
```c
char* coap_resource_router_build_core_links(const coap_resource_router_t* router);
```

- FUNC `response_set_text`
  role: 调用 response_set_text 完成子步骤
```c
static bool response_set_text(coap_message_t* resp, uint8_t code, uint16_t format, const char* text);
```

- FUNC `coap_make_code`
  role: 调用 coap_make_code 完成子步骤
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[GUARANTEE]
```c
static bool handle_discovery(void* user, const coap_request_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: resource router 匹配 /.well-known/core 后调用 discovery handler。
- Precondition: user 必须是 coap_server_t*，req/resp 非空；源码若任一为空返回 false。
- Input: 输入包括 req->accept、server router 中 discoverable entries，以及 resp 输出 message。

**Post-Condition**:
- State Change: 不修改 router；只写 response message，并释放临时 links。
- Response: 参数无效返回 false；不可接受 Accept 返回 4.06；links 分配失败返回 5.00；成功返回 2.05 application/link-format payload。

**Invariant**:
- None specified.

**System Algorithm**:
- 若 req->accept 非 0 且不等于 COAP_FORMAT_LINK_FORMAT，则只设置 resp->code = coap_make_code(4, 6) 并返回 true。否则调用 coap_resource_router_build_core_links(s->router)；返回 NULL 时设置 resp->code = coap_make_code(5, 0) 并返回 true。成功构造 links 后调用 response_set_text(resp, coap_make_code(2, 5), COAP_FORMAT_LINK_FORMAT, links)，随后 free(links)，并返回 response_set_text 的结果。
