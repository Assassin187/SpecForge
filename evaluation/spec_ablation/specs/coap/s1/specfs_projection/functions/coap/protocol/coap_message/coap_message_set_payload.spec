[PROMPT]
Implement function `coap_message_set_payload`. Responsibility: 释放旧 payload；空数据清空 payload，否则分配并复制新 bytes

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

[GUARANTEE]
```c
bool coap_message_set_payload(coap_message_t* msg, const uint8_t* data, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是目标 message；data/len 是新的 payload bytes。

**Post-Condition**:
- msg 为空或分配失败返回 false；清空 payload 或成功复制新 payload 返回 true。

**Invariant**:
- 调用失败于 malloc 时旧 payload 已被释放。
- data 为空时无论 len 如何都表示清空 payload。

**System Algorithm**:
- 若 msg 为空返回 false。先释放旧 payload，并将 payload 指针置 NULL、payload_len 置 0。若 data 为空或 len 为 0，清空 payload 后返回 true。否则 malloc(len)；分配失败返回 false 且 message 保持空 payload；分配成功后复制 len bytes 并设置 payload_len。
