[PROMPT]
Implement function `handle_hello`. Responsibility: 返回 hello greeting，并在 query 非空时使用 query-received 文本

[RELY]
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
static bool handle_hello(void* user, const coap_request_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: resource router 匹配 /hello 且 method 允许后调用。
- Precondition: req/resp 由 router 传入；源码忽略 user，不显式检查 req/resp 为空。
- Input: 输入为 req->query 是否存在且非空。

**Post-Condition**:
- State Change: 不修改 server 状态；只写 response code、Content-Format 与 text payload。
- Response: 返回 response_set_text 的结果；成功时响应为 2.05 text/plain。

**Invariant**:
- None specified.

**System Algorithm**:
- 默认 text 为 "hello from CoAP server"；若 req->query 非空字符串，则改为 "hello from CoAP server (query received)"。调用 response_set_text(resp, coap_make_code(2, 5), COAP_FORMAT_TEXT_PLAIN, text)。
