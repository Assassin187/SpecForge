[PROMPT]
Implement function `build_request_view`. Responsibility: 从 method message 构造 normalized path、joined query 与 Content-Format/Accept 视图

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

- FUNC `coap_code_is_method`
  role: 调用 coap_code_is_method 完成子步骤
```c
bool coap_code_is_method(uint8_t code);
```

- FUNC `coap_message_join_options`
  role: 调用 coap_message_join_options 完成子步骤
```c
char* coap_message_join_options(const coap_message_t* msg, uint16_t number, char sep);
```

- FUNC `coap_message_get_uint_option`
  role: 调用 coap_message_get_uint_option 完成子步骤
```c
bool coap_message_get_uint_option(const coap_message_t* msg, uint16_t number, uint32_t* out);
```

- FUNC `dup_string`
  role: 调用 dup_string 完成子步骤
```c
static char* dup_string(const char* s);
```

[GUARANTEE]
```c
static bool build_request_view(const coap_message_t* req, coap_request_t* out);
```

[SPECIFICATION]
**Pre-Condition**:
- req 是已解码 method message；out 是请求视图输出对象。

**Post-Condition**:
- 成功返回 true，out 持有新分配 path/query，调用方必须 free_request_view；参数非法或任一分配失败返回 false，并清理本函数已拥有的临时字符串。

**Invariant**:
- Uri-Path segments 通过 / 拼接；无 Uri-Path 规范化为 /。
- Uri-Query segments 通过 & 拼接。
- Content-Format 和 Accept 只在 option 可成功解码时置 has_*。

**System Algorithm**:
- 若 req/out 为空或 coap_code_is_method(req->code) 为 false，返回 false。清零 out，保存 out->message=req，out->method=(coap_method_t)req->code。用 coap_message_join_options 连接 URI_PATH，分隔符为 /；连接 URI_QUERY，分隔符为 &。任一 join 失败时释放已分配 path/query 并返回 false。若 path 为空字符串，替换为新分配 "/"；替换失败时释放 query 并返回 false。若 path 不以 / 开头，分配新字符串，在前面加 /，失败时释放 path/query 并返回 false。随后读取 CONTENT_FORMAT 和 ACCEPT uint option；命中时设置 has_content_format/content_format 或 has_accept/accept。
