[PROMPT]
Implement function `coap_message_add_option_string`. Responsibility: 以字符串 bytes 添加 option；NULL 表示零长度 option

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

- FUNC `coap_message_add_option`
  role: 调用 coap_message_add_option 完成子步骤
```c
bool coap_message_add_option(coap_message_t* msg, uint16_t number, const uint8_t* value, size_t len);
```

[GUARANTEE]
```c
bool coap_message_add_option_string(coap_message_t* msg, uint16_t number, const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- msg/number 指定目标 option；s 是可空 NUL-terminated string。

**Post-Condition**:
- 返回 coap_message_add_option 的布尔结果。

**Invariant**:
- 字符串 option value 不包含结尾 NUL。

**System Algorithm**:
- 若 s 为空，调用 coap_message_add_option(msg, number, NULL, 0) 添加零长度 option。否则以 strlen(s) 为长度，把字符串 bytes 作为 option value 添加，不包含 NUL terminator。
