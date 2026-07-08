[PROMPT]
Implement function `coap_message_get_uint_option`. Responsibility: 查找首个匹配 option，并将最长 4-byte big-endian value 解码为 uint32_t

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

- STRUCT `coap_option_t`
  role: 解码后的单个 CoAP option，持有 value 动态副本
```c
typedef struct coap_option {
    uint16_t number;
    uint16_t length;
    uint8_t* value;
} coap_option_t;
```

[GUARANTEE]
```c
bool coap_message_get_uint_option(const coap_message_t* msg, uint16_t number, uint32_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是待查询 message；number 是目标 option number；out 可为空。

**Post-Condition**:
- 找到可解码 option 返回 true；msg 为空、匹配项长度超过 4 或没有匹配项返回 false。失败时 out 已被预置为 0。

**Invariant**:
- 只读取首个匹配 option。
- 零长度 integer option 解码为 0 并返回 true。

**System Algorithm**:
- 若 out 非空，先将 *out 置 0。若 msg 为空返回 false。按出现顺序查找首个 number 匹配的 option；不匹配项跳过。匹配项长度大于 4 时返回 false。长度 0..4 时按 big-endian 累加为 uint32_t；out 非空时写出该值，然后返回 true。若没有匹配 option 返回 false。
