[PROMPT]
Implement function `coap_message_join_options`. Responsibility: 按出现顺序连接匹配 option；无匹配返回新分配空字符串，调用方负责 free

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
char* coap_message_join_options(const coap_message_t* msg, uint16_t number, char sep);
```

[SPECIFICATION]
**Pre-Condition**:
- msg 是待查询 message；number 是目标 option number；sep 是多个 option value 之间的分隔字符。

**Post-Condition**:
- 返回新分配的 NUL-terminated string；msg 为空或分配失败返回 NULL。调用方负责 free 返回值。

**Invariant**:
- 保留 option 出现顺序。
- 匹配 option 的 value bytes 按原样复制，函数不做 UTF-8 或路径规范化。

**System Algorithm**:
- 若 msg 为空返回 NULL。第一遍统计匹配 option 的总 value 长度和数量，每个非首个匹配项额外计入 1 byte separator。没有匹配项时 calloc(1,1) 返回新分配空字符串。存在匹配项时 malloc(total+1)；失败返回 NULL。第二遍按原出现顺序复制每个匹配 option value，匹配项之间写入 sep，最后写入 NUL terminator。
