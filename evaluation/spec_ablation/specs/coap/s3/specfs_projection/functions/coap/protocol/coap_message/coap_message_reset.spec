[PROMPT]
Implement function `coap_message_reset`. Responsibility: 释放所有 option value、option 数组和 payload，再恢复 version=1 的空 message

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

- FUNC `coap_message_init`
  role: 调用 coap_message_init 完成子步骤
```c
void coap_message_init(coap_message_t* msg);
```

[GUARANTEE]
```c
void coap_message_reset(coap_message_t* msg);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是可能为空、可能持有 options 和 payload 的 message。

**Post-Condition**:
- 无返回值；非空 msg 的动态字段被释放，version 重新设为 1，计数和指针清零。

**Invariant**:
- 每个 option value 在释放 options 数组前释放。
- reset 后 msg 可被复用。

**System Algorithm**:
- 若 msg 为空直接返回。否则遍历 option_count，释放每个 options[i].value；释放 options 数组；释放 payload；最后调用 coap_message_init(msg) 恢复空 message 状态。
