[PROMPT]
Implement function `handle_kv`. Responsibility: 实现 /kv collection 与 /kv/<key> 的 list/create/update/read/delete 行为

[RELY]
- STRUCT `coap_server_t`
  role: CoAP server 进程级对象的不透明句柄
```c
typedef struct coap_server coap_server_t;
```

- STRUCT `kv_entry_t`
  role: 本函数使用的模块内部数据结构
```c
typedef struct kv_entry kv_entry_t;
```

- STRUCT `coap_request_t`
  role: 从 decoded message 投影出的资源请求视图
```c
typedef struct coap_request {
    const coap_message_t* message;
    coap_method_t method;
    char* path;
    char* query;
    uint16_t content_format;
    bool has_content_format;
    uint16_t accept;
    bool has_accept;
} coap_request_t;
```

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

- FUNC `find_kv`
  role: 调用 find_kv 完成子步骤
```c
static kv_entry_t* find_kv(coap_server_t* s, const char* key);
```

- FUNC `kv_put`
  role: 调用 kv_put 完成子步骤
```c
static bool kv_put(coap_server_t* s, const char* key, const uint8_t* value, size_t len, bool has_content_format, uint16_t content_format, bool* created);
```

- FUNC `kv_delete`
  role: 调用 kv_delete 完成子步骤
```c
static bool kv_delete(coap_server_t* s, const char* key);
```

- FUNC `response_set_text`
  role: 调用 response_set_text 完成子步骤
```c
static bool response_set_text(coap_message_t* resp, uint8_t code, uint16_t format, const char* text);
```

- FUNC `response_set_bytes`
  role: 调用 response_set_bytes 完成子步骤
```c
static bool response_set_bytes(coap_message_t* resp, uint8_t code, uint16_t format, const uint8_t* data, size_t len);
```

- FUNC `coap_make_code`
  role: 调用 coap_make_code 完成子步骤
```c
uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
```

[GUARANTEE]
```c
static bool handle_kv(void* user, const coap_request_t* req, coap_message_t* resp);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: resource router 匹配 /kv 或 /kv/<key> 后调用。
- Precondition: user 必须是 coap_server_t*，req/resp 非空；源码若任一为空返回 false。req->path 由 build_request_view 规范化并以 /kv 开头。
- Input: 输入包括 req->method、/kv 后的 suffix、请求 payload、Content-Format metadata，以及 server 当前 kv_entries。

**Post-Condition**:
- State Change: GET 不改变 KV store；PUT/POST 可能创建或更新 entry；DELETE 命中时删除 entry 并压紧数组；所有路径都写 resp->code 或完整 response。
- Response: 参数无效返回 false；已处理请求返回 true，响应 code 按源码分支为 2.01、2.02、2.04、2.05、4.00、4.04、4.05 或 5.00。

**Invariant**:
- None specified.

**System Algorithm**:
- 先令 suffix = req->path + 3，并跳过连续 /。若 method 为 GET 且 suffix 为空，构造 JSON array 字符串列出所有 key；分配失败时设置 5.00 并返回 true，成功时用 response_set_text 返回 2.05 application/json 并释放临时 json。若 suffix 为空但不是 collection GET，设置 4.00 并返回 true。否则查找 key。GET key 未命中时设置 4.04；命中时用 response_set_bytes 返回 2.05，format 为 entry content_format 或默认 COAP_FORMAT_OCTET_STREAM。PUT/POST 调用 kv_put；失败设置 5.00；成功时 created 为 true 返回 2.01，否则返回 2.04。DELETE 未命中返回 4.04；命中时调用 kv_delete，设置 2.02。其他 method 设置 4.05。
