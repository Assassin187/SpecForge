[PROMPT]
Implement function `response_set_text`. Responsibility: 设置 response code、Content-Format option 与字符串 payload

[RELY]
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

- FUNC `coap_message_add_option_uint`
  role: 调用 coap_message_add_option_uint 完成子步骤
```c
bool coap_message_add_option_uint(coap_message_t* msg, uint16_t number, uint32_t value);
```

- FUNC `coap_message_set_payload_string`
  role: 调用 coap_message_set_payload_string 完成子步骤
```c
bool coap_message_set_payload_string(coap_message_t* msg, const char* s);
```

[GUARANTEE]
```c
static bool response_set_text(coap_message_t* resp, uint8_t code, uint16_t format, const char* text);
```

[SPECIFICATION]
**Pre-Condition**:
- resp 是要写入的 response message；code/format/text 是响应 code、Content-Format 与字符串 payload。

**Post-Condition**:
- Content-Format option 添加失败返回 false；否则返回 payload 设置结果。即使后续失败，resp->code 已经被写入。

**Invariant**:
- text 为 NULL 时 payload 被清空。
- payload 不包含字符串 NUL terminator。

**System Algorithm**:
- 先设置 resp->code = code。调用 coap_message_add_option_uint(resp, COAP_OPT_CONTENT_FORMAT, format) 添加 Content-Format；失败时返回 false。成功后调用 coap_message_set_payload_string(resp, text) 设置字符串 payload。
