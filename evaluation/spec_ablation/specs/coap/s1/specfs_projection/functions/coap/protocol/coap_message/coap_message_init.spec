[PROMPT]
Implement function `coap_message_init`. Responsibility: 将 message 清零并设置 version=1；空指针安全返回

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
void coap_message_init(coap_message_t* msg);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是可能为空的 message 对象指针。

**Post-Condition**:
- 无返回值；非空 msg 变为 version=1 的空 message，所有 pointer/count/length 字段为 0。

**Invariant**:
- 初始化不会释放 msg 之前可能持有的动态内存；调用者必须先 reset/free 旧内容。

**System Algorithm**:
- 若 msg 为空直接返回。否则 memset 整个对象为 0，并将 version 设置为 1。
