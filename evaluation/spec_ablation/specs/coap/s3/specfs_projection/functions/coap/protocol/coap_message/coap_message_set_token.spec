[PROMPT]
Implement function `coap_message_set_token`. Responsibility: 校验 token_len<=8，记录长度并在 token 非空时复制 bytes

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
bool coap_message_set_token(coap_message_t* msg, const uint8_t* token, size_t token_len);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是目标 message；token/token_len 是待保存 token bytes。

**Post-Condition**:
- 参数非法返回 false；否则返回 true 并更新 token_len，必要时复制 token bytes。

**Invariant**:
- token 最大长度为 COAP_MAX_TOKEN_LEN。
- 函数不清零未被覆盖的 token buffer bytes。

**System Algorithm**:
- 若 msg 为空或 token_len > COAP_MAX_TOKEN_LEN，返回 false。否则先设置 msg->token_len = token_len。仅当 token_len > 0 且 token 非空时复制 token bytes 到 msg->token；token_len > 0 但 token 为空时源码仍返回 true，只是不复制 token 内容。
