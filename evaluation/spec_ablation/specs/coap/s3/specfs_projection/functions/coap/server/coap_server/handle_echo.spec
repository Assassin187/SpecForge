[PROMPT]
Implement function `handle_echo`. Responsibility: 以 2.04 echo 请求 payload，保留 Content-Format 或默认 text/plain

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

- FUNC `response_set_bytes`
  role: 调用 response_set_bytes 完成子步骤
```c
static bool response_set_bytes(coap_message_t* resp, uint8_t code, uint16_t format, const uint8_t* data, size_t len);
```

- FUNC `coap_make_code`
  role: 调用 coap_make_code 完成子步骤
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[GUARANTEE]
```c
static bool handle_echo(void* user, const coap_request_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: resource router 匹配 /echo 且 method 为 POST 或 PUT 后调用。
- Precondition: req/resp 由 router 传入；源码忽略 user，不显式检查 req/resp 为空。
- Input: 输入为请求 payload、payload_len、has_content_format 与 content_format。

**Post-Condition**:
- State Change: 不修改 server 状态；只写 response message。
- Response: 返回 response_set_bytes 的结果；成功时响应为 2.04，payload 等于请求 payload。

**Invariant**:
- None specified.

**System Algorithm**:
- 默认 response format 为 COAP_FORMAT_TEXT_PLAIN；若 req->has_content_format 为 true，则使用 req->content_format。调用 response_set_bytes(resp, coap_make_code(2, 4), format, req->message->payload, req->message->payload_len)，复制请求 payload 到响应。
