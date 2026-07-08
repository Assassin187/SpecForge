[PROMPT]
Implement function `coap_decode_message`. Responsibility: 按单个 datagram 解码 4-byte header、token、ordered options、payload marker 与 payload；失败时释放 out

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

- FUNC `coap_message_init`
  role: 调用 coap_message_init 完成子步骤
```c
void coap_message_init(coap_message_t* msg);
```

- FUNC `decode_ext`
  role: 调用 decode_ext 完成子步骤
```c
static bool decode_ext(const uint8_t* data, size_t len, size_t* off, uint8_t nibble, uint16_t* out_value);
```

- FUNC `coap_message_add_option`
  role: 调用 coap_message_add_option 完成子步骤
```c
bool coap_message_add_option(coap_message_t* msg, uint16_t number, const uint8_t* value, size_t len);
```

- FUNC `coap_message_set_payload`
  role: 调用 coap_message_set_payload 完成子步骤
```c
bool coap_message_set_payload(coap_message_t* msg, const uint8_t* data, size_t len);
```

- FUNC `coap_message_free`
  role: 调用 coap_message_free 完成子步骤
```c
void coap_message_free(coap_message_t* msg);
```

[GUARANTEE]
```c
coap_decode_status_t coap_decode_message(const uint8_t* data, size_t len, coap_message_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- data/len 是单个 UDP datagram；out 是调用方提供的 coap_message_t 输出对象。

**Post-Condition**:
- 成功返回 COAP_DECODE_OK；格式非法或内存添加失败返回 COAP_DECODE_INVALID；长度不足或扩展字段越界返回 COAP_DECODE_TRUNCATED。失败路径会调用 coap_message_free(out) 清理已写入字段。

**Invariant**:
- offset 始终相对完整 datagram。
- option number 通过 delta 累加得到。
- payload marker 0xff 本身不进入 payload。
- 输入不跨 datagram 累积。

**System Algorithm**:
- 若 data 为空、len < 4 或 out 为空，返回 COAP_DECODE_TRUNCATED。先 coap_message_init(out)，再从 header 解析 version、type、token_len、code、message_id。version 不为 1 或 token_len > COAP_MAX_TOKEN_LEN 时释放 out 并返回 COAP_DECODE_INVALID。若 datagram 不足以包含 token，释放 out 并返回 COAP_DECODE_TRUNCATED。复制 token 后从 absolute offset 4 + token_len 开始解析 options：0xff 表示 payload marker 并退出 option loop；每个 option byte 拆为 delta/length nibble，任何 nibble 15 都是 invalid；decode_ext 失败视为 truncated；累加 option number，若 option value 越界返回 truncated；coap_message_add_option 失败返回 invalid。option loop 后若 off < len，将剩余 bytes 设置为 payload；payload 分配失败返回 invalid。
