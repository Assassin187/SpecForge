[PROMPT]
Implement function `coap_message_set_payload_string`. Responsibility: 将字符串内容作为不含 NUL 的 payload；NULL 表示空 payload

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

- FUNC `coap_message_set_payload`
  role: 调用 coap_message_set_payload 完成子步骤
```c
bool coap_message_set_payload(coap_message_t* msg, const uint8_t* data, size_t len);
```

[GUARANTEE]
```c
bool coap_message_set_payload_string(coap_message_t* msg, const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是目标 message；s 是可空 NUL-terminated string。

**Post-Condition**:
- 返回 coap_message_set_payload 的布尔结果。

**Invariant**:
- 字符串 payload 不包含结尾 NUL。

**System Algorithm**:
- 若 s 为空，调用 coap_message_set_payload(msg, NULL, 0) 清空 payload。否则以 strlen(s) 作为长度，将字符串 bytes 作为 payload，不包含 NUL terminator。
