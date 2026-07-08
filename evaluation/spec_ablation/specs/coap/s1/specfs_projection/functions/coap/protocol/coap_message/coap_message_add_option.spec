[PROMPT]
Implement function `coap_message_add_option`. Responsibility: 扩展 option 数组并复制 option value，成功后递增 option_count

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

- FUNC `xrealloc`
  role: 调用 xrealloc 完成子步骤
```c
static void* xrealloc(void* p, size_t n);
```

[GUARANTEE]
```c
bool coap_message_add_option(coap_message_t* msg, uint16_t number, const uint8_t* value, size_t len);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是目标 message；number 是 option number；value/len 是要复制的 option value。

**Post-Condition**:
- 成功添加 option 返回 true；参数非法、数组扩展失败或 value 分配失败返回 false。

**Invariant**:
- 成功路径复制 value，调用方仍拥有原始 value。
- value 分配失败时 options 数组可能已扩展，但新 entry 不计入 option_count。

**System Algorithm**:
- 若 msg 为空或 len > UINT16_MAX，返回 false。用 xrealloc 将 options 数组扩展到 option_count + 1；失败返回 false。扩展成功后在当前尾部清零新 option，写入 number 和 length。若 len > 0，malloc(len) 存放 value；分配失败返回 false 且 option_count 不递增；分配成功后复制 value bytes。最后 option_count 加 1。源码假定 len > 0 时 value 可读。
