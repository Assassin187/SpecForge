[PROMPT]
Implement function `coap_message_add_option_uint`. Responsibility: 使用最短 big-endian unsigned integer encoding 添加 option；0 编码为零长度

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
bool coap_message_add_option_uint(coap_message_t* msg, uint16_t number, uint32_t value);
```

[SPECIFICATION]
**Pre-Condition**:
- msg/number 指定目标 option；value 是要编码的 unsigned integer。

**Post-Condition**:
- 返回 coap_message_add_option 的布尔结果。

**Invariant**:
- 整数 0 编码为零长度 option。
- 非零整数使用最短 big-endian 表示，最多 4 bytes。

**System Algorithm**:
- 若 value 为 0，调用 coap_message_add_option(msg, number, NULL, 0) 添加零长度 integer option。非 0 时先把 value 写入 4-byte big-endian 临时缓冲，然后跳过前导 0 bytes，只把最短非零 big-endian suffix 作为 option value 添加。
