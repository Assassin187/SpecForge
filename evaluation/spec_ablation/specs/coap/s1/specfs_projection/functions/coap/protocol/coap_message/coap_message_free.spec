[PROMPT]
Implement function `coap_message_free`. Responsibility: 通过 reset 释放 message 所有动态字段并恢复可复用空状态

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

- FUNC `coap_message_reset`
  role: 调用 coap_message_reset 完成子步骤
```c
void coap_message_reset(coap_message_t* msg);
```

[GUARANTEE]
```c
void coap_message_free(coap_message_t* msg);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是可能为空的 message 对象。

**Post-Condition**:
- 无返回值；非空 msg 的动态内容被释放并恢复为空 message，但 msg 对象本身不被 free。

**Invariant**:
- 该函数释放 message 内部字段，不释放 coap_message_t 存储本身。

**System Algorithm**:
- 直接调用 coap_message_reset(msg)。空指针由 reset 安全处理。
