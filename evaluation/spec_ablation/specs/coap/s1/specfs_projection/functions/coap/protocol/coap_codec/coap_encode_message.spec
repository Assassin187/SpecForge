[PROMPT]
Implement function `coap_encode_message`. Responsibility: 计算完整 datagram 大小，编码 header/token/ordered options，并仅在 payload 非空时写入 0xff marker

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

- STRUCT `coap_bytes_t`
  role: 编码结果动态字节缓冲，由 coap_bytes_free 释放
```c
typedef struct coap_bytes {
    uint8_t* data;
    size_t len;
} coap_bytes_t;
```

- FUNC `option_ext_len`
  role: 调用 option_ext_len 完成子步骤
```c
static size_t option_ext_len(uint16_t value);
```

- FUNC `encode_nibble`
  role: 调用 encode_nibble 完成子步骤
```c
static uint8_t encode_nibble(uint16_t value);
```

- FUNC `encode_ext`
  role: 调用 encode_ext 完成子步骤
```c
static bool encode_ext(uint16_t value, uint8_t nibble, uint8_t* out, size_t* out_len);
```

[GUARANTEE]
```c
bool coap_encode_message(const coap_message_t* msg, coap_bytes_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是已构造的 CoAP message；out 是编码结果接收对象。

**Post-Condition**:
- 成功时设置 out->data 为新分配 datagram，out->len 为实际写入长度并返回 true；失败返回 false，encode_ext 失败时释放临时 data。

**Invariant**:
- options 必须按 nondecreasing number 排列。
- 只有 payload_len > 0 时写 payload marker。
- 调用方负责用 coap_bytes_free 释放成功返回的 out->data。

**System Algorithm**:
- 若 msg/out 为空、msg->version != 1 或 token_len > COAP_MAX_TOKEN_LEN，返回 false。先计算总长度：4-byte header + token，逐个 option 要求 option number 非递减，否则返回 false；每个 option 贡献 1 byte header、delta/length 扩展字节和 value bytes；payload_len > 0 时额外加入 0xff marker 与 payload。分配总长度缓冲失败返回 false。随后写 header、code、message_id big-endian、token，再按顺序写每个 option 的 delta/length nibble、扩展字节和 value。任何 encode_ext 失败都会 free(data) 并返回 false。payload 非空时写 0xff marker 后复制 payload。
